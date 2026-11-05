"""feedbackapi.py: anonymous feedback from the page: a few words from a reader to the maintainer, nothing about who.

    POST {"msg": "…", "kind": "bug" | "idea" | "content" | "other", "contact": "…" (optional), "where": "page name", "lang": "zh" | "en", "ver": "page version", "website": ""}
        -> {"ok": true}                                   (a honeypot that is filled in, or the same words twice in a day, is answered the same way and kept once or not at all)
    GET -> {"ok": true, "service": "shelf-feedback", "configured": bool}   (the page shows its feedback entry only when this is true)

What it keeps, and what not:
  - the words (at most 1000 characters), the kind, the contact that the reader chose to write (at most 80 characters, optional), the name of the page the reader was on, the language, the page version and the DAY (not the
    time of day). No address, no browser, no cookie, no account.
  - to let one reader send only a few a day, "how many today?" is a counter under a key made from a one-way hash of (a salt, today's date, the address); it lives for two days and then Redis forgets it. A second key (a hash of
    the day and the words) lets the same words count once a day.
  - the newest FEEDBACK_KEEP (default 2000) are kept, older ones go; nothing is kept longer than 180 days.
The maintainer reads and deletes them in the admin page (adminapi.py: the actions feedback_list and feedback_delete, behind the same password session as the rest of that page).

Storage: the Redis of the sync service (UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN, or KV_REST_API_URL + KV_REST_API_TOKEN). Without it GET says "configured": false and POST answers 503.
Other environment (optional): ALLOWED_ORIGINS and IP_SALT (as in submission.py), FEEDBACK_PER_DAY (3 by default, 1 to 50), FEEDBACK_KEEP (2000 by default, 50 to 20000).
Standard library only. api/feedback.py is the thin Vercel wrapper; the tests run against syncapi.MemoryStore.
"""
import datetime
import hashlib
import json
import re
import time

import submission
import syncapi
from submission import Reply
from syncapi import StoreError

KINDS = ('bug', 'idea', 'content', 'other')
MSG_MAX = 1000
CONTACT_MAX = 80
BODY_LIMIT = 6_000
PER_DAY = 3
KEEP = 2000
KEEP_DAYS = 180
LIST_MAX = 100                       # at most this many in one answer of the admin list
WHERE_RE = re.compile(r'^[A-Za-z0-9_./#-]{0,24}$')
VER_RE = re.compile(r'^[A-Za-z0-9._-]{0,20}$')
ITEMS, COUNTER, LOW = 'fb:items', 'fb:n', 'fb:lo'
_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _day(now):
    return (datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=now)).strftime('%Y%m%d')          # (also for a moment before 1970, which fromtimestamp refuses on Windows)


def _h(*parts):
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:24]


def _num(env, name, default, low, top):
    try:
        return max(low, min(top, int(env.get(name) or default)))
    except (TypeError, ValueError):
        return default


def _text(v, limit):
    """A reader's words: control characters out, ends trimmed, at most `limit` characters. -> '' when it is not text."""
    if not isinstance(v, str):
        return ''
    return _CTRL.sub('', v).replace('\r\n', '\n').replace('\r', '\n').strip()[:limit]


def purge(store, now, env=None):
    """Forget what is older than KEEP_DAYS (from the oldest id on, a few at a time) and what is past the newest FEEDBACK_KEEP."""
    keep = _num(env or {}, 'FEEDBACK_KEEP', KEEP, 50, 20000)
    n, lo = store.run([('GET', COUNTER), ('GET', LOW)])
    n, lo = int(n or 0), max(1, int(lo or 1))
    cutoff = _day(now - KEEP_DAYS * 86400)
    new, dead = lo, []
    while new <= n and len(dead) < 40:
        raw = store.run([('HGET', ITEMS, str(new))])[0]
        if raw is None:                                           # (already deleted)
            new += 1
            continue
        try:
            old = json.loads(raw).get('d', '') < cutoff
        except (ValueError, AttributeError):
            old = True
        if not old and n - new < keep:
            break
        dead.append(str(new))
        new += 1
    cmds = []
    if dead:
        cmds.append(('HDEL', ITEMS) + tuple(dead))
    if new != lo:
        cmds.append(('SET', LOW, str(new)))
    if cmds:
        store.run(cmds)


