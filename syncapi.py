"""syncapi.py: the optional "sync code": the same shelf on several devices, without accounts.

A reader of the site can turn sync on in their browser. The browser makes a long random code, derives three things from it (an id, a secret to prove it is the same
reader, and an encryption key that never leaves the browser), and from then on sends this function only encrypted things:

    {"action": "pull",     "id", "auth", "rev"}                    the encrypted list of the shelf (or "unchanged"), which stored texts there are, how much room is used
    {"action": "push",     "id", "auth", "base_rev", "blob"}       replace the list, but only if nobody else did meanwhile (compare-and-set on the revision)
    {"action": "body_put", "id", "auth", "key", "blob"}            keep one encrypted (and compressed) text under a name only the browser can make
    {"action": "body_get", "id", "auth", "key"}   {"action": "body_del", "id", "auth", "keys": [...]}   {"action": "destroy", "id", "auth"}
    {"action": "poll",     "answer": "yes" | "no" | "later"}       one anonymous count: would you pay for large-file sync? (nothing else is kept with it)

What is kept here is opaque: the browser encrypts (AES-GCM) before sending, so neither the titles nor the texts can be read here, and the secret is kept only as a hash.
Nobody is asked for a name, an email or a password: whoever has the code is the reader, and whoever loses it loses the data (nothing here can recover it).
It is small on purpose (a free vault holds QUOTA characters, one text at most ITEM_MAX) and forgets what is not used for TTL_DAYS days.

The storage is a Redis reachable over HTTPS (Upstash, e.g. from the Vercel marketplace): UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN (or Vercel KV's KV_REST_API_URL +
KV_REST_API_TOKEN). Without them GET says "configured": false and the page shows no sync. Other environment (all optional): ALLOWED_ORIGINS, IP_SALT (as in submission.py),
SYNC_QUOTA (characters per vault), SYNC_TTL_DAYS, SYNC_MAX_KEYS (no new vault when the database holds more keys than that).

Standard library only. api/sync.py is the thin Vercel wrapper; the tests run `handle` / `respond` against MemoryStore and against a fake Upstash.
"""
import base64
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.error
import urllib.request

import submission
from submission import Reply

BODY_LIMIT = 1_400_000               # one request: base64 of at most ~1 MB
INDEX_MAX = 1_000_000                # the encrypted list of a shelf (base64 characters)
ITEM_MAX = 700_000                   # one encrypted text (base64 characters, about 520 KB)
QUOTA = 12_000_000                   # everything one free vault keeps (base64 characters, about 9 MB of ciphertext)
MAX_BODIES = 600
TTL_DAYS = 180
MAX_KEYS = 6000                      # about 1000 vaults: then no new one is made (the owner sees it on the database's own page and raises the limit)
LIMITS = {'pull': (60, 60), 'push': (30, 60), 'body_put': (120, 60), 'body_get': (120, 60), 'body_del': (30, 60), 'destroy': (5, 3600)}     # (requests, seconds) per sender
CREATE_LIMIT = (3, 86400)            # new vaults per sender per day
POLL_LIMIT = (1, 86400)
ID_RE = re.compile(r'^[0-9a-f]{32}$')
AUTH_RE = re.compile(r'^[A-Za-z0-9_-]{32,64}$')
KEY_RE = re.compile(r'^[0-9a-f]{32}$')
B64_RE = re.compile(r'^[A-Za-z0-9+/=]*$')

CAS_SCRIPT = """local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
if cur ~= tonumber(ARGV[1]) then return {0, cur} end
redis.call('SET', KEYS[1], tostring(cur + 1), 'EX', ARGV[3])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return {1, cur + 1}"""


class StoreError(Exception):
    pass


