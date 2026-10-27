"""feedapi.py: the update service for what readers subscribe to. It stores nothing.

A reader of the site subscribes to the blogs and article feeds they want to follow; that list stays in the reader's own browser (a collection per feed).
A page in a browser cannot read someone else's feed (the other site does not allow it), so the page asks this function instead:

    {"action": "discover", "url": "…"}                              what is this address?  -> a feed (name, latest items), or a single page (title, summary)
    {"action": "update", "feeds": [{"url", "etag", "modified", "content"}]}    what is new in these feeds?  -> per feed: items, "unchanged", or why not
    {"action": "read", "url": "…"}                                  the article at this address, for reading in the page: title, author, date and the text as safe html

No database, no GitHub token, no list of subscriptions is kept here: the addresses are read while the request lasts, and what comes back is titles, summaries,
links and dates. With "content": true the text of the newest articles comes along too, when the feed itself carries it (it is the feed's own text, not a page that was
fetched), rebuilt as a small safe subset of HTML (htmlsafe.py), bounded per article and per answer; a feed that carries only excerpts gets none. "read" fetches the page once, at the reader's request (an honest user agent, no cookies, no log-in, robots.txt respected unless READ_IGNORE_ROBOTS=1), finds the article in it
(readable.py) and hands it back; a page that is not an article (a login or subscription wall, a bot check, a page drawn by scripts, a PDF, a video) is answered with the reason
instead (422, "reason"), and the page then offers the original address. Nothing is kept here. The reading is the
directory's own bounded reading (aggregate.py: https only, public addresses only, the connection pinned to the address that was checked, size and time limits, feeds are
read as feeds), a little tighter, and a sender can ask only so often.

Standard library only. api/feed.py is the thin Vercel wrapper; the tests run `handle` / `respond` against fake sites.
Environment (all optional): ALLOWED_ORIGINS (the page's origin, default https://books.zhitree.cn), IP_SALT (only to make the limit's key unreadable).
"""
import concurrent.futures
import json
import re
import time

import aggregate as agg
import htmlsafe
import readable
import submission
from submission import Reply

BODY_LIMIT = 40_000
MAX_FEEDS = 20                       # feeds in one update request
WORKERS = 5
READ_SECONDS = 20                    # leave time for sanitizing and returning the response
DISCOVER_ITEMS = 8                   # latest items shown when a feed is looked up
CONTENT_ITEMS = 12                   # of a feed's newest items, those whose text may come along
CONTENT_ITEM_CHARS = 60_000          # of one article; the rest is left out and the reader is told
CONTENT_TOTAL = 1_500_000            # of one answer (a Vercel function may answer 4.5 MB at most; Chinese text is 3 bytes a character)
CONTENT_MIN_TEXT = 500               # characters of text: a feed's 300-character description is an excerpt, not the article
READ_CHARS = 150_000                 # of one article read from its page (the rest is left out and the reader is told)
READ_UA = 'ZhiTreeBooks-Reader/1 (+https://books.zhitree.cn; fetched because a reader opened this address; nothing is stored)'
READ_ACCEPT = 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.5'
LIMITS = {'discover': (15, 600), 'update': (40, 600), 'read': (40, 600)}      # requests per sender in 10 minutes, counted per warm instance: a soft limit, the real ones are Vercel's
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
    if i.get('audio'):                                                       # a podcast episode: where its sound is (the page plays it from there; nothing of it passes through here)
        out['audio'] = {k: i['audio'][k] for k in ('url', 'type', 'dur')}
    return out


def _content(i):
    """-> (safe html, cut) when the feed carries the article itself, else None."""
    raw = i.get('_content') or ''
    if not raw:
        return None
    safe, cut = htmlsafe.sanitize(raw, i['url'], CONTENT_ITEM_CHARS)
    return (safe, cut) if htmlsafe.text_len(safe) >= CONTENT_MIN_TEXT else None


def _unreadable(e):
    """An Unreadable as the answer of the service: the reason as a code and in words, both languages."""
    return {'ok': False, 'error': 'unreadable', 'reason': e.code, 'message': e.zh, 'message_en': e.en}


def _page_article(addr, pre=None, env=None):
    """The article at an address -> dict (see readable.extract) with 'url' (where it ended up). Raises readable.Unreadable. `pre` is a page that was fetched already."""
    kind = agg.classify(addr)['kind']
    if kind == 'pdf':
        raise readable.Unreadable('pdf')
    if kind != 'web':
        raise readable.Unreadable('unsupported')
    if pre is None:
        if not (env or {}).get('READ_IGNORE_ROBOTS') and not agg.feed_like(addr) and not agg.robots_allows(addr):
            raise readable.Unreadable('robots')
        try:
            pre = agg.fetch(addr, READ_ACCEPT, headers={'User-Agent': READ_UA}, truncate=True)
        except agg.Refused as e:
            code = e.code or 0
            raise readable.Unreadable('login' if code in (401, 402, 407) else 'blocked' if code in (403, 429, 451) else 'gone' if code in (404, 410) else 'unreachable')
    final, h, body = pre
    ctype = (h.get('content-type') or '').lower()
    if 'pdf' in ctype:
        raise readable.Unreadable('pdf')
    if ctype and not any(x in ctype for x in ('html', 'xml', 'text')):
        raise readable.Unreadable('unsupported')
    art = readable.extract(agg.decode(body, h), final, READ_CHARS)
    art['url'] = final
    return art


