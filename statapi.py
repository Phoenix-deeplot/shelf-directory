"""statapi.py: anonymous, aggregate counters for the public directory: how often an entry was opened, saved to a shelf, or subscribed to. Nothing else.

    POST {"events": [{"t": "open" | "save" | "sub", "k": "item" | "shelf", "id": "..."}]}      (at most 20; the page sends each once a day per entry)
    GET                                                                                       {"items": {id: {"o", "s", "u", "h"}}, "shelves": {...}}   the busiest, for the page to sort and show

What it keeps, and what not:
  - per entry, a number for each kind of event, per day (40 days) and for ever. That is all: no cookie, no account, no address, no browser, no time of day.
  - to count one reader once a day, "has this reader done this today?" is a key made from a one-way hash of (a salt, today's date, the address, the kind, the entry). It lives for two
    days and then Redis forgets it; the hash cannot be turned back into an address, and tomorrow's hash is another one (the date is in it), so nobody can be followed from one day
    to the next. A second hash (salt, date, address) counts how many events one reader may send in a day (200): beyond that they are dropped, so one machine cannot make an entry "hot".
  - the page sends nothing when the browser says Do Not Track or Global Privacy Control, and nothing for what is not in the public directory.
The heat of an entry is its last 7 days: opens + 4 x saves + 6 x subscriptions (SCORE below); the page shows the all-time numbers next to it.

The storage is the one the sync service uses (a Redis over HTTPS: UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN, or KV_REST_API_URL + KV_REST_API_TOKEN). Without it GET says
"configured": false and the page shows no heat. Other environment (optional): ALLOWED_ORIGINS and IP_SALT (as in submission.py), STAT_DAILY_EVENTS.
Standard library only. api/stat.py is the thin Vercel wrapper; the tests run against syncapi.MemoryStore.
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

EVENTS = ('open', 'save', 'sub')
SCORE = {'open': 1, 'save': 4, 'sub': 6}
KINDS = {'item': 'i', 'shelf': 's'}
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
BODY_LIMIT = 6_000
PER_REQUEST = 20
DAILY_EVENTS = 200                   # per reader per day
WINDOW_DAYS = 7
KEEP_DAYS = 40
TOP = 400                            # entries of each kind the page is told about
CDN = 'public, max-age=60, s-maxage=600, stale-while-revalidate=3600'


def _day(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y%m%d')


def _days(now):
    return [_day(now - i * 86400) for i in range(WINDOW_DAYS)]


def _h(*parts):
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:24]


def count(store, payload, ip, env, now=None):
    """Count what a page reports. -> {'ok': True, 'counted': n}."""
    now = time.time() if now is None else now
    ev = payload.get('events') if isinstance(payload, dict) else None
    if not isinstance(ev, list) or not 0 < len(ev) <= PER_REQUEST:
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    clean = []
    for e in ev:
        if not (isinstance(e, dict) and e.get('t') in EVENTS and e.get('k') in KINDS and isinstance(e.get('id'), str) and ID_RE.match(e['id'])):
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        clean.append((e['t'], KINDS[e['k']] + ':' + e['id']))
    cfg = submission.settings(env)
    try:
        cap = max(1, min(5000, int(env.get('STAT_DAILY_EVENTS') or DAILY_EVENTS)))
    except (TypeError, ValueError):
        cap = DAILY_EVENTS
    day = _day(now)
    who = _h(cfg['salt'], day, ip)
    used = store.run([('INCRBY', 'stat:q:%s:%s' % (day, who), len(clean)), ('EXPIRE', 'stat:q:%s:%s' % (day, who), 2 * 86400)])[0]
    if used > cap:
        raise Reply(429, 'rate', '今天的统计次数用完了', 'Too many events today')
    firsts = store.run([('SET', 'stat:s:%s:%s' % (day, _h(who, t, member)), '1', 'NX', 'EX', 2 * 86400) for t, member in clean])
    new = [c for c, r in zip(clean, firsts) if r == 'OK']
    if new:
        cmds = []
        for t, member in new:
            cmds += [('HINCRBY', 'stat:d:%s:%s' % (day, t), member, 1), ('HINCRBY', 'stat:all:' + t, member, 1)]
        for t in {t for t, _ in new}:
            cmds.append(('EXPIRE', 'stat:d:%s:%s' % (day, t), KEEP_DAYS * 86400))
        store.run(cmds)
    return {'ok': True, 'counted': len(new)}


def _map(flat):
    """HGETALL answers a flat list [field, value, ...] (Redis and Upstash alike)."""
    flat = flat or []
    return {flat[i]: int(flat[i + 1]) for i in range(0, len(flat) - 1, 2)}


def read(store, now=None):
    """The busiest entries: heat of the last WINDOW_DAYS days and the all-time numbers. -> dict for the page."""
    now = time.time() if now is None else now
    days = _days(now)
    cmds = [('HGETALL', 'stat:d:%s:%s' % (d, t)) for d in days for t in EVENTS] + [('HGETALL', 'stat:all:' + t) for t in EVENTS]
    out = store.run(cmds)
    recent = {t: {} for t in EVENTS}
    for n, d in enumerate(days):
        for j, t in enumerate(EVENTS):
            for member, v in _map(out[n * len(EVENTS) + j]).items():
                recent[t][member] = recent[t].get(member, 0) + v
    total = {t: _map(out[len(days) * len(EVENTS) + j]) for j, t in enumerate(EVENTS)}
    members = set()
    for t in EVENTS:
        members |= set(recent[t]) | set(total[t])
    rows = {}
    for m in members:
        heat = sum(SCORE[t] * recent[t].get(m, 0) for t in EVENTS)
        rows[m] = {'o': total['open'].get(m, 0), 's': total['save'].get(m, 0), 'u': total['sub'].get(m, 0), 'h': heat}
    result = {'items': {}, 'shelves': {}}
    for kind, prefix in (('items', 'i:'), ('shelves', 's:')):
        mine = sorted(((m[2:], r) for m, r in rows.items() if m.startswith(prefix)), key=lambda x: (-x[1]['h'], -(x[1]['o'] + 4 * x[1]['s'] + 6 * x[1]['u']), x[0]))[:TOP]
        result[kind] = {i: r for i, r in mine}
    return result


def respond(h, env, method, store=None):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/stat.py and by the tests): a page of another site is refused, as in the submit function."""
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
        if store is None:
            return send(200, {'ok': True, 'service': 'shelf-stat', 'configured': False})
        try:
            return send(200, {'ok': True, 'service': 'shelf-stat', 'configured': True, 'window': WINDOW_DAYS, **read(store)}, {'Cache-Control': CDN})
        except StoreError:
            return send(503, {'ok': False, 'error': 'storage', 'message': '统计服务暂时不可用', 'message_en': 'The counters are unavailable'})
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
            raise Reply(503, 'not_configured', '统计服务还没有开通', 'Counters are not set up on this site')
        return send(200, count(store, payload, ip, env))
    except Reply as r:
        return send(r.status, r.body())
    except StoreError:
        return send(503, {'ok': False, 'error': 'storage', 'message': '统计服务暂时不可用', 'message_en': 'The counters are unavailable'})