# ======================================================================= storage
class MemoryStore:
    """The few Redis commands this function uses, in memory (the tests, and a local run). Atomic, like Redis."""

    def __init__(self, clock=time.time):
        self.d, self.exp, self.lock, self.clock = {}, {}, threading.RLock(), clock

    def _live(self, k):
        e = self.exp.get(k)
        if e is not None and e <= self.clock():
            self.d.pop(k, None)
            self.exp.pop(k, None)
        return k in self.d

    def run(self, commands):
        with self.lock:
            return [self._one(c) for c in commands]

    def _one(self, c):
        op, a = str(c[0]).upper(), [str(x) for x in c[1:]]
        if op == 'GET':
            return self.d[a[0]] if self._live(a[0]) and isinstance(self.d[a[0]], str) else None
        if op == 'SET':
            k, v, rest = a[0], a[1], [x.upper() for x in a[2:]]
            if 'NX' in rest and self._live(k):
                return None
            self.d[k] = v
            self.exp.pop(k, None)
            if 'EX' in rest:
                self.exp[k] = self.clock() + int(a[2 + rest.index('EX') + 1])
            return 'OK'
        if op == 'DEL':
            n = 0
            for k in a:
                if self._live(k):
                    n += 1
                self.d.pop(k, None)
                self.exp.pop(k, None)
            return n
        if op in ('INCR', 'INCRBY'):
            k, by = a[0], (1 if op == 'INCR' else int(a[1]))
            cur = int(self.d[k]) if self._live(k) else 0
            self.d[k] = str(cur + by)
            return cur + by
        if op == 'STRLEN':
            return len(self.d[a[0]]) if self._live(a[0]) else 0
        if op == 'EXPIRE':
            if self._live(a[0]):
                self.exp[a[0]] = self.clock() + int(a[1])
                return 1
            return 0
        if op == 'HSET':
            if not self._live(a[0]):
                self.d[a[0]] = {}
            h = self.d[a[0]]
            new = a[1] not in h
            h[a[1]] = a[2]
            return 1 if new else 0
        if op == 'HGET':
            return self.d[a[0]].get(a[1]) if self._live(a[0]) else None
        if op == 'HINCRBY':
            if not self._live(a[0]):
                self.d[a[0]] = {}
            h = self.d[a[0]]
            h[a[1]] = str(int(h.get(a[1], '0')) + int(a[2]))
            return int(h[a[1]])
        if op == 'HGETALL':                                              # flat, as Redis answers over the wire: [field, value, field, value ...]
            if not self._live(a[0]):
                return []
            return [x for kv in self.d[a[0]].items() for x in kv]
        if op == 'HSTRLEN':
            return len(self.d[a[0]].get(a[1], '')) if self._live(a[0]) else 0
        if op == 'HLEN':
            return len(self.d[a[0]]) if self._live(a[0]) else 0
        if op == 'HKEYS':
            return list(self.d[a[0]].keys()) if self._live(a[0]) else []
        if op == 'HDEL':
            if not self._live(a[0]):
                return 0
            n = 0
            for f in a[1:]:
                n += 1 if self.d[a[0]].pop(f, None) is not None else 0
            if not self.d[a[0]]:
                self.d.pop(a[0], None)
            return n
        if op == 'DBSIZE':
            return len([k for k in list(self.d) if self._live(k)])
        if op == 'EVAL':
            if a[0] != CAS_SCRIPT or a[1] != '2':
                raise StoreError('this store runs only the one script of syncapi.py')
            rk, ik, expect, value, ex = a[2], a[3], int(a[4]), a[5], int(a[6])
            cur = int(self.d[rk]) if self._live(rk) else 0
            if cur != expect:
                return [0, cur]
            self.d[rk], self.exp[rk] = str(cur + 1), self.clock() + ex
            self.d[ik], self.exp[ik] = value, self.clock() + ex
            return [1, cur + 1]
        raise StoreError('unknown command ' + op)


