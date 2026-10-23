"""Hot lists as feeds: GET /api/hot?p=github  ->  an RSS feed of what is on that platform's hot list right now (rank, title, heat, a link to the platform's own page).

What a "hot list" site does, and so does this: it asks the platform for the same small JSON (or page) that the platform's own web page shows, with a browser's headers, takes the rank,
the title, the heat and the address, and hands that on. No article text is read or kept. Here the answer is an ordinary RSS feed, so a reader follows a hot list like any blog:
"subscribe" in the shelf, the new entries that came onto the list show up as new articles, a click opens the platform's page. Nothing is stored here: each answer is made from the
platform's answer of the last few minutes (an in-memory copy for 3 minutes, and the CDN keeps the feed for 10).

These are the platforms' own front-end addresses, not documented APIs: they can change or close without notice, and their terms are the platforms' own. So:
  - the platforms of general news (Weibo, Zhihu, Baidu, Toutiao) are OFF until the owner of the site turns them on (HOT_ENABLE=weibo,zhihu or all): what is on those lists is whatever
    the country is talking about that hour, and whether to pass it on is the owner's decision;
  - HOT_DISABLE=juejin,v2ex turns any of them off again; a platform that is off answers 404;
  - the list of what is on is GET /api/hot (JSON).
Nothing here reads a page that belongs to the reader: every address is fixed in PLATFORMS below, the only thing a request chooses is which of them (and how many entries).
"""
import hashlib
import html
import json
import re
import time
import urllib.parse
from email.utils import formatdate
from xml.sax.saxutils import escape

import aggregate as agg

BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36', 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
CACHE_SECONDS = 180            # the platform is asked at most this often per warm instance
STALE_SECONDS = 3600           # when the platform does not answer, the last good copy up to this old is served
DEFAULT_COUNT, MAX_COUNT = 30, 50
CDN = 'public, max-age=300, s-maxage=600, stale-while-revalidate=3600'
_CACHE = {}


def _heat(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n or '')
    if n >= 1e8:
        return '%.1f 亿' % (n / 1e8)
    if n >= 1e4:
        return '%.1f 万' % (n / 1e4)
    return '%d' % n


def _text(s, limit=140):
    s = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(s or '')))).strip()
    return s[:limit]


def _json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        raise agg.Refused('榜单的格式变了', 'The format of the list has changed')


# ---------------------------------------------------------------- one parser per platform: text of the answer -> [{'title', 'url', 'heat', 'note'}]
def p_weibo(raw):
    rows = (_json(raw).get('data') or {}).get('realtime') or []
    return [{'title': r['word'], 'url': 'https://s.weibo.com/weibo?q=' + urllib.parse.quote('#%s#' % r['word']), 'heat': '热度 ' + _heat(r.get('num')), 'note': r.get('flag_desc') or r.get('label_name') or ''}
            for r in rows if isinstance(r, dict) and r.get('word') and not r.get('is_ad')]


def p_zhihu(raw):
    out = []
    for r in _json(raw).get('data') or []:
        t = r.get('target') if isinstance(r, dict) else None
        if isinstance(t, dict) and t.get('type') == 'question' and t.get('title') and t.get('id'):
            out.append({'title': t['title'], 'url': 'https://www.zhihu.com/question/%s' % t['id'], 'heat': _text(r.get('detail_text')) or ('%s 回答' % t.get('answer_count', 0)), 'note': ''})
    return out


def p_bilibili(raw):
    rows = (_json(raw).get('data') or {}).get('list') or []
    return [{'title': r['title'], 'url': 'https://www.bilibili.com/video/%s' % r['bvid'], 'heat': '播放 ' + _heat((r.get('stat') or {}).get('view')), 'note': (r.get('owner') or {}).get('name', '')}
            for r in rows if isinstance(r, dict) and r.get('title') and re.fullmatch(r'BV\w{10}', str(r.get('bvid') or ''))]


def p_baidu(raw):
    m = re.search(r'<!--s-data:(.*?)-->', raw, re.S)
    if not m:
        raise agg.Refused('榜单的格式变了', 'The format of the list has changed')
    cards = (_json(m.group(1)).get('data') or {}).get('cards') or []
    rows = (cards[0].get('content') if cards and isinstance(cards[0], dict) else None) or []
    out = []
    for r in rows:
        if isinstance(r, dict) and (r.get('query') or r.get('word')):
            title = r.get('query') or r.get('word')
            url = r.get('rawUrl') or r.get('url') or 'https://www.baidu.com/s?wd=' + urllib.parse.quote(title)
            out.append({'title': title, 'url': url, 'heat': '热度 ' + _heat(r.get('hotScore')), 'note': _text(r.get('desc'), 100)})
    return out


def p_toutiao(raw):
    return [{'title': r['Title'], 'url': 'https://www.toutiao.com/trending/%s/' % r['ClusterId'], 'heat': '热度 ' + _heat(r.get('HotValue')), 'note': ''}
            for r in _json(raw).get('data') or [] if isinstance(r, dict) and r.get('Title') and re.fullmatch(r'\d+', str(r.get('ClusterId') or ''))]


