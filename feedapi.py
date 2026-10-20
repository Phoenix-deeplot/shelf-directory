"""feedapi.py: the update service for what readers subscribe to. It stores nothing.

A reader of the site subscribes to the blogs and article feeds they want to follow; that list stays in the reader's own browser (a collection per feed).
A page in a browser cannot read someone else's feed (the other site does not allow it), so the page asks this function instead:

    {"action": "discover", "url": "…"}                              what is this address?  -> a feed (name, latest items), or a single page (title, summary)
    {"action": "update", "feeds": [{"url", "etag", "modified"}]}    what is new in these feeds?  -> per feed: items, "unchanged", or why not

No database, no GitHub token, no list of subscriptions is kept here: the addresses are read while the request lasts, and what comes back is titles, summaries,
links and dates, never a page's own text. The reading is the directory's own bounded reading (aggregate.py: https only, public addresses only, the connection
pinned to the address that was checked, size and time limits, feeds are read as feeds), a little tighter, and a sender can ask only so often.

Standard library only. api/feed.py is the thin Vercel wrapper; the tests run `handle` / `respond` against fake sites.
Environment (all optional): ALLOWED_ORIGINS (the page's origin, default https://books.zhitree.cn), IP_SALT (only to make the limit's key unreadable).
"""
import concurrent.futures
import json
import re
import time

import aggregate as agg
import submission
from submission import Reply

BODY_LIMIT = 40_000
MAX_FEEDS = 20                       # feeds in one update request
WORKERS = 5
READ_SECONDS = 20                    # of the 30 seconds a Vercel function here may run (vercel.json maxDuration): the rest is for the answer
DISCOVER_ITEMS = 8                   # latest items shown when a feed is looked up
LIMITS = {'discover': (15, 600), 'update': (40, 600)}      # requests per sender in 10 minutes, counted per warm instance: a soft limit, the real ones are Vercel's
_USED = {}


def limited(who, action, now):
    n, window = LIMITS[action]
    key = (who, action)
    recent = [t for t in _USED.get(key, []) if now - t < window]
    if len(recent) >= n:
        raise Reply(429, 'rate', '查得太频繁了，请稍等几分钟', 'Too many requests; wait a few minutes')
    recent.append(now)
    _USED[key] = recent
    if len(_USED) > 2000:                                                    # never grows without bound
        for k in [k for k, v in _USED.items() if not v or now - v[-1] > 600]:
            _USED.pop(k, None)


class reading:
    """The directory's reading, with a time limit for the whole request and tighter limits than the Action has."""
    def __enter__(self):
        self.old = (agg.TIMEOUT, agg.DEADLINE)
        agg.TIMEOUT = 7
        agg.DEADLINE = time.monotonic() + READ_SECONDS
        return self

    def __exit__(self, *exc):
        agg.TIMEOUT, agg.DEADLINE = self.old


def _item(i, with_tags=True):
    out = {'url': i['url'], 'title': i['title'], 'summary': i['summary'], 'date': i['date'], 'image': i['image'], 'lang': i['lang'], 'type': i['type']}
    if with_tags:
        out['tags'] = list(i['tags'])[:agg.TAGS_PER_ITEM]
    return out


def _shelf(s):
    return {'title': s.get('name', ''), 'home': s.get('home', ''), 'description': s.get('description', ''), 'lang': s.get('lang', '')}


def _checked(raw):
    """The address, in the form the reading code wants, or Refused. Nothing is requested here."""
    url = agg.normalize_input(raw)
    if not agg.ALLOW_LOCAL:                                                  # (the tests read fake sites on this computer)
        agg.check_url_syntax(url)
    return url


def discover(raw):
    """-> what the address is: {'kind': 'feed', 'feed': address of the feed, ...} (can be subscribed) or {'kind': 'link', ...} (a page: one link, nothing to follow)."""
    url = _checked(raw)
    kind = agg.classify(url)
    if kind['kind'] == 'unsupported':
        raise agg.Refused(kind['zh'], kind['en'])
    entry, pre, warnings = agg.discover(url)
    if entry['kind'] == 'repo':
        raise agg.Refused('GitHub 仓库暂时不能订阅（可以把里面的文章单独存成链接）', 'A GitHub repository cannot be followed yet (save its articles as links)')
    res = agg.read_source(entry, None, pre)
    shelf, items = _shelf(res['shelf']), res['items']
    out = {'ok': True, 'title': shelf['title'], 'home': shelf['home'], 'description': shelf['description'], 'lang': shelf['lang'],
           'warnings': [list(w) for w in warnings + res.get('warnings', [])]}
    if entry['kind'] == 'feed':
        newest = sorted(items, key=lambda i: i['date'], reverse=True)
        out.update({'kind': 'feed', 'feed': entry['source'], 'found': len(items), 'etag': res.get('etag', ''), 'modified': res.get('modified', ''),
                    'items': [_item(i) for i in newest[:DISCOVER_ITEMS]]})
    else:
        first = items[0]
        out.update({'kind': 'link', 'url': first['url'], 'title': first['title'], 'summary': first['summary'], 'image': first['image'], 'date': first['date'],
                    'site': shelf['title'], 'items': [_item(first)]})
    return out


