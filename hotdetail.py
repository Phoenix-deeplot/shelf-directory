"""hotdetail.py: what is behind a row of a hot list, so that the site can show it itself instead of sending the reader away.

    GET /api/hot?detail=<address of a row>      what the platform shows on that page, as the site's reader can show it:
        {"ok": true, "kind": "html" | "md" | "podcast", "title", "content" (safe html) | "md" (+ "base") | "feed", "image", "meta"}
    GET /api/hot?related=<words>&lang=zh|en    the news that are about these words right now (for the platforms whose rows are only a phrase: Weibo, Douyin, Google Trends ...):
        {"ok": true, "items": [{"title", "url", "source", "date", "snippet"}]}

Only a few shapes of address are known (V2EX topics, Zhihu Daily stories, a GitHub repository's README, an Apple Podcasts show); the request chooses the address, never the host: an address that
does not match one of the patterns below is answered "none", nothing is fetched for it. The text is the platform's own, rebuilt as a small safe subset of HTML (htmlsafe.py) or kept as
Markdown for the page to render (the page cleans it again). Nothing is stored; the answers are kept in memory for a while and by the CDN. The platforms' terms are their own.
"""
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import aggregate as agg
import htmlsafe

BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
TEXT_CHARS = 150_000                 # of one text (the rest is left out and the reader is told)
KEEP_SECONDS = 600                   # a copy in memory
KEEP_MAX = 300
REL_PER_MINUTE = 40                  # fetches of "related news" per minute per warm instance (a copy served from memory is free)
REL_ITEMS = 8
_COPY = {}
_REL_USED = []


class Nothing(Exception):
    """No page of this kind is known for the address."""


def _get(url, accept='*/*', limit=2 * 1024 * 1024, headers=None):
    final, h, body = agg.fetch(url, accept, limit, headers={**BROWSER, **(headers or {})}, truncate=True)
    return final, h, body


def _json(url, headers=None):
    import json
    _, h, body = _get(url, 'application/json, */*;q=0.5', headers=headers)
    try:
        return json.loads(agg.decode(body, h))
    except ValueError:
        raise agg.Refused('页面的格式变了', 'The format of the page has changed')


def _safe(raw, base):
    safe, cut = htmlsafe.sanitize(raw, base, TEXT_CHARS)
    return safe, bool(cut)


def _https(u):
    u = str(u or '').strip()
    if u.startswith('//'):
        u = 'https:' + u
    if u.startswith('http://'):
        u = 'https://' + u[7:]
    return u if re.fullmatch(r'https://[^\s"<>]{4,400}', u) else ''


# ---------------------------------------------------------------- one reader per kind of address: (match) -> dict
def d_v2ex(m):
    tid = m.group(1)
    topic = (_json('https://www.v2ex.com/api/topics/show.json?id=' + tid) or [None])[0]
    if not isinstance(topic, dict) or not topic.get('title'):
        raise Nothing()
    try:
        replies = _json('https://www.v2ex.com/api/replies/show.json?topic_id=' + tid)
    except agg.Refused:
        replies = []
    who = (topic.get('member') or {}).get('username') or ''
    raw = topic.get('content_rendered') or html.escape(topic.get('content') or '').replace('\n', '<br>')
    if isinstance(replies, list) and replies:
        raw += '<h2>回复</h2>' + ''.join('<p><b>%s</b>　%s</p>' % (html.escape((r.get('member') or {}).get('username') or ''), r.get('content_rendered') or '')
                                       for r in replies[:30] if isinstance(r, dict))
    safe, cut = _safe(raw, 'https://www.v2ex.com/t/' + tid)
    node = (topic.get('node') or {}).get('title') or ''
    return {'kind': 'html', 'title': str(topic['title'])[:200], 'content': safe, 'cut': cut, 'image': '',
            'meta': ' · '.join(x for x in ('V2EX', node, who, '%s 条回复' % topic.get('replies', 0)) if x)}


def d_zhihu_daily(m):
    d = _json('https://news-at.zhihu.com/api/4/news/' + m.group(1))
    if not isinstance(d, dict) or not d.get('body'):
        raise Nothing()
    safe, cut = _safe(d['body'], 'https://daily.zhihu.com/story/' + m.group(1))
    return {'kind': 'html', 'title': str(d.get('title') or '')[:200], 'content': safe, 'cut': cut, 'image': _https(d.get('image')), 'meta': '知乎日报'}


README_NAMES = ('README.md', 'readme.md', 'Readme.md', 'README.MD', 'README.markdown', 'README.rst', 'README.txt', 'README')


def d_github(m):
    owner, repo = m.group(1), m.group(2)
    if repo.endswith('.git'):
        repo = repo[:-4]
    if owner.lower() in ('topics', 'orgs', 'features', 'sponsors', 'settings', 'login', 'about', 'trending', 'explore', 'marketplace'):
        raise Nothing()
    base = 'https://raw.githubusercontent.com/%s/%s/HEAD/' % (owner, repo)
    for name in README_NAMES:
        try:
            _, h, body = _get(base + name, 'text/plain, */*;q=0.5', limit=400_000)
        except agg.Refused as e:
            if e.code == 404:
                continue
            raise
        text = agg.decode(body, h)
        return {'kind': 'md', 'title': '%s/%s' % (owner, repo), 'md': text[:TEXT_CHARS], 'cut': len(text) > TEXT_CHARS, 'base': base, 'image': '', 'meta': 'GitHub · README'}
    raise Nothing()