def read(raw, env=None):
    art = _page_article(_checked(raw), None, env)
    return {'ok': True, 'kind': 'article', **art}


def _shelf(s):
    return {'title': s.get('name', ''), 'home': s.get('home', ''), 'description': s.get('description', ''), 'lang': s.get('lang', ''), 'image': s.get('image', '')}


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
        out.update({'kind': 'feed', 'feed': entry['source'], 'image': shelf['image'], 'found': len(items), 'etag': res.get('etag', ''), 'modified': res.get('modified', ''),
                    'items': [_item(i) for i in newest[:DISCOVER_ITEMS]]})
    else:
        first = items[0]
        out.update({'kind': 'link', 'url': first['url'], 'title': first['title'], 'summary': first['summary'], 'image': first['image'], 'date': first['date'],
                    'site': shelf['title'], 'items': [_item(first)]})
        try:                                                                 # the page was read once for its title: the same text is its article, if it has one
            if not pre:
                raise readable.Unreadable('pdf' if agg.classify(url)['kind'] == 'pdf' else 'unsupported')
            art = _page_article(url, pre)
            out.update({'readable': True, 'content': art['content'], 'content_cut': art['cut'], 'author': art['author']})
            if art['title']:
                out['title'] = art['title']
            if art['date'] and not out['date']:
                out['date'] = art['date']
        except readable.Unreadable as e:
            out.update({'readable': False, 'reason': e.code, 'reason_message': e.zh, 'reason_message_en': e.en})
    return out


def _spec(raw):
    if not isinstance(raw, dict):
        raise ValueError
    url = str(raw.get('url') or '').strip()
    if not url or len(url) > 1500:
        raise ValueError
    return {'url': url, 'etag': str(raw.get('etag') or '')[:300], 'modified': str(raw.get('modified') or '')[:100], 'content': raw.get('content') is True}


def update_one(spec):
    """One feed -> {'url', 'status': 'ok' | 'unchanged' | 'error', ...}. Never raises: one bad feed must not spoil the others."""
    url = spec['url']
    want = bool(spec.get('content'))
    try:
        addr = _checked(url)
        prev = {'parser': agg.PARSER, 'etag': spec['etag'], 'modified': spec['modified']} if (spec['etag'] or spec['modified']) else None
        agg._OPT.content = want                                              # (this thread only; the text is read along with the feed)
        res = agg.read_feed({'source': addr}, prev)
    except agg.Unchanged:
        return {'url': url, 'status': 'unchanged'}
    except agg.Refused as e:
        return {'url': url, 'status': 'error', 'message': str(e), 'message_en': e.en, 'code': e.code or 0}
    except Exception as e:                                                   # a feed that breaks the parser is that feed's problem only
        return {'url': url, 'status': 'error', 'message': '读取失败', 'message_en': 'Could not be read (%s)' % type(e).__name__, 'code': 0}
    finally:
        agg._OPT.content = False
    items = [i for i in res['items'] if i]
    if not items:
        return {'url': url, 'status': 'error', 'message': '没有读到文章', 'message_en': 'No articles were found', 'code': 0}
    out = []
    for n, i in enumerate(sorted(items, key=lambda i: i['date'], reverse=True)[:agg.FEED_KEEP]):
        it = _item(i)
        c = _content(i) if want and n < CONTENT_ITEMS else None
        if c:
            it['content'] = c[0]
            if c[1]:
                it['content_cut'] = True
        out.append(it)
    res_out = {'url': url, 'status': 'ok', **_shelf(res['shelf']), 'etag': res.get('etag', ''), 'modified': res.get('modified', ''), 'items': out}
    if want:
        res_out['content_mode'] = True                                       # "text was asked for and looked at": the page need not ask again for what has none
    return res_out


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
    total = 0
    for r in results:                                                        # one answer carries so much text and no more: the later feeds' articles lose theirs first
        for it in r.get('items', []):
            if 'content' in it:
                if total + len(it['content']) > CONTENT_TOTAL:
                    del it['content']
                    it.pop('content_cut', None)
                else:
                    total += len(it['content'])
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
        if action == 'read':
            if not isinstance(payload.get('url'), str):
                raise Reply(400, 'request', '请求格式不对', 'Bad request')
            limited(who, 'read', now)
            with reading():
                return 200, read(payload['url'], env)
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
    except readable.Unreadable as e:                                          # a page that is not an article: the reason, so that the page can offer the original instead
        return 422, _unreadable(e)
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