def _spec(raw):
    if not isinstance(raw, dict):
        raise ValueError
    url = str(raw.get('url') or '').strip()
    if not url or len(url) > 1500:
        raise ValueError
    return {'url': url, 'etag': str(raw.get('etag') or '')[:300], 'modified': str(raw.get('modified') or '')[:100]}


def update_one(spec):
    """One feed -> {'url', 'status': 'ok' | 'unchanged' | 'error', ...}. Never raises: one bad feed must not spoil the others."""
    url = spec['url']
    try:
        addr = _checked(url)
        prev = {'parser': agg.PARSER, 'etag': spec['etag'], 'modified': spec['modified']} if (spec['etag'] or spec['modified']) else None
        res = agg.read_feed({'source': addr}, prev)
    except agg.Unchanged:
        return {'url': url, 'status': 'unchanged'}
    except agg.Refused as e:
        return {'url': url, 'status': 'error', 'message': str(e), 'message_en': e.en, 'code': e.code or 0}
    except Exception as e:                                                   # a feed that breaks the parser is that feed's problem only
        return {'url': url, 'status': 'error', 'message': '读取失败', 'message_en': 'Could not be read (%s)' % type(e).__name__, 'code': 0}
    items = [i for i in res['items'] if i]
    if not items:
        return {'url': url, 'status': 'error', 'message': '没有读到文章', 'message_en': 'No articles were found', 'code': 0}
    return {'url': url, 'status': 'ok', **_shelf(res['shelf']), 'etag': res.get('etag', ''), 'modified': res.get('modified', ''),
            'items': [_item(i) for i in sorted(items, key=lambda i: i['date'], reverse=True)[:agg.FEED_KEEP]]}


def update(raws):
    specs, seen = [], set()
    for raw in raws:
        s = _spec(raw)
        if s['url'] not in seen:
            seen.add(s['url'])
            specs.append(s)
    with reading():
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(update_one, specs))
    return {'ok': True, 'results': results}


def handle(payload, ip, env, now=None):
    """-> (HTTP status, JSON-able dict)."""
    now = now if now is not None else time.time()
    cfg = submission.settings(env)
    try:
        if not isinstance(payload, dict):
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        who = submission.ip_hash(ip, cfg['salt'])
        action = payload.get('action')
        if action == 'discover':
            if not isinstance(payload.get('url'), str):
                raise Reply(400, 'request', '请求格式不对', 'Bad request')
            limited(who, 'discover', now)
            with reading():
                return 200, discover(payload['url'])
        if action == 'update':
            feeds = payload.get('feeds')
            if not isinstance(feeds, list) or not feeds or len(feeds) > MAX_FEEDS:
                raise Reply(400, 'request', '一次最多更新 %d 个订阅源' % MAX_FEEDS, 'At most %d feeds at a time' % MAX_FEEDS)
            try:
                _ = [_spec(f) for f in feeds]
            except ValueError:
                raise Reply(400, 'request', '请求格式不对', 'Bad request')
            limited(who, 'update', now)
            return 200, update(feeds)
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    except Reply as r:
        return r.status, r.body()
    except agg.Refused as e:                                                 # an address that cannot be read, or is not a safe one: the same answer the submit function gives a check
        return 422, {'ok': False, 'error': 'unreachable', 'message': str(e), 'message_en': e.en, 'code': e.code or 0}


# ---------------------------------------------------------------- the HTTP side (used by api/feed.py and by the tests)
def respond(h, env, method):
    """Answer one request on a BaseHTTPRequestHandler `h`: the same origin rule as the submit function (a page of another site is refused)."""
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
        return send(200, {'ok': True, 'service': 'shelf-feed'})
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
    status, body = handle(payload, ip, env)
    return send(status, body)
