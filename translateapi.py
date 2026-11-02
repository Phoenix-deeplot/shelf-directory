"""translateapi.py: the function behind the reading page's "translate" button (api/translate.py is only the Vercel wrapper).

    POST /api/translate  {"to": "zh" | "en", "from": "auto" | "en" | ..., "texts": ["a paragraph", ...]}
        -> {"ok": true, "provider": "deepl" | "aliyun" | "cache", "texts": [...], "cached": n, "left": {"day": characters}}
        -> {"ok": false, "error": "request" | "toolong" | "limit" | "quota" | "nokey" | "fail" | "origin", "message": "...", "message_en": "..."}
    GET  /api/translate  -> {"ok": true, "service": "shelf-translate", "configured": bool, "providers": [...], "day_chars": n}

Who pays: the owner's DeepL / Aliyun allowance, so it is guarded: only the pages of ALLOWED_ORIGINS may ask; a text of at most MAX_TEXT characters, at most MAX_TEXTS of them and MAX_TOTAL in all
in one request; every reader (a salted hash of the address, as in the other services) gets TRANSLATE_DAY_CHARS characters a day and the whole site TRANSLATE_MONTH_CHARS a month (both counted
in the same Redis as the sync and the counters: WITHOUT that storage the function does not translate at all, since it could not count). What was translated once is kept for CACHE_DAYS
(a hash of the text and the language is the key), costs nothing again and is not counted.
Nothing about a reader is kept and the text itself is not logged; the providers see the text, which the privacy page says.
The keys come from the environment (DEEPL_API_KEY, ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET; TRANSLATE_PROVIDER=aliyun to try Aliyun first)."""
import datetime
import hashlib
import json
import re
import time

import submission
import syncapi
import translate
from submission import Reply
from syncapi import StoreError

MAX_TEXTS = 50
MAX_TEXT = 1500
MAX_TOTAL = 8000
BODY_LIMIT = 64_000
DAY_CHARS = 30_000                   # per reader per day (TRANSLATE_DAY_CHARS)
MONTH_CHARS = 800_000                # for the whole site per month (TRANSLATE_MONTH_CHARS): below what the free tiers of both providers give
CACHE_DAYS = 90
_CTRL = re.compile(r'[\x00-\x1f\x7f]')
_LANG = set(translate.LANGS)


def _h(*parts):
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:32]