def submit(store, payload, ip, env, now=None):
    """Keep one piece of feedback. -> {'ok': True}. Raises Reply (400 request, 429 rate)."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    if payload.get('website'):                                    # the honeypot: a person never fills it in; a program is told that all is well
        return {'ok': True}
    msg = _text(payload.get('msg'), MSG_MAX + 1)
    kind = payload.get('kind') if payload.get('kind') in KINDS else ('other' if payload.get('kind') in (None, '') else None)
    contact = _text(payload.get('contact'), CONTACT_MAX + 1)
    where = payload.get('where') if isinstance(payload.get('where'), str) else ''
    ver = payload.get('ver') if isinstance(payload.get('ver'), str) else ''
    lang = payload.get('lang') if payload.get('lang') in ('zh', 'en') else 'zh'
    if not msg or len(msg) > MSG_MAX or kind is None or len(contact) > CONTACT_MAX or not WHERE_RE.match(where) or not VER_RE.match(ver):
        raise Reply(400, 'request', '请求格式不对（内容不能为空，最多 1000 字）', 'Bad request (write something; at most 1000 characters)')
    cfg = submission.settings(env)
    cap = _num(env, 'FEEDBACK_PER_DAY', PER_DAY, 1, 50)
    day = _day(now)
    who = _h(cfg['salt'], day, ip)
    used = store.run([('INCR', 'fb:q:%s:%s' % (day, who)), ('EXPIRE', 'fb:q:%s:%s' % (day, who), 2 * 86400)])[0]
    if used > cap:
        raise Reply(429, 'rate', '今天发得够多了，明天再来', 'That is enough for today; come back tomorrow')
    first = store.run([('SET', 'fb:dup:%s:%s' % (day, _h(day, msg)), '1', 'NX', 'EX', 2 * 86400)])[0]
    if first != 'OK':                                             # the same words today: kept once
        return {'ok': True}
    n = int(store.run([('INCR', COUNTER)])[0])
    entry = {'id': n, 'd': day, 'k': kind, 'm': msg, 'c': contact, 'w': where, 'l': lang, 'v': ver}
    store.run([('HSET', ITEMS, str(n), json.dumps(entry, ensure_ascii=False, separators=(',', ':')))])
    purge(store, now, env)
    return {'ok': True}


def list_items(store, before=None, limit=50, now=None, env=None):
    """For the admin page: the newest first, `limit` of them, those with an id below `before`. -> {'total', 'items': [...], 'next': id to ask for the next page, or None}."""
    now = time.time() if now is None else now
    try:
        limit = max(1, min(LIST_MAX, int(limit)))
    except (TypeError, ValueError):
        limit = 50
    purge(store, now, env)
    n, total, lo = store.run([('GET', COUNTER), ('HLEN', ITEMS), ('GET', LOW)])
    n, total, lo = int(n or 0), int(total or 0), max(1, int(lo or 1))
    try:
        top = min(n, int(before) - 1) if before not in (None, '') else n
    except (TypeError, ValueError):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    items, i = [], top
    while i >= lo and len(items) < limit and top - i < 4 * LIST_MAX:          # (ids below `lo` are gone; a long run of deleted ids is not walked for ever: the next page goes on from where this one stopped)
        ids = list(range(i, max(lo - 1, i - 50), -1))
        for raw in store.run([('HGET', ITEMS, str(x)) for x in ids]):
            if raw is None:
                continue
            try:
                items.append(json.loads(raw))
            except ValueError:
                continue
        i -= len(ids)
    items = items[:limit]
    cursor = items[-1]['id'] if len(items) == limit else (i + 1 if i >= lo else None)
    return {'total': total, 'items': items, 'next': cursor if cursor is not None and cursor > lo else None}


def delete_items(store, ids):
    """Delete these ids (at most 50). -> how many were there."""
    if not isinstance(ids, list) or not 0 < len(ids) <= 50 or not all(isinstance(x, int) and not isinstance(x, bool) and 0 < x < 10 ** 9 for x in ids):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    return int(store.run([('HDEL', ITEMS) + tuple(str(x) for x in sorted(set(ids)))])[0] or 0)


def respond(h, env, method, store=None):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/feedback.py and by the tests): a page of another site is refused, as in the submit function."""
    store = store if store is not None else syncapi.store_from_env(env)
    cfg = submission.settings(env)
    origin = h.headers.get('Origin') or ''
    cors = submission.cors_headers(origin, cfg)

    def send(status, payload, extra=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else b''
        h.send_response(status)
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
        return send(200, {'ok': True, 'service': 'shelf-feedback', 'configured': store is not None})
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        length = -1
    if length < 0 or length > BODY_LIMIT:
        try:
            h.rfile.read(max(0, min(length, 2 * BODY_LIMIT)))
        except OSError:
            pass
        return send(413, {'ok': False, 'error': 'size', 'message': '内容太大', 'message_en': 'Too large'})
    try:
        payload = json.loads(h.rfile.read(length).decode('utf-8')) if length else None
    except ValueError:
        payload = None
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    try:
        if store is None:
            raise Reply(503, 'not_configured', '反馈服务还没有开通', 'Feedback is not set up on this site')
        return send(200, submit(store, payload, ip, env))
    except Reply as r:
        return send(r.status, r.body())
    except StoreError:
        return send(503, {'ok': False, 'error': 'storage', 'message': '反馈服务暂时不可用', 'message_en': 'Feedback is unavailable for the moment'})