class UpstashStore:
    """The same commands over Upstash's REST pipeline (https://upstash.com/docs/redis/features/restapi)."""

    def __init__(self, url, token, timeout=8):
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout

    def run(self, commands):
        body = json.dumps([[str(x) for x in c] for c in commands]).encode('utf-8')
        req = urllib.request.Request(self.url + '/pipeline', data=body, method='POST', headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read().decode('utf-8'))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise StoreError('storage unreachable: %s' % type(e).__name__)
        if not isinstance(out, list) or len(out) != len(commands):
            raise StoreError('storage answered oddly')
        res = []
        for o in out:
            if not isinstance(o, dict) or o.get('error'):
                raise StoreError('storage refused: %s' % (o.get('error') if isinstance(o, dict) else o))
            res.append(o.get('result'))
        return res


def store_from_env(env):
    url = str(env.get('UPSTASH_REDIS_REST_URL') or env.get('KV_REST_API_URL') or '').strip()
    token = str(env.get('UPSTASH_REDIS_REST_TOKEN') or env.get('KV_REST_API_TOKEN') or '').strip()
    return UpstashStore(url, token) if url.startswith('https://') and token else None


# ======================================================================= the service
_LOCAL = {}                                                  # soft limits when the store cannot count (never used with a store)


def _cfg(env):
    def num(name, default):
        try:
            return max(1, int(env.get(name) or default))
        except (TypeError, ValueError):
            return default
    return {'quota': num('SYNC_QUOTA', QUOTA), 'ttl': num('SYNC_TTL_DAYS', TTL_DAYS) * 86400, 'max_keys': num('SYNC_MAX_KEYS', MAX_KEYS)}


def _keys(vid):
    return {n: 'v:%s:%s' % (vid, n) for n in 'arinb'}         # a: hash of the secret, r: revision, i: the list, n: characters in the texts, b: the texts


def _check_id(payload):
    vid, auth = payload.get('id'), payload.get('auth')
    if not isinstance(vid, str) or not ID_RE.match(vid) or not isinstance(auth, str) or not AUTH_RE.match(auth):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    return vid, hashlib.sha256(auth.encode('ascii')).hexdigest()


def _blob(v, limit):
    if not isinstance(v, str) or not v or not B64_RE.match(v):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    if len(v) > limit:
        raise Reply(413, 'size', '内容太大', 'Too large')
    return v