def _day(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y%m%d')


def _month(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y%m')


def _cap(env, name, default, top):
    try:
        return max(1000, min(top, int(str(env.get(name) or '').strip())))
    except ValueError:
        return default


def status(store, env):
    return {'ok': True, 'service': 'shelf-translate', 'configured': bool(store is not None and translate.configured(env)), 'providers': translate.configured(env) if store is not None else [],
            'day_chars': _cap(env, 'TRANSLATE_DAY_CHARS', DAY_CHARS, 500_000)}


def clean_texts(payload):
    """-> (to, from, [texts]) checked, the texts with their white space folded. Raises Reply."""
    if not isinstance(payload, dict):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    to, src, texts = payload.get('to'), payload.get('from') or 'auto', payload.get('texts')
    if to not in ('zh', 'en') or src not in _LANG or src == to or not isinstance(texts, list) or not 0 < len(texts) <= MAX_TEXTS or not all(isinstance(t, str) for t in texts):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    out = [re.sub(r'\s+', ' ', _CTRL.sub(' ', t)).strip() for t in texts]
    if any(len(t) > MAX_TEXT for t in out) or sum(len(t) for t in out) > MAX_TOTAL:
        raise Reply(413, 'toolong', '一次翻译的文字太多，请分段', 'Too much text in one request')
    return to, src, out


def translate_request(store, payload, ip, env, now=None, translator=None):
    """Translate what a page sends. -> the answer dict. Raises Reply."""
    now = time.time() if now is None else now
    to, src, texts = clean_texts(payload)
    if store is None or not translate.configured(env):
        raise Reply(503, 'nokey', '翻译服务还没有开通', 'Translation is not set up on this site')
    cfg = submission.settings(env)
    keys = ['tr:c:' + _h(to, t) for t in texts]
    got = store.run([('GET', k) for k in keys])
    todo = [i for i, t in enumerate(texts) if t and got[i] is None]
    out = [(g if g is not None else '') for g in got]
    provider, left = 'cache', None
    day, month = _day(now), _month(now)
    qk, mk = 'tr:q:%s:%s' % (day, _h(cfg['salt'], day, ip)), 'tr:m:' + month
    day_cap, month_cap = _cap(env, 'TRANSLATE_DAY_CHARS', DAY_CHARS, 500_000), _cap(env, 'TRANSLATE_MONTH_CHARS', MONTH_CHARS, 50_000_000)
    if todo:
        need = sum(len(texts[i]) for i in todo)
        r = store.run([('INCRBY', qk, need), ('EXPIRE', qk, 2 * 86400), ('INCRBY', mk, need), ('EXPIRE', mk, 40 * 86400)])
        used, month_used = r[0], r[2]

        def give_back():
            store.run([('INCRBY', qk, -need), ('INCRBY', mk, -need)])
        if used > day_cap:
            give_back()
            raise Reply(429, 'limit', '你今天的翻译字数用完了（每人每天 %d 字），明天再来' % day_cap, 'You have used today\'s translation allowance (%d characters a day)' % day_cap)
        if month_used > month_cap:
            give_back()
            raise Reply(429, 'quota', '本站这个月的翻译额度用完了', 'This site\'s translation allowance for the month is used up')
        try:
            provider, done = (translator or translate.translate_texts)([texts[i] for i in todo], to, src, env)
        except translate.Unavailable as e:
            give_back()
            if e.code == 'quota':
                raise Reply(429, 'quota', '翻译服务的额度用完了', 'The translation service has no allowance left')
            raise Reply(502, 'fail', '翻译服务暂时不可用', 'The translation service is unavailable')
        if len(done) != len(todo):
            give_back()
            raise Reply(502, 'fail', '翻译服务暂时不可用', 'The translation service is unavailable')
        for i, x in zip(todo, done):
            out[i] = x
        store.run([('SET', keys[i], x, 'EX', CACHE_DAYS * 86400) for i, x in zip(todo, done) if x])
        left = max(0, day_cap - used)
    if left is None:
        used_now = store.run([('GET', qk)])[0]
        left = max(0, day_cap - int(used_now or 0))
    return {'ok': True, 'provider': provider, 'texts': out, 'cached': len(texts) - len(todo), 'left': {'day': left}}


def respond(h, env, method, store=None, translator=None):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/translate.py and by the tests): a page of another site is refused, as in the other functions."""
    store = store if store is not None else syncapi.store_from_env(env)
    cfg = submission.settings(env)
    origin = h.headers.get('Origin') or ''
    cors = submission.cors_headers(origin, cfg)

    def send(code, payload, extra=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else b''
        h.send_response(code)
        h.send_header('Content-Type', 'application/json; charset=utf-8')
        h.send_header('Vary', 'Origin')
        for k, v in {'Cache-Control': 'no-store', **cors, **(extra or {})}.items():
            h.send_header(k, v)
        h.send_header('Content-Length', str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    if method == 'OPTIONS':
        return send(204, None, {'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400'} if cors else {})
    if origin and not cors:
        return send(403, {'ok': False, 'error': 'origin', 'message': '这个来源不允许', 'message_en': 'This origin is not allowed'})
    if method == 'GET':
        return send(200, status(store, env), {'Cache-Control': 'public, max-age=60, s-maxage=300'})
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        length = -1
    if length < 0 or length > BODY_LIMIT:
        try:
            h.rfile.read(max(0, min(length, 2 * BODY_LIMIT)))
        except OSError:
            pass
        return send(413, {'ok': False, 'error': 'toolong', 'message': '一次翻译的文字太多，请分段', 'message_en': 'Too much text in one request'})
    try:
        payload = json.loads(h.rfile.read(length).decode('utf-8')) if length else None
    except ValueError:
        payload = None
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    try:
        return send(200, translate_request(store, payload, ip, env, translator=translator))
    except Reply as r:
        return send(r.status, r.body())
    except StoreError:
        return send(503, {'ok': False, 'error': 'storage', 'message': '翻译服务暂时不可用', 'message_en': 'Translation is unavailable'})