def d_podcast(m):
    cc = (re.match(r'https://podcasts\.apple\.com/([a-z]{2})/', m.group(0)) or [None, 'us'])[1]
    d = _json('https://itunes.apple.com/lookup?id=%s&country=%s&entity=podcast' % (m.group(1), cc))
    r = (d.get('results') or [None])[0] if isinstance(d, dict) else None
    feed = _https((r or {}).get('feedUrl'))
    if not isinstance(r, dict) or not feed:
        raise Nothing()
    return {'kind': 'podcast', 'title': str(r.get('collectionName') or r.get('trackName') or '')[:200], 'feed': feed, 'image': _https(r.get('artworkUrl600') or r.get('artworkUrl100')), 'content': '',
            'meta': ' · '.join(x for x in (r.get('artistName') or '', r.get('primaryGenreName') or '', ('%s 集' % r['trackCount']) if r.get('trackCount') else '') if x)}


DETAILS = [
    (re.compile(r'https://www\.v2ex\.com/t/(\d{3,9})(?:[#?].*)?$'), d_v2ex),
    (re.compile(r'https://daily\.zhihu\.com/story/(\d{4,10})$'), d_zhihu_daily),
    (re.compile(r'https://github\.com/([A-Za-z0-9][\w.-]{0,38})/([\w.-]{1,100})/?$'), d_github),
    (re.compile(r'https://podcasts\.apple\.com/[a-z]{2}/podcast/[^/?#]*/id(\d{5,12})(?:[?#].*)?$'), d_podcast),
]


def _keep(key, now, make):
    hit = _COPY.get(key)
    if hit and now - hit[0] < KEEP_SECONDS:
        return hit[1]
    val = make()
    if len(_COPY) >= KEEP_MAX:
        for k in sorted(_COPY, key=lambda k: _COPY[k][0])[:KEEP_MAX // 3]:
            _COPY.pop(k, None)
    _COPY[key] = (now, val)
    return val


def detail(url, now=None):
    """-> the dict of the matching reader. Raises Nothing (no reader for this address) or agg.Refused (the platform did not answer)."""
    now = time.time() if now is None else now
    url = str(url or '').strip()
    for rx, fn in DETAILS:
        m = rx.match(url)
        if m:
            out = _keep('d:' + url, now, lambda: fn(m))
            return {'ok': True, 'url': url, **out}
    raise Nothing()


def has_detail(url):
    return any(rx.match(str(url or '').strip()) for rx, _ in DETAILS)


# ---------------------------------------------------------------- the news about a phrase (Bing News' RSS: no key, the address of the story is inside the link)
def _story_url(link):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(link or '')).query)
    u = (q.get('url') or [''])[0]
    return _https(u) if u else _https(link) if 'bing.com' not in link else ''


def _day(s):
    try:
        return parsedate_to_datetime(s).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return ''


def parse_news(raw):
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise agg.Refused('相关报道的格式变了', 'The format of the news has changed')
    try:
        root = ET.fromstring(raw.lstrip('﻿ \t\r\n').encode('utf-8'))
    except ET.ParseError:
        raise agg.Refused('相关报道的格式变了', 'The format of the news has changed')
    out, seen = [], set()
    for it in root.iter('item'):
        title = re.sub(r'\s+', ' ', html.unescape(it.findtext('title') or '')).strip()
        url = _story_url(it.findtext('link') or '')
        host = urllib.parse.urlsplit(url).hostname or ''
        if not title or not url or host.endswith('bing.com') or (host, title) in seen:
            continue
        seen.add((host, title))
        snippet = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', it.findtext('description') or ''))).strip()
        out.append({'title': title[:200], 'url': url, 'source': (it.findtext('{*}Source') or host.replace('www.', ''))[:60], 'date': _day(it.findtext('pubDate')), 'snippet': snippet[:200]})
        if len(out) >= REL_ITEMS:
            break
    return out


def related(words, lang='zh', now=None):
    """-> the news about `words` [{'title', 'url', 'source', 'date', 'snippet'}]. Raises Nothing (nothing to ask) or agg.Refused."""
    now = time.time() if now is None else now
    q = re.sub(r'\s+', ' ', re.sub(r'[#"<>]', ' ', str(words or ''))).strip()[:60]
    if len(q) < 2:
        raise Nothing()
    lang = 'en' if lang == 'en' else 'zh'
    key = 'r:%s:%s' % (lang, q.lower())
    hit = _COPY.get(key)
    if hit and now - hit[0] < KEEP_SECONDS:
        return hit[1]
    _REL_USED[:] = [t for t in _REL_USED if now - t < 60]
    if len(_REL_USED) >= REL_PER_MINUTE:
        raise agg.Refused('查得太频繁了，请稍等一会儿', 'Too many requests; wait a moment')
    _REL_USED.append(now)

    def make():
        _, h, body = _get('https://www.bing.com/news/search?q=%s&format=rss' % urllib.parse.quote(q), 'application/rss+xml, application/xml;q=0.9, */*;q=0.5', 1024 * 1024,
                          {'Accept-Language': 'zh-CN,zh;q=0.9' if lang == 'zh' else 'en-US,en;q=0.9'})
        return parse_news(agg.decode(body, h))
    return _keep(key, now, make)