def _hit(store, name, who, limit, now):
    n, window = limit
    k = 'rl:%s:%s:%d' % (name, who, int(now // window))
    count, _ = store.run([('INCR', k), ('EXPIRE', k, window * 2)])
    if count > n:
        raise Reply(429, 'rate', '请求太频繁了，请稍等几分钟', 'Too many requests; wait a few minutes')


def _touch(vid, ttl):
    return [('EXPIRE', k, ttl) for k in _keys(vid).values()]


def _auth(store, vid, ah):
    """The vault must exist and the secret must be the one it was made with."""
    got = store.run([('GET', _keys(vid)['a'])])[0]
    if got is None:
        raise Reply(404, 'novault', '同步空间不存在（可能已过期）', 'No such vault (it may have expired)')
    if not hmac.compare_digest(got, ah):
        raise Reply(403, 'auth', '同步码不对', 'Wrong sync code')


def _used(store, vid):
    k = _keys(vid)
    n, i = store.run([('GET', k['n']), ('STRLEN', k['i'])])
    return int(n or 0) + int(i or 0)


def _expires(now, ttl):
    return int(now + ttl)


def pull(store, p, who, now, cfg):
    vid, ah = _check_id(p)
    _hit(store, 'pull', who, LIMITS['pull'], now)
    k = _keys(vid)
    a, r, n, il, bodies = store.run([('GET', k['a']), ('GET', k['r']), ('GET', k['n']), ('STRLEN', k['i']), ('HKEYS', k['b'])])
    if a is None:
        return {'ok': True, 'exists': False}
    if not hmac.compare_digest(a, ah):
        raise Reply(403, 'auth', '同步码不对', 'Wrong sync code')
    cur = int(r or 0)
    out = {'ok': True, 'exists': True, 'rev': cur, 'bodies': bodies, 'quota': {'used': int(n or 0) + int(il or 0), 'limit': cfg['quota']}, 'expires': _expires(now, cfg['ttl'])}
    cmds = _touch(vid, cfg['ttl'])
    if isinstance(p.get('rev'), int) and not isinstance(p['rev'], bool) and p['rev'] == cur:
        out['unchanged'] = True
    else:
        out['blob'] = store.run([('GET', k['i'])] + cmds)[0] or ''
        return out
    store.run(cmds)
    return out


def push(store, p, who, now, cfg):
    vid, ah = _check_id(p)
    blob = _blob(p.get('blob'), INDEX_MAX)
    base = p.get('base_rev')
    if not isinstance(base, int) or isinstance(base, bool) or base < 0:
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    _hit(store, 'push', who, LIMITS['push'], now)
    k = _keys(vid)
    existing = store.run([('GET', k['a'])])[0]
    if existing is None:
        if base != 0:
            raise Reply(404, 'novault', '同步空间不存在（可能已过期）', 'No such vault (it may have expired)')
        _hit(store, 'create', who, CREATE_LIMIT, now)
        if store.run([('DBSIZE',)])[0] > cfg['max_keys']:
            raise Reply(503, 'full', '同步服务暂时满了，请稍后再试', 'The sync service is full for now; try again later')
        if store.run([('SET', k['a'], ah, 'EX', cfg['ttl'], 'NX')])[0] is None:            # somebody made it a moment ago
            existing = store.run([('GET', k['a'])])[0]
    if existing is not None and not hmac.compare_digest(existing, ah):
        raise Reply(403, 'auth', '同步码不对', 'Wrong sync code')
    n = int(store.run([('GET', k['n'])])[0] or 0)
    if n + len(blob) > cfg['quota']:
        raise Reply(413, 'quota', '同步空间已满', 'The sync space is full')
    ok, rev = store.run([('EVAL', CAS_SCRIPT, 2, k['r'], k['i'], base, blob, cfg['ttl'])])[0]
    if not ok:
        err = Reply(409, 'conflict', '另一台设备刚刚同步过', 'Another device synced a moment ago')
        err.extra = {'rev': int(rev)}
        raise err
    store.run(_touch(vid, cfg['ttl']))
    return {'ok': True, 'rev': int(rev), 'expires': _expires(now, cfg['ttl'])}


def body_put(store, p, who, now, cfg):
    vid, ah = _check_id(p)
    key = p.get('key')
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    blob = _blob(p.get('blob'), ITEM_MAX)
    _hit(store, 'body_put', who, LIMITS['body_put'], now)
    _auth(store, vid, ah)
    k = _keys(vid)
    old, count, n, il = store.run([('HSTRLEN', k['b'], key), ('HLEN', k['b']), ('GET', k['n']), ('STRLEN', k['i'])])
    if not old and count >= MAX_BODIES:
        raise Reply(413, 'quota', '同步空间里的文件太多了', 'Too many texts in the sync space')
    if int(n or 0) - old + len(blob) + int(il or 0) > cfg['quota']:
        raise Reply(413, 'quota', '同步空间已满', 'The sync space is full')
    store.run([('HSET', k['b'], key, blob), ('INCRBY', k['n'], len(blob) - old)] + _touch(vid, cfg['ttl']))
    return {'ok': True, 'quota': {'used': int(n or 0) - old + len(blob) + int(il or 0), 'limit': cfg['quota']}}


def body_get(store, p, who, now, cfg):
    vid, ah = _check_id(p)
    key = p.get('key')
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    _hit(store, 'body_get', who, LIMITS['body_get'], now)
    _auth(store, vid, ah)
    blob = store.run([('HGET', _keys(vid)['b'], key)])[0]
    if blob is None:
        raise Reply(404, 'nobody', '这份内容不在同步空间里', 'That text is not in the sync space')
    return {'ok': True, 'blob': blob}


def body_del(store, p, who, now, cfg):
    vid, ah = _check_id(p)
    keys = p.get('keys')
    if not isinstance(keys, list) or not keys or len(keys) > 100 or not all(isinstance(x, str) and KEY_RE.match(x) for x in keys):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    _hit(store, 'body_del', who, LIMITS['body_del'], now)
    _auth(store, vid, ah)
    k = _keys(vid)
    sizes = store.run([('HSTRLEN', k['b'], x) for x in keys])
    store.run([('HDEL', k['b']) + tuple(keys), ('INCRBY', k['n'], -sum(sizes))])
    return {'ok': True, 'removed': len([s for s in sizes if s])}


def destroy(store, p, who, now, cfg):
    vid, ah = _check_id(p)
    _hit(store, 'destroy', who, LIMITS['destroy'], now)
    _auth(store, vid, ah)
    store.run([('DEL',) + tuple(_keys(vid).values())])
    return {'ok': True}


def poll(store, p, who, now, cfg):
    ans = p.get('answer')
    if ans not in ('yes', 'no', 'later'):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    _hit(store, 'poll', who, POLL_LIMIT, now)
    store.run([('INCR', 'poll:' + ans)])
    return {'ok': True}


ACTIONS = {'pull': pull, 'push': push, 'body_put': body_put, 'body_get': body_get, 'body_del': body_del, 'destroy': destroy, 'poll': poll}


def poll_counts(store):
    try:
        yes, no, later = store.run([('GET', 'poll:yes'), ('GET', 'poll:no'), ('GET', 'poll:later')])
        return {'yes': int(yes or 0), 'no': int(no or 0), 'later': int(later or 0)}
    except (StoreError, ValueError):
        return None


def handle(payload, ip, env, store, now=None):
    """-> (HTTP status, JSON-able dict)."""
    now = now if now is not None else time.time()
    try:
        if store is None:
            raise Reply(503, 'not_configured', '同步服务还没有开通', 'Sync is not set up on this site')
        if not isinstance(payload, dict) or payload.get('action') not in ACTIONS:
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        who = submission.ip_hash(ip, submission.settings(env)['salt'])
        return 200, ACTIONS[payload['action']](store, payload, who, now, _cfg(env))
    except Reply as r:
        body = r.body()
        body.update(getattr(r, 'extra', {}))
        return r.status, body
    except StoreError:
        return 503, {'ok': False, 'error': 'storage', 'message': '同步服务暂时不可用，请稍后再试', 'message_en': 'The sync service is unavailable; try again later'}


# ---------------------------------------------------------------- the HTTP side (used by api/sync.py and by the tests)
def respond(h, env, method, store=None):
    """Answer one request on a BaseHTTPRequestHandler `h`: the same origin rule as the submit function (a page of another site is refused)."""
    store = store if store is not None else store_from_env(env)
    cfg = submission.settings(env)
    origin = h.headers.get('Origin') or ''
    cors = submission.cors_headers(origin, cfg)

    def send(status, payload, extra=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else b''
        h.send_response(status)
        h.send_header('Content-Type', 'application/json; charset=utf-8')
        h.send_header('Cache-Control', 'no-store')
        for k, v in {**cors, **(extra or {})}.items():
            h.send_header(k, v)
        h.send_header('Content-Length', str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    if method == 'OPTIONS':
        return send(204, None, {'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400'} if cors else {})
    if method == 'GET':
        return send(200, {'ok': True, 'service': 'shelf-sync', 'configured': store is not None, 'poll': poll_counts(store) if store is not None else None})
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        length = -1

    def drain():
        try:
            h.rfile.read(max(0, min(length, 2 * BODY_LIMIT)))
        except OSError:
            pass

    if origin and not cors:
        drain()
        return send(403, {'ok': False, 'error': 'origin', 'message': '这个来源不允许', 'message_en': 'This origin is not allowed'})
    if length < 0 or length > BODY_LIMIT:
        drain()
        return send(413, {'ok': False, 'error': 'size', 'message': '内容太大', 'message_en': 'Too large'})
    try:
        payload = json.loads(h.rfile.read(length).decode('utf-8')) if length else None
    except ValueError:
        payload = None
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    status, body = handle(payload, ip, env, store)
    return send(status, body)