def p_juejin(raw):
    out = []
    for r in _json(raw).get('data') or []:
        c = r.get('content') if isinstance(r, dict) else None
        if isinstance(c, dict) and c.get('title') and re.fullmatch(r'\d+', str(c.get('content_id') or '')):
            cc = r.get('content_counter') or {}
            out.append({'title': c['title'], 'url': 'https://juejin.cn/post/%s' % c['content_id'], 'heat': '热度 ' + _heat(cc.get('hot_rank')), 'note': (r.get('author') or {}).get('name', '')})
    return out


def p_v2ex(raw):
    data = _json(raw)
    return [{'title': r['title'], 'url': r['url'], 'heat': '回复 %s' % r.get('replies', 0), 'note': (r.get('node') or {}).get('title', '')}
            for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and r.get('title') and str(r.get('url') or '').startswith('https://www.v2ex.com/t/')]


def p_github(raw):
    out = []
    for block in re.findall(r'<article class="Box-row".*?</article>', raw, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([\w.-]+/[\w.-]+)"', block, re.S)
        if not m:
            continue
        desc = re.search(r'<p class="[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>', block, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', block)
        today = re.search(r'([\d,]+)\s+stars\s+today', block)
        out.append({'title': m.group(1), 'url': 'https://github.com/' + m.group(1), 'heat': ('今日 +%s ★' % today.group(1)) if today else '', 'note': ' · '.join(x for x in (_text(lang.group(1), 30) if lang else '', _text(desc.group(1), 140) if desc else '') if x)})
    if not out and 'Box-row' not in raw:
        raise agg.Refused('榜单的格式变了', 'The format of the list has changed')
    return out


def p_netease(raw):
    out = []
    for r in (_json(raw).get('result') or {}).get('tracks') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d+', str(r.get('id') or '')):
            who = ' / '.join(a.get('name', '') for a in r.get('artists') or [] if isinstance(a, dict))
            out.append({'title': '%s — %s' % (r['name'], who) if who else r['name'], 'url': 'https://music.163.com/#/song?id=%s' % r['id'], 'heat': '', 'note': (r.get('album') or {}).get('name', '')})
    return out


# id -> what it is. `default`: on unless the owner turns it off (the news platforms are off until the owner turns them on).
PLATFORMS = {
    'github': {'name': 'GitHub Trending（今日）', 'name_en': 'GitHub Trending (today)', 'home': 'https://github.com/trending', 'url': 'https://github.com/trending?since=daily', 'headers': {}, 'parse': p_github, 'lang': 'en', 'default': True},
    'juejin': {'name': '掘金热榜', 'name_en': 'Juejin hot articles', 'home': 'https://juejin.cn/hot/articles', 'url': 'https://api.juejin.cn/content_api/v1/content/article_rank?category_id=1&type=hot', 'headers': {}, 'parse': p_juejin, 'lang': 'zh', 'default': True},
    'v2ex': {'name': 'V2EX 今日热议', 'name_en': 'V2EX hot topics', 'home': 'https://www.v2ex.com/', 'url': 'https://www.v2ex.com/api/topics/hot.json', 'headers': {}, 'parse': p_v2ex, 'lang': 'zh', 'default': True},
    'bilibili': {'name': 'B 站热门视频', 'name_en': 'Bilibili popular videos', 'home': 'https://www.bilibili.com/v/popular/all', 'url': 'https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1', 'headers': {'Referer': 'https://www.bilibili.com/'}, 'parse': p_bilibili, 'lang': 'zh', 'default': True},
    'netease': {'name': '网易云音乐飙升榜', 'name_en': 'NetEase Cloud Music: rising songs', 'home': 'https://music.163.com/#/discover/toplist?id=19723756', 'url': 'https://music.163.com/api/playlist/detail?id=19723756', 'headers': {}, 'parse': p_netease, 'lang': 'zh', 'default': True},
    'weibo': {'name': '微博热搜', 'name_en': 'Weibo hot searches', 'home': 'https://s.weibo.com/top/summary', 'url': 'https://weibo.com/ajax/side/hotSearch', 'headers': {'Referer': 'https://weibo.com/'}, 'parse': p_weibo, 'lang': 'zh', 'default': False},
    'zhihu': {'name': '知乎热榜', 'name_en': 'Zhihu hot questions', 'home': 'https://www.zhihu.com/hot', 'url': 'https://api.zhihu.com/topstory/hot-lists/total?limit=50', 'headers': {}, 'parse': p_zhihu, 'lang': 'zh', 'default': False},
    'baidu': {'name': '百度热搜', 'name_en': 'Baidu hot searches', 'home': 'https://top.baidu.com/board?tab=realtime', 'url': 'https://top.baidu.com/board?tab=realtime', 'headers': {}, 'parse': p_baidu, 'lang': 'zh', 'default': False},
    'toutiao': {'name': '今日头条热榜', 'name_en': 'Toutiao hot board', 'home': 'https://www.toutiao.com/', 'url': 'https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc', 'headers': {}, 'parse': p_toutiao, 'lang': 'zh', 'default': False},
}
NEWS = ('weibo', 'zhihu', 'baidu', 'toutiao')


def enabled(env):
    """The platforms that are on: the defaults, plus HOT_ENABLE (a list, or `all`), minus HOT_DISABLE."""
    on = {k for k, v in PLATFORMS.items() if v['default']}
    add = {x.strip() for x in str((env or {}).get('HOT_ENABLE') or '').lower().split(',') if x.strip()}
    off = {x.strip() for x in str((env or {}).get('HOT_DISABLE') or '').lower().split(',') if x.strip()}
    on |= set(PLATFORMS) if 'all' in add else (add & set(PLATFORMS))
    return [k for k in PLATFORMS if k in on and k not in off]


def entries(pid, now=None):
    """-> the platform's list now (a copy kept for CACHE_SECONDS; an old copy when the platform does not answer). Raises agg.Refused."""
    now = time.time() if now is None else now
    spec = PLATFORMS[pid]
    hit = _CACHE.get(pid)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        _, h, body = agg.fetch(spec['url'], '*/*', 3 * 1024 * 1024, headers={**BROWSER, **spec['headers']}, truncate=True)
        rows = spec['parse'](agg.decode(body, h))
        if not rows:
            raise agg.Refused('榜单现在是空的', 'The list is empty right now')
    except agg.Refused:
        if hit and now - hit[0] < STALE_SECONDS:
            return hit[1]
        raise
    _CACHE[pid] = (now, rows)
    return rows


def rss(pid, rows, now=None):
    spec = PLATFORMS[pid]
    stamp = formatdate(now if now is not None else time.time(), usegmt=True)
    items = []
    for i, r in enumerate(rows, 1):
        bits = ['第 %d 名' % i] + [x for x in (r['heat'], r['note']) if x]
        items.append('<item><title>%s</title><link>%s</link><guid isPermaLink="false">hot:%s:%s</guid><pubDate>%s</pubDate><description>%s</description></item>'
                     % (escape(r['title']), escape(r['url']), pid, hashlib.sha1(r['url'].encode('utf-8')).hexdigest()[:12], stamp, escape(' · '.join(bits))))
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>%s</title><link>%s</link><description>%s</description><language>%s</language><lastBuildDate>%s</lastBuildDate>%s</channel></rss>'
            % (escape(spec['name']), escape(spec['home']), escape('%s：平台当前的榜单（排名、标题、热度和平台自己的链接），由知树阅读按需生成，不保存任何内容。' % spec['name']), 'zh-CN' if spec['lang'] == 'zh' else 'en', stamp, ''.join(items)))


def handle(query, env, now=None):
    """-> (status, content type, body bytes, cache header). `query`: the part after "?" (a dict of lists, as parse_qs makes it)."""
    on = enabled(env)
    pid = (query.get('p') or [''])[0]
    if not pid:                                                                # what is on
        body = json.dumps({'ok': True, 'service': 'shelf-hot', 'platforms': [{'id': k, 'name': PLATFORMS[k]['name'], 'name_en': PLATFORMS[k]['name_en'], 'home': PLATFORMS[k]['home'], 'lang': PLATFORMS[k]['lang'],
                                                                                'news': k in NEWS} for k in on]}, ensure_ascii=False).encode('utf-8')
        return 200, 'application/json; charset=utf-8', body, 'public, max-age=300'
    if pid not in PLATFORMS or pid not in on:
        return 404, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'platform', 'message': '没有这个榜单', 'message_en': 'No such list'}).encode('utf-8'), 'no-store'
    try:
        n = max(1, min(MAX_COUNT, int((query.get('n') or [DEFAULT_COUNT])[0])))
    except ValueError:
        n = DEFAULT_COUNT
    try:
        rows = entries(pid, now)[:n]
    except agg.Refused as e:
        return 502, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'upstream', 'message': '这个榜单现在读不到：%s' % e, 'message_en': 'This list cannot be read right now: %s' % e.en}, ensure_ascii=False).encode('utf-8'), 'no-store'
    if (query.get('format') or [''])[0] == 'json':
        return 200, 'application/json; charset=utf-8', json.dumps({'ok': True, 'platform': pid, 'name': PLATFORMS[pid]['name'], 'items': rows}, ensure_ascii=False).encode('utf-8'), CDN
    return 200, 'application/rss+xml; charset=utf-8', rss(pid, rows, now).encode('utf-8'), CDN


def respond(h, env, method):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/hot.py and by the tests)."""
    if method != 'GET':
        h.send_response(405)
        h.send_header('Allow', 'GET')
        h.send_header('Content-Length', '0')
        h.end_headers()
        return
    status, ctype, body, cache = handle(urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query), env)
    h.send_response(status)
    h.send_header('Content-Type', ctype)
    h.send_header('Cache-Control', cache)
    h.send_header('X-Robots-Tag', 'noindex')
    h.send_header('Access-Control-Allow-Origin', '*')          # public, read-only, the same for everybody: any page may read what is on (the page shows only the lists that are)
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    h.wfile.write(body)
