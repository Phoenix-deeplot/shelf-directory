"""Hot lists as feeds: GET /api/hot?p=github  ->  an RSS feed of what is on that platform's hot list right now (rank, title, heat, a link to the platform's own page).
GET /api/hot?p=github&format=json  ->  the same as JSON (what the hot-list page of the site reads); GET /api/hot  ->  the columns and the platforms that are on.

What a "hot list" site does, and so does this: it asks the platform for the same small JSON (or page) that the platform's own web page shows, with a browser's headers, takes the rank,
the title, the heat and the address, and hands that on. No article text is read or kept. Here the answer is an ordinary RSS feed, so a reader follows a hot list like any blog:
"subscribe" in the shelf, the new entries that came onto the list show up as new articles, a click opens the platform's page. Nothing is stored here: each answer is made from the
platform's answer of the last few minutes (an in-memory copy for 3 minutes, and the CDN keeps the feed for 10).

Where the platform has no list of its own to read, or none that is open, the list comes from one of three other places, and the page says so ("via ..."):
  - the platform's own RSS / Atom feed (news sites, blogs, arXiv, ...): a feed has no rank, so the list shows the newest first, with dates;
  - tophub.today / rebang.today, two hot-list sites that already collect a few lists (WeChat articles, Xueqiu, Xiaohongshu) no platform offers openly: read like a platform's own page;
  - the owner's own list in registry/hot.json: any RSS address, or a route of an RSSHub that the owner runs (RSSHUB_BASE=https://rsshub.example.com), or a tophub.today list by its id.

These are the platforms' own front-end addresses, not documented APIs: they can change or close without notice, and their terms are the platforms' own. So:
  - every list is on unless the owner turns it off: HOT_DISABLE=weibo,douyin (a platform that is off answers 404). HOT_ENABLE=... adds the ones whose `default` is off (none now);
  - a list that needs a key is on only when the key is set: YOUTUBE_API_KEY for YouTube's most popular videos;
  - a list of a country (YouTube, Google Trends) follows the reader: `&region=JP` asks for that country, `&region=auto` (what the page asks for until the reader picks one) takes the country of the
    address the reader comes from (Vercel's x-vercel-ip-country header, used for this one answer and kept nowhere), else the country of the language of their browser, else the owner's
    YOUTUBE_REGION (two letters), else US. An answer that depends on the reader is not shared by the CDN;
  - the list of what is on is GET /api/hot (JSON).
Nothing here reads a page that belongs to the reader: every address is fixed in PLATFORMS below (or in the owner's registry/hot.json), the only thing a request chooses is which of them (and how many entries).
"""
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import formatdate
from xml.sax.saxutils import escape

import aggregate as agg

BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36', 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
CACHE_SECONDS = 180            # the platform is asked at most this often per warm instance
THIRD_SECONDS = 600            # a list that another site (tophub.today, rebang.today) collected is asked for less often: they limit what comes from one address
STALE_SECONDS = 3600           # when the platform does not answer, the last good copy up to this old is served
DEFAULT_COUNT, MAX_COUNT, KEEP = 30, 50, 100
CDN = 'public, max-age=300, s-maxage=600, stale-while-revalidate=3600'
PRIVATE = 'private, max-age=300'
FEED_ACCEPT = 'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8'
_CACHE = {}

# The columns of the hot-list page, in the order they are shown.
COLUMNS = [
    {'id': 'news', 'name': '综合热点', 'name_en': 'Top stories'},
    {'id': 'tech', 'name': '科技与开发', 'name_en': 'Tech & dev'},
    {'id': 'money', 'name': '财经', 'name_en': 'Finance'},
    {'id': 'world', 'name': '国际', 'name_en': 'World'},
    {'id': 'ai', 'name': 'AI 与学术', 'name_en': 'AI & research'},
    {'id': 'play', 'name': '读书与影音', 'name_en': 'Books & media'},
]
COLUMN_IDS = [c['id'] for c in COLUMNS]
WEB = ('http://', 'https://')


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


def _n(n):
    try:
        return '{:,}'.format(int(float(n)))
    except (TypeError, ValueError):
        return ''


def _text(s, limit=140):
    s = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(s or '')))).strip()
    return s[:limit]


def _json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        raise agg.Refused('榜单的格式变了', 'The format of the list has changed')


def _changed():
    return agg.Refused('榜单的格式变了', 'The format of the list has changed')


def _untrack(url):
    """Without the tracking parameters some feeds add (utm_*, the BBC's at_*): the same story keeps the same address."""
    base, _, q = url.partition('?')
    keep = [x for x in q.split('&') if x and not re.match(r'(utm_\w*|at_\w*)=', x)]
    return base + ('?' + '&'.join(keep) if keep else '')


def _norm(r):
    """One row as the pages and the feed take it: the five fields, an http(s) address, no markup. None for a row that is not one."""
    if not isinstance(r, dict):
        return None
    title, url = re.sub(r'\s+', ' ', str(r.get('title') or '')).strip()[:200], _untrack(str(r.get('url') or '').strip())          # (a title is text: the pages escape it)
    p = urllib.parse.urlsplit(url)
    if not title or p.scheme not in ('http', 'https') or not p.netloc or re.search(r'\s', url):
        return None
    alt = str(r.get('alt') or '').strip()
    if urllib.parse.urlsplit(alt).scheme not in ('http', 'https'):
        alt = ''
    return {'title': title, 'url': url, 'heat': _text(r.get('heat'), 40), 'note': _text(r.get('note'), 160), 'alt': alt, 'date': str(r.get('date') or '')[:10]}


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


def p_zhihu_daily(raw):
    return [{'title': r['title'], 'url': r['url'], 'heat': '', 'note': r.get('hint') or ''}
            for r in _json(raw).get('stories') or [] if isinstance(r, dict) and r.get('title') and str(r.get('url') or '').startswith('https://daily.zhihu.com/')]


def p_bilibili(raw):
    rows = (_json(raw).get('data') or {}).get('list') or []
    return [{'title': r['title'], 'url': 'https://www.bilibili.com/video/%s' % r['bvid'], 'heat': '播放 ' + _heat((r.get('stat') or {}).get('view')), 'note': (r.get('owner') or {}).get('name', '')}
            for r in rows if isinstance(r, dict) and r.get('title') and re.fullmatch(r'BV\w{10}', str(r.get('bvid') or ''))]


def p_toutiao(raw):
    return [{'title': r['Title'], 'url': 'https://www.toutiao.com/trending/%s/' % r['ClusterId'], 'heat': '热度 ' + _heat(r.get('HotValue')), 'note': ''}
            for r in _json(raw).get('data') or [] if isinstance(r, dict) and r.get('Title') and re.fullmatch(r'\d+', str(r.get('ClusterId') or ''))]


def p_douyin(raw):
    rows = (_json(raw).get('data') or {}).get('word_list') or []
    return [{'title': r['word'], 'url': 'https://www.douyin.com/hot/%s' % r['sentence_id'], 'heat': '热度 ' + _heat(r.get('hot_value')), 'note': ''}
            for r in rows if isinstance(r, dict) and r.get('word') and re.fullmatch(r'\d+', str(r.get('sentence_id') or ''))]


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
        raise _changed()
    return out


def p_hn(raw):
    out = []
    for r in _json(raw).get('hits') or []:
        if isinstance(r, dict) and r.get('title') and re.fullmatch(r'\d+', str(r.get('objectID') or '')):
            talk = 'https://news.ycombinator.com/item?id=%s' % r['objectID']
            out.append({'title': r['title'], 'url': r.get('url') or talk, 'alt': talk, 'heat': '▲ %s' % _n(r.get('points')), 'note': '%s 条评论' % _n(r.get('num_comments'))})
    return out


def p_lobsters(raw):
    data = _json(raw)
    return [{'title': r['title'], 'url': r.get('url') or r.get('short_id_url') or '', 'alt': r.get('comments_url') or r.get('short_id_url') or '', 'heat': '▲ %s' % _n(r.get('score')),
             'note': ' · '.join(x for x in (' '.join(r.get('tags') or []), '%s 条评论' % _n(r.get('comment_count'))) if x)}
            for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and r.get('title')]


def p_devto(raw):
    data = _json(raw)
    return [{'title': r['title'], 'url': r.get('url') or '', 'heat': '♥ %s' % _n(r.get('public_reactions_count')), 'note': ' · '.join(x for x in ((r.get('user') or {}).get('name', ''), '%s 条评论' % _n(r.get('comments_count'))) if x)}
            for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and r.get('title')]


def p_netease(raw):
    out = []
    for r in (_json(raw).get('result') or {}).get('tracks') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d+', str(r.get('id') or '')):
            who = ' / '.join(a.get('name', '') for a in r.get('artists') or [] if isinstance(a, dict))
            out.append({'title': '%s — %s' % (r['name'], who) if who else r['name'], 'url': 'https://music.163.com/#/song?id=%s' % r['id'], 'heat': '', 'note': (r.get('album') or {}).get('name', '')})
    return out


def p_qqmusic(raw):
    out = []
    for r in ((_json(raw).get('detail') or {}).get('data') or {}).get('songInfoList') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'[A-Za-z0-9]{10,16}', str(r.get('mid') or '')):
            who = ' / '.join(a.get('name', '') for a in r.get('singer') or [] if isinstance(a, dict))
            out.append({'title': '%s — %s' % (r['name'], who) if who else r['name'], 'url': 'https://y.qq.com/n/ryqq/songDetail/%s' % r['mid'], 'heat': '', 'note': (r.get('album') or {}).get('name', '')})
    return out


def p_apple(raw):
    """Apple's marketing-tools charts (songs, podcasts)."""
    out = []
    for r in (_json(raw).get('feed') or {}).get('results') or []:
        if isinstance(r, dict) and r.get('name') and re.match(r'https://(podcasts|music)\.apple\.com/', str(r.get('url') or '')):
            genre = ((r.get('genres') or [{}])[0] or {}).get('name', '')
            out.append({'title': r['name'], 'url': r['url'], 'heat': '', 'note': ' · '.join(x for x in (r.get('artistName') or '', genre) if x)})
    return out


def p_ths(raw):
    """Tonghuashun's hottest stocks (A shares, by the hour)."""
    out = []
    for r in ((_json(raw).get('data') or {}).get('stock_list')) or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d{6}', str(r.get('code') or '')):
            tags = ((r.get('tag') or {}).get('concept_tag')) or []
            pct = r.get('rise_and_fall')
            out.append({'title': '%s（%s）' % (r['name'], r['code']), 'url': 'https://stockpage.10jqka.com.cn/%s/' % r['code'], 'heat': ('热度 ' + _heat(r.get('rate'))) if r.get('rate') else '',
                        'note': ' · '.join(x for x in (('%+.2f%%' % pct) if isinstance(pct, (int, float)) else '', '、'.join(str(t) for t in tags[:3])) if x)})
    return out


def p_weread(raw):
    """WeRead's lists (rising books, novels)."""
    out = []
    for r in _json(raw).get('books') or []:
        b = r.get('bookInfo') if isinstance(r, dict) else None
        if not isinstance(b, dict) or not b.get('title'):
            continue
        link = str(b.get('deepLink') or b.get('readerLink') or '')
        if not link.startswith('https://weread.qq.com/'):
            link = 'https://weread.qq.com/web/search/books?keyword=' + urllib.parse.quote(b['title'])
        rating = b.get('newRating')
        out.append({'title': b['title'], 'url': link, 'heat': ('在读 ' + _heat(r.get('readingCount'))) if r.get('readingCount') else '',
                    'note': ' · '.join(x for x in (b.get('author') or '', ('推荐值 %.1f%%' % (rating / 10)) if isinstance(rating, (int, float)) and rating > 0 else '') if x)})
    return out


def p_openlibrary(raw):
    out = []
    for r in _json(raw).get('works') or []:
        if isinstance(r, dict) and r.get('title') and re.fullmatch(r'/works/OL\d+W', str(r.get('key') or '')):
            out.append({'title': r['title'], 'url': 'https://openlibrary.org' + r['key'], 'heat': '', 'note': ' · '.join(x for x in ((r.get('author_name') or [''])[0], str(r.get('first_publish_year') or '')) if x)})
    return out


def p_gutendex(raw):
    out = []
    for r in _json(raw).get('results') or []:
        if isinstance(r, dict) and r.get('title') and re.fullmatch(r'\d+', str(r.get('id') or '')):
            who = ((r.get('authors') or [{}])[0] or {}).get('name', '')
            if ', ' in who:                                                            # "Austen, Jane" -> "Jane Austen"
                a, b = who.split(', ', 1)
                who = '%s %s' % (b, a)
            out.append({'title': r['title'], 'url': 'https://www.gutenberg.org/ebooks/%s' % r['id'], 'heat': '下载 ' + _n(r.get('download_count')), 'note': who})
    return out


def _price(cents, cur):
    try:
        v = float(cents) / 100
    except (TypeError, ValueError):
        return ''
    if v == 0:
        return '免费'
    s = ('%d' % v) if v == int(v) else ('%.2f' % v)
    return ('¥' + s) if cur == 'CNY' else ('%s %s' % (cur, s) if cur else s)


def p_steam(raw):
    out = []
    for r in ((_json(raw).get('top_sellers')) or {}).get('items') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d+', str(r.get('id') or '')):
            off = r.get('discount_percent')
            out.append({'title': r['name'], 'url': 'https://store.steampowered.com/app/%s/' % r['id'], 'heat': _price(r.get('final_price'), r.get('currency')), 'note': ('-%s%%' % off) if off else ''})
    return out


def p_youtube(raw):
    out = []
    for r in _json(raw).get('items') or []:
        s = r.get('snippet') if isinstance(r, dict) else None
        if isinstance(s, dict) and s.get('title') and re.fullmatch(r'[\w-]{11}', str(r.get('id') or '')):
            out.append({'title': s['title'], 'url': 'https://www.youtube.com/watch?v=%s' % r['id'], 'heat': ('观看 ' + _heat((r.get('statistics') or {}).get('viewCount'))) if (r.get('statistics') or {}).get('viewCount') else '', 'note': s.get('channelTitle') or ''})
    return out


def p_coingecko(raw):
    out = []
    for c in _json(raw).get('coins') or []:
        r = c.get('item') if isinstance(c, dict) else None
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'[\w.-]+', str(r.get('id') or '')):
            out.append({'title': '%s（%s）' % (r['name'], r.get('symbol') or ''), 'url': 'https://www.coingecko.com/en/coins/%s' % r['id'], 'heat': ('市值排名 #%s' % r['market_cap_rank']) if r.get('market_cap_rank') else '', 'note': ''})
    return out


def wiki(lang):
    skip = re.compile(r'^(Main_Page|Special:|Wikipedia:|Portal:|Help:|File:|Category:|Template:|Talk:|User:|特殊:|维基百科:|Wikipedia_talk:|-$)', re.I)

    def parse(raw):
        arts = ((_json(raw).get('items') or [{}])[0] or {}).get('articles') or []
        return [{'title': a['article'].replace('_', ' '), 'url': 'https://%s.wikipedia.org/wiki/%s' % (lang, urllib.parse.quote(a['article'], safe="_(),:'!*~-.")), 'heat': '浏览 ' + _heat(a.get('views')), 'note': ''}
                for a in arts if isinstance(a, dict) and a.get('article') and not skip.match(a['article'])]
    return parse


def p_mastodon(raw):
    data = _json(raw)
    out = []
    for r in (data if isinstance(data, list) else []):
        if isinstance(r, dict) and r.get('title') and str(r.get('url') or '').startswith('https://'):
            people = 0
            for h in (r.get('history') or [])[:2]:
                try:
                    people += int(h.get('accounts') or 0)
                except (TypeError, ValueError, AttributeError):
                    pass
            out.append({'title': r['title'], 'url': r['url'], 'heat': ('%s 人分享' % _n(people)) if people else '', 'note': r.get('provider_name') or ''})
    return out


def p_hf_models(raw):
    data = _json(raw)
    out = []
    for d in (data if isinstance(data, list) else []):
        if isinstance(d, dict) and re.fullmatch(r'[\w.-]+/[\w.-]+', str(d.get('id') or '')) and not d.get('private'):
            out.append({'title': d['id'], 'url': 'https://huggingface.co/' + d['id'], 'heat': '♥ %s' % _n(d.get('likes')), 'note': ' · '.join(x for x in (d.get('pipeline_tag') or '', ('下载 ' + _heat(d['downloads'])) if d.get('downloads') else '') if x)})
    return out


def p_hf_papers(raw):
    data = _json(raw)
    out = []
    for r in (data if isinstance(data, list) else []):
        p = r.get('paper') if isinstance(r, dict) else None
        if isinstance(p, dict) and p.get('title') and re.fullmatch(r'\d{4}\.\d{4,6}', str(p.get('id') or '')):
            out.append({'title': p['title'], 'url': 'https://huggingface.co/papers/' + p['id'], 'heat': '▲ %s' % _n(p.get('upvotes')), 'note': (r.get('organization') or {}).get('fullname', '') if isinstance(r.get('organization'), dict) else ''})
    return out


def p_gtrends(raw):
    """Google Trends' "trending now" feed of a country."""
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise _changed()
    try:
        root = ET.fromstring(raw.lstrip('﻿ \t\r\n').encode('utf-8'))
    except ET.ParseError:
        raise _changed()
    out = []
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        if not title:
            continue
        news = it.find('{*}news_item')
        link = (news.findtext('{*}news_item_url') if news is not None else '') or ''
        out.append({'title': title, 'url': link if link.startswith('https://') else 'https://www.google.com/search?q=' + urllib.parse.quote(title),
                    'heat': ('搜索 ' + (it.findtext('{*}approx_traffic') or '').strip()) if (it.findtext('{*}approx_traffic') or '').strip() else '', 'note': _text(news.findtext('{*}news_item_title'), 100) if news is not None else ''})
    return out


def tophub(hosts=None):
    """A list on tophub.today (its page is a plain table): the first link of each row is the item, the cell next to it the heat."""
    def parse(raw):
        out, seen = [], set()
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', raw, re.S):
            a = re.search(r'<a\s[^>]*href="(https?://[^"]+)"[^>]*>([^<]{2,300})</a>', tr)
            if not a:
                continue
            url = html.unescape(a.group(1))
            host = urllib.parse.urlsplit(url).hostname or ''
            if hosts and not any(host == h or host.endswith('.' + h) for h in hosts):
                continue
            if url.startswith('http://'):
                url = 'https://' + url[7:]
            if url in seen:
                continue
            seen.add(url)
            heat = re.search(r'<td[^>]*class="ws"[^>]*>([^<]*)</td>', tr)
            note = re.search(r'<div class="item-desc">(.*?)</div>', tr, re.S)
            out.append({'title': _text(a.group(2), 200), 'url': url, 'heat': ('热度 ' + _text(heat.group(1), 20)) if heat and _text(heat.group(1)) else '', 'note': _text(note.group(1), 80) if note else ''})
        if not out and 'tophub' not in raw.lower():
            raise _changed()
        return out
    return parse


def p_xiaohongshu(raw):
    """Xiaohongshu's hot notes, as rebang.today shows them (the title is the label of the cover's link)."""
    out, seen = [], set()
    for tag in re.findall(r'<a\b[^>]*>', raw):
        if 'xiaohongshu.com/explore/' not in tag:
            continue
        h = re.search(r'href="(https://www\.xiaohongshu\.com/explore/[0-9a-f]+[^"]*)"', tag)
        l = re.search(r'aria-label="打开图片：([^"]+)"', tag)
        if h and l:
            url = html.unescape(h.group(1))
            if url not in seen:
                seen.add(url)
                out.append({'title': html.unescape(l.group(1)), 'url': url, 'heat': '', 'note': ''})
    if not out and 'rebang' not in raw.lower():
        raise _changed()
    return out


TOPHUB = 'https://tophub.today/n/'


def P(col, name, name_en, home, url, parse=None, lang='zh', headers=None, **kw):
    """A platform. `feed`: the address is an RSS / Atom feed (a list in time order, with dates). `default`: on unless the owner turns it off. `needs`: environment variables that must be set.
    `via`: where the list really comes from, when that is not the platform. `read`: what the entries point to are articles the page may read in its reader (news, blogs); the others open the platform's own page."""
    return {'col': col, 'name': name, 'name_en': name_en, 'home': home, 'url': url, 'parse': parse, 'lang': lang, 'headers': headers or {}, 'default': True, 'ranked': parse is not None, 'feed': parse is None, 'read': parse is None, **kw}


def TH(col, name, name_en, home, node, hosts, **kw):
    """A list of tophub.today (its page of that list is a plain table): only links to `hosts` are taken."""
    return P(col, name, name_en, home, TOPHUB + node, tophub(hosts), via='tophub.today', hosts=hosts, ttl=THIRD_SECONDS, **kw)


# The countries a list of a country has (YouTube's regions; Google Trends' common ones). China is not among YouTube's.
YT_REGIONS = tuple('DZ AR AU AT AZ BH BY BE BO BA BR BG CA CL CO CR HR CY CZ DK DO EC EG SV EE FI FR GE DE GH GR GT HN HK HU IS IN ID IQ IE IL IT JM JP JO KZ KE KW LV LB LY LI LT LU MY MT MX ME MA NP NL NZ NI NG MK NO OM PK PA PG PY PE PH PL PT PR QA RO RU SA SN RS SG SK SI ZA KR ES LK SE CH TW TZ TH TN TR UG UA AE GB US UY VE VN YE ZW'.split())
GT_REGIONS = tuple('US GB JP DE FR IN BR CA AU IT ES MX KR TW HK SG NL SE NO DK FI PL TR RU ID TH VN MY PH AR CL CO ZA EG SA AE IL NZ IE CH AT BE CZ PT GR HU RO UA'.split())
LANG_REGION = {'ja': 'JP', 'ko': 'KR', 'de': 'DE', 'fr': 'FR', 'es': 'ES', 'it': 'IT', 'pt': 'BR', 'ru': 'RU', 'nl': 'NL', 'pl': 'PL', 'tr': 'TR', 'th': 'TH', 'vi': 'VN', 'id': 'ID', 'sv': 'SE', 'da': 'DK',
               'fi': 'FI', 'nb': 'NO', 'no': 'NO', 'cs': 'CZ', 'el': 'GR', 'hu': 'HU', 'ro': 'RO', 'uk': 'UA', 'he': 'IL', 'ar': 'SA', 'zh-hant': 'TW', 'zh-tw': 'TW', 'zh-hk': 'HK'}


def pick_region(spec, env, asked='', headers=None):
    """-> the country of a list that has countries ('' for one that has none): the one asked for, else (`auto`) the country of the reader's address, of their browser's language, the owner's, the list's."""
    regs = spec.get('regions')
    if not regs:
        return ''
    asked = str(asked or '').strip().upper()
    if asked in regs:
        return asked
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    ip = (h.get('x-vercel-ip-country') or h.get('cf-ipcountry') or '').strip().upper()
    if ip in regs:
        return ip
    for part in (h.get('accept-language') or '').split(',')[:8]:                    # in the order the browser lists them
        tag = part.split(';')[0].strip().lower()
        m = re.fullmatch(r'[a-z]{2,3}-([a-z]{2})', tag)
        guess = m.group(1).upper() if m else ''
        if guess not in regs:
            guess = LANG_REGION.get(tag) or LANG_REGION.get(tag.split('-')[0]) or ''
        if guess in regs:
            return guess
    own = str((env or {}).get(spec.get('region_env') or '') or '').strip().upper()
    return own if own in regs else spec['region']


GT = 'https://trends.google.com/trending/rss?geo='
WIKI = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/%s.wikipedia/all-access/{DATE}'
APPLE = 'https://rss.applemarketingtools.com/api/v2/%s'
# id -> what it is; the order is the order of the tabs of a column.
PLATFORMS = {
    # ------------------------------------------------------------ 综合热点
    'weibo': P('news', '微博热搜', 'Weibo hot searches', 'https://s.weibo.com/top/summary', 'https://weibo.com/ajax/side/hotSearch', p_weibo, headers={'Referer': 'https://weibo.com/'}),
    'zhihu': P('news', '知乎热榜', 'Zhihu hot questions', 'https://www.zhihu.com/hot', 'https://api.zhihu.com/topstory/hot-lists/total?limit=50', p_zhihu),
    'douyin': P('news', '抖音热点', 'Douyin hot topics', 'https://www.douyin.com/hot', 'https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1', p_douyin, headers={'Referer': 'https://www.douyin.com/'}),
    'xiaohongshu': P('news', '小红书热门', 'Xiaohongshu hot notes', 'https://www.xiaohongshu.com/explore', 'https://rebang.today/?tab=xiaohongshu', p_xiaohongshu, via='rebang.today', ttl=THIRD_SECONDS),
    'bilibili': P('news', 'B 站热门视频', 'Bilibili popular videos', 'https://www.bilibili.com/v/popular/all', 'https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1', p_bilibili, headers={'Referer': 'https://www.bilibili.com/'}),
    'wechat': TH('news', '公众号热文', 'WeChat articles', 'https://weixin.sogou.com/', 'WnBe01o371', ('weixin.qq.com',)),
    'toutiao': P('news', '今日头条热榜', 'Toutiao hot board', 'https://www.toutiao.com/', 'https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc', p_toutiao),
    'hupu': TH('news', '虎扑步行街热帖', 'Hupu hot posts', 'https://bbs.hupu.com/', 'G47o8weMmN', ('hupu.com',)),
    'zhihu_daily': P('news', '知乎日报', 'Zhihu Daily', 'https://daily.zhihu.com/', 'https://news-at.zhihu.com/api/4/news/latest', p_zhihu_daily, ranked=False, read=True),
    # ------------------------------------------------------------ 科技与开发
    'github': P('tech', 'GitHub Trending（今日）', 'GitHub Trending (today)', 'https://github.com/trending', 'https://github.com/trending?since=daily', p_github, lang='en'),
    'juejin': P('tech', '掘金热榜', 'Juejin hot articles', 'https://juejin.cn/hot/articles', 'https://api.juejin.cn/content_api/v1/content/article_rank?category_id=1&type=hot', p_juejin),
    'v2ex': P('tech', 'V2EX 今日热议', 'V2EX hot topics', 'https://www.v2ex.com/', 'https://www.v2ex.com/api/topics/hot.json', p_v2ex),
    'hackernews': P('tech', 'Hacker News', 'Hacker News', 'https://news.ycombinator.com/', 'https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50', p_hn, lang='en'),
    'lobsters': P('tech', 'Lobsters', 'Lobsters', 'https://lobste.rs/', 'https://lobste.rs/hottest.json', p_lobsters, lang='en'),
    'devto': P('tech', 'DEV 社区（本周）', 'DEV Community (top)', 'https://dev.to/top/week', 'https://dev.to/api/articles?top=7&per_page=50', p_devto, lang='en', read=True),
    'producthunt': P('tech', 'Product Hunt', 'Product Hunt', 'https://www.producthunt.com/', 'https://www.producthunt.com/feed', lang='en', read=False),
    'sspai': P('tech', '少数派', 'sspai', 'https://sspai.com/', 'https://sspai.com/feed'),
    'geekpark': P('tech', '极客公园', 'GeekPark', 'https://www.geekpark.net/', 'https://www.geekpark.net/rss'),
    'ithome': P('tech', 'IT 之家', 'ITHome', 'https://www.ithome.com/', 'https://www.ithome.com/rss/'),
    'cnblogs': P('tech', '博客园', 'cnblogs', 'https://www.cnblogs.com/', 'https://feed.cnblogs.com/blog/sitehome/rss'),
    'kr36': TH('tech', '36氪 24 小时热榜', '36Kr 24-hour hot list', 'https://36kr.com/hot-list/catalog', 'Q1Vd5Ko85R', ('36kr.com',)),
    # ------------------------------------------------------------ 财经
    'xueqiu': TH('money', '雪球热帖', 'Xueqiu hot posts', 'https://xueqiu.com/', 'X12owXzvNV', ('xueqiu.com',)),
    'ths': P('money', '同花顺热股', 'Tonghuashun hot stocks', 'https://www.10jqka.com.cn/', 'https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal', p_ths),
    'wallstreetcn': P('money', '华尔街见闻', 'WallStreetCN', 'https://wallstreetcn.com/', 'https://dedicated.wallstreetcn.com/rss.xml'),
    'cnbc': P('money', 'CNBC', 'CNBC', 'https://www.cnbc.com/', 'https://www.cnbc.com/id/100003114/device/rss/rss.html', lang='en'),
    'bloomberg': P('money', 'Bloomberg Markets', 'Bloomberg Markets', 'https://www.bloomberg.com/markets', 'https://feeds.bloomberg.com/markets/news.rss', lang='en'),
    'economist': P('money', '经济学人 · 金融与经济', 'The Economist · Finance & economics', 'https://www.economist.com/finance-and-economics', 'https://www.economist.com/finance-and-economics/rss.xml', lang='en', read=False),
    'coingecko': P('money', '加密货币热搜（CoinGecko）', 'Trending coins (CoinGecko)', 'https://www.coingecko.com/en/highlights/trending-crypto', 'https://api.coingecko.com/api/v3/search/trending', p_coingecko, lang='en'),
    # ------------------------------------------------------------ 国际
    'gtrends': P('world', 'Google 热搜', 'Google Trends', 'https://trends.google.com/trending', GT + '{REGION}', p_gtrends, lang='en', regions=GT_REGIONS, region='US'),
    'wikipedia': P('world', '维基百科阅读榜（英文）', 'Wikipedia most read (English)', 'https://en.wikipedia.org/wiki/Wikipedia:Top_25_Report', WIKI % 'en', wiki('en'), lang='en', read=True),
    'wikipedia_zh': P('world', '维基百科阅读榜（中文）', 'Wikipedia most read (Chinese)', 'https://zh.wikipedia.org/', WIKI % 'zh', wiki('zh'), read=True),
    'bbc': P('world', 'BBC 国际', 'BBC World', 'https://www.bbc.com/news/world', 'https://feeds.bbci.co.uk/news/world/rss.xml', lang='en'),
    'bbc_zh': P('world', 'BBC 中文', 'BBC Chinese', 'https://www.bbc.com/zhongwen/simp', 'https://feeds.bbci.co.uk/zhongwen/simp/rss.xml'),
    'rfi_zh': P('world', '法广中文', 'RFI Chinese', 'https://www.rfi.fr/cn/', 'https://www.rfi.fr/cn/rss'),
    'nyt_world': P('world', '纽约时报 · 国际', 'The New York Times · World', 'https://www.nytimes.com/section/world', 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml', lang='en', read=False),
    'nyt_cn': P('world', '纽约时报中文网', 'The New York Times (Chinese)', 'https://cn.nytimes.com/', 'https://cn.nytimes.com/rss/', read=False),
    'aljazeera': P('world', '半岛电视台', 'Al Jazeera', 'https://www.aljazeera.com/', 'https://www.aljazeera.com/xml/rss/all.xml', lang='en'),
    'guardian': P('world', '卫报 · 国际', 'The Guardian · World', 'https://www.theguardian.com/world', 'https://www.theguardian.com/world/rss', lang='en'),
    'mastodon': P('world', 'Mastodon 热门链接', 'Mastodon trending links', 'https://mastodon.social/explore/links', 'https://mastodon.social/api/v1/trends/links?limit=40', p_mastodon, lang='en'),
    # ------------------------------------------------------------ AI 与学术
    'hf_papers': P('ai', 'Hugging Face 每日论文', 'Hugging Face daily papers', 'https://huggingface.co/papers', 'https://huggingface.co/api/daily_papers?limit=50', p_hf_papers, lang='en'),
    'hf_models': P('ai', 'Hugging Face 热门模型', 'Hugging Face trending models', 'https://huggingface.co/models?sort=trending', 'https://huggingface.co/api/models?sort=trendingScore&limit=50', p_hf_models, lang='en'),
    'arxiv_ai': P('ai', 'arXiv · cs.AI', 'arXiv · cs.AI', 'https://arxiv.org/list/cs.AI/new', 'https://rss.arxiv.org/rss/cs.AI', lang='en'),
    'arxiv_cl': P('ai', 'arXiv · cs.CL', 'arXiv · cs.CL', 'https://arxiv.org/list/cs.CL/new', 'https://rss.arxiv.org/rss/cs.CL', lang='en'),
    'openai': P('ai', 'OpenAI 动态', 'OpenAI news', 'https://openai.com/news/', 'https://openai.com/news/rss.xml', lang='en'),
    'mitnews': P('ai', 'MIT 新闻', 'MIT News', 'https://news.mit.edu/', 'https://news.mit.edu/rss/feed', lang='en'),
    'qbitai': P('ai', '量子位', 'QbitAI', 'https://www.qbitai.com/', 'https://www.qbitai.com/feed'),
    'mit_ai': P('ai', 'MIT Technology Review · AI', 'MIT Technology Review · AI', 'https://www.technologyreview.com/topic/artificial-intelligence/', 'https://www.technologyreview.com/topic/artificial-intelligence/feed', lang='en'),
    'techcrunch_ai': P('ai', 'TechCrunch · AI', 'TechCrunch · AI', 'https://techcrunch.com/category/artificial-intelligence/', 'https://techcrunch.com/category/artificial-intelligence/feed/', lang='en'),
    'nature': P('ai', 'Nature 新闻', 'Nature', 'https://www.nature.com/nature/articles?type=news', 'https://www.nature.com/nature.rss', lang='en'),
    'science_news': P('ai', 'Science 新闻', 'Science news', 'https://www.science.org/news', 'https://www.science.org/rss/news_current.xml', lang='en'),
    'physorg': P('ai', 'Phys.org', 'Phys.org', 'https://phys.org/', 'https://phys.org/rss-feed/', lang='en'),
    # ------------------------------------------------------------ 读书与影音
    'weread_rising': P('play', '微信读书 · 飙升榜', 'WeRead · rising books', 'https://weread.qq.com/web/category/rising', 'https://weread.qq.com/web/bookListInCategory/rising?rank=1', p_weread, headers={'Referer': 'https://weread.qq.com/'}),
    'weread_novel': P('play', '微信读书 · 小说榜', 'WeRead · novels', 'https://weread.qq.com/web/category/100000', 'https://weread.qq.com/web/bookListInCategory/100000?maxIndex=0', p_weread, headers={'Referer': 'https://weread.qq.com/'}),
    'qidian': TH('play', '起点中文网热榜', 'Qidian novels', 'https://www.qidian.com/rank/', 'VaobmGneAj', ('qidian.com',)),
    'qidian_f': TH('play', '起点女生网热榜', 'Qidian Women novels', 'https://www.qdmm.com/rank/', 'DgeygBwvZq', ('qdmm.com', 'qidian.com')),
    'zongheng': TH('play', '纵横中文网热榜', 'Zongheng novels', 'https://www.zongheng.com/rank/', 'b0vmYyJvB1', ('zongheng.com',)),
    'tuishujun': TH('play', '推书君网文榜', 'Tuishujun novels', 'https://www.tuishujun.com/', '47o8QGjvMm', ('tuishujun.com',)),
    'openlibrary': P('play', 'Open Library 近期热门', 'Open Library trending', 'https://openlibrary.org/trending/daily', 'https://openlibrary.org/trending/daily.json?limit=50', p_openlibrary, lang='en'),
    'gutenberg': P('play', 'Project Gutenberg 热门公版书', 'Project Gutenberg popular books', 'https://www.gutenberg.org/browse/scores/top', 'https://gutendex.com/books/?sort=popular&languages=en', p_gutendex, lang='en'),
    'netease': P('play', '网易云音乐飙升榜', 'NetEase Cloud Music: rising songs', 'https://music.163.com/#/discover/toplist?id=19723756', 'https://music.163.com/api/playlist/detail?id=19723756', p_netease),
    'qqmusic': P('play', 'QQ 音乐飙升榜', 'QQ Music: rising songs', 'https://y.qq.com/n/ryqq/toplist/62', 'https://u.y.qq.com/cgi-bin/musicu.fcg?data=%7B%22detail%22%3A%7B%22module%22%3A%22musicToplist.ToplistInfoServer%22%2C%22method%22%3A%22GetDetail%22%2C%22param%22%3A%7B%22topId%22%3A62%2C%22offset%22%3A0%2C%22num%22%3A50%7D%7D%7D', p_qqmusic, headers={'Referer': 'https://y.qq.com/'}),
    'apple_pod_cn': P('play', 'Apple 播客榜 · 中国', 'Apple Podcasts · China', 'https://podcasts.apple.com/cn/charts', APPLE % 'cn/podcasts/top/50/podcasts.json', p_apple),
    'apple_pod_us': P('play', 'Apple 播客榜 · 美国', 'Apple Podcasts · US', 'https://podcasts.apple.com/us/charts', APPLE % 'us/podcasts/top/50/podcasts.json', p_apple, lang='en'),
    'apple_music_us': P('play', 'Apple Music 最多播放 · 美国', 'Apple Music most played · US', 'https://music.apple.com/us/new', APPLE % 'us/music/most-played/50/songs.json', p_apple, lang='en'),
    'steam': P('play', 'Steam 热销', 'Steam top sellers', 'https://store.steampowered.com/search/?filter=topsellers', 'https://store.steampowered.com/api/featuredcategories?cc=cn&l=schinese', p_steam),
    'taptap': TH('play', 'TapTap 热门游戏', 'TapTap hot games', 'https://www.taptap.cn/top/download', '6ARe1k2v7n', ('taptap.cn', 'taptap.io')),
    'douban_movie': TH('play', '豆瓣新片榜', 'Douban new films', 'https://movie.douban.com/chart', 'mDOvnyBoEB', ('douban.com',)),
    'youtube': P('play', 'YouTube 热门', 'YouTube most popular', 'https://www.youtube.com/feed/trending',
                 'https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&maxResults=50&regionCode={REGION}&key={YOUTUBE_API_KEY}', p_youtube, lang='en', needs=['YOUTUBE_API_KEY'], regions=YT_REGIONS, region='US', region_env='YOUTUBE_REGION'),
}


# ---------------------------------------------------------------- the owner's own lists: registry/hot.json
def custom_path():
    return os.path.join(os.path.dirname(agg.reg_path()), 'hot.json')


def custom(env):
    """-> {id: platform} for what the owner added in registry/hot.json:
    {"platforms": [{"id": "x-ai", "name": "...", "col": "ai", "feed": "https://..."}, {"id": "...", "rsshub": "/zhihu/hot", "ranked": true}, {"id": "...", "tophub": "WnBe01o371"}]}.
    `rsshub` needs RSSHUB_BASE (the owner's RSSHub, https). A built-in id cannot be replaced. A broken entry is skipped."""
    cfg = agg.read_json(custom_path(), {})
    base = str((env or {}).get('RSSHUB_BASE') or '').strip().rstrip('/')
    out = {}
    for c in (cfg.get('platforms') if isinstance(cfg, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        pid, name = str(c.get('id') or ''), agg.clean(c.get('name'), 40)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,23}', pid) or pid in PLATFORMS or pid in out or not name:
            continue
        col = c.get('col') if c.get('col') in COLUMN_IDS else 'news'
        kw = dict(lang='en' if c.get('lang') == 'en' else 'zh', custom=True)
        if c.get('tophub') and re.fullmatch(r'[A-Za-z0-9]{6,16}', str(c['tophub'])):
            spec = P(col, name, agg.clean(c.get('name_en'), 40) or name, c.get('home') or TOPHUB + c['tophub'], TOPHUB + c['tophub'], tophub(None), via='tophub.today', ttl=THIRD_SECONDS, **kw)
        elif c.get('rsshub') and str(c['rsshub']).startswith('/') and base.startswith(WEB):
            spec = P(col, name, agg.clean(c.get('name_en'), 40) or name, c.get('home') or base, base + str(c['rsshub']), via='RSSHub', **kw)
        elif str(c.get('feed') or '').startswith(WEB):
            spec = P(col, name, agg.clean(c.get('name_en'), 40) or name, c.get('home') or c['feed'], c['feed'], **kw)
        else:
            continue                                                                  # (what is fetched later is https only, unless the tests allow local addresses)
        if not str(spec['home']).startswith(WEB):
            spec['home'] = spec['url']
        spec['ranked'] = bool(c.get('ranked')) if spec['feed'] else True
        spec['read'] = bool(c['read']) if 'read' in c else (spec['feed'] and not spec['ranked'])         # a list in time order points at articles; a ranking (RSSHub's hot lists) at the platform's pages
        out[pid] = spec
    return out


def platforms(env):
    return {**PLATFORMS, **custom(env)}


def enabled(env, plats=None):
    """The platforms that are on: the defaults, plus HOT_ENABLE (a list, or `all`), minus HOT_DISABLE, and only the ones whose keys are set."""
    plats = platforms(env) if plats is None else plats
    on = {k for k, v in plats.items() if v['default']}
    add = {x.strip() for x in str((env or {}).get('HOT_ENABLE') or '').lower().split(',') if x.strip()}
    off = {x.strip() for x in str((env or {}).get('HOT_DISABLE') or '').lower().split(',') if x.strip()}
    on |= set(plats) if 'all' in add else (add & set(plats))
    return [k for k, v in plats.items() if k in on and k not in off and all(str((env or {}).get(n) or '').strip() for n in v.get('needs') or [])]


def _url(spec, env, now, region=''):
    """The address, with what changes filled in: the day of Wikipedia's list, YouTube's key, the country of a list that has countries."""
    u = spec['url']
    if '{' not in u:
        return u
    vals = {'DATE': time.strftime('%Y/%m/%d', time.gmtime(now - 2 * 86400)), 'YOUTUBE_API_KEY': urllib.parse.quote(str((env or {}).get('YOUTUBE_API_KEY') or '').strip(), safe=''),
            'REGION': region if re.fullmatch(r'[A-Z]{2}', region or '') else spec.get('region', 'US')}
    for k, v in vals.items():
        u = u.replace('{%s}' % k, v)
    return u


def feed_rows(body, headers, url):
    _, items = agg.parse_feed(body, headers, url)
    return [{'title': i.get('title'), 'url': i.get('url'), 'heat': '', 'note': '', 'date': i.get('date') or ''} for i in items]


def entries(pid, now=None, env=None, region=''):
    """-> (the platform's rows now, when they were read). A copy is kept for CACHE_SECONDS (for each country of a list that has countries); an old copy is served when the platform does not answer.
    Raises agg.Refused."""
    now = time.time() if now is None else now
    spec = platforms(env)[pid]
    key = '%s:%s' % (pid, region) if spec.get('regions') else pid
    hit = _CACHE.get(key)
    if hit and now - hit[0] < spec.get('ttl', CACHE_SECONDS):
        return hit[1], hit[0]
    try:
        final, h, body = agg.fetch(_url(spec, env, now, region), FEED_ACCEPT if spec['feed'] else '*/*', 3 * 1024 * 1024, headers={**BROWSER, **spec['headers']}, truncate=True)
        try:
            rows = feed_rows(body, h, final) if spec['feed'] else spec['parse'](agg.decode(body, h))
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, ET.ParseError):
            raise _changed()
        rows = [r for r in map(_norm, rows) if r][:KEEP]
        if not rows:
            raise agg.Refused('榜单现在是空的', 'The list is empty right now')
    except agg.Refused:
        if hit and now - hit[0] < STALE_SECONDS:
            return hit[1], hit[0]
        raise
    _CACHE[key] = (now, rows)
    return rows, now


def rss(pid, spec, rows, now=None, region=''):
    title = '%s · %s' % (spec['name'], region) if region else spec['name']
    stamp = formatdate(now if now is not None else time.time(), usegmt=True)
    items = []
    for i, r in enumerate(rows, 1):
        bits = (['第 %d 名' % i] if spec['ranked'] else [r['date']]) + [x for x in (r['heat'], r['note']) if x]
        items.append('<item><title>%s</title><link>%s</link><guid isPermaLink="false">hot:%s:%s</guid><pubDate>%s</pubDate><description>%s</description></item>'
                     % (escape(r['title']), escape(r['url']), pid, hashlib.sha1(r['url'].encode('utf-8')).hexdigest()[:12], stamp, escape(' · '.join(x for x in bits if x))))
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>%s</title><link>%s</link><description>%s</description><language>%s</language><lastBuildDate>%s</lastBuildDate>%s</channel></rss>'
            % (escape(title), escape(spec['home']), escape('%s：平台当前的榜单（排名、标题、热度和平台自己的链接），由知树阅读按需生成，不保存任何内容。' % title), 'zh-CN' if spec['lang'] == 'zh' else 'en', stamp, ''.join(items)))


def _info(k, v):
    d = {'id': k, 'name': v['name'], 'name_en': v['name_en'], 'home': v['home'], 'lang': v['lang'], 'col': v['col'], 'ranked': v['ranked']}
    if v.get('via'):
        d['via'] = v['via']
    if v.get('read'):
        d['read'] = True
    if v.get('regions'):
        d['regions'] = list(v['regions'])
    return d


def handle(query, env, now=None, headers=None):
    """-> (status, content type, body bytes, cache header). `query`: the part after "?" (a dict of lists, as parse_qs makes it); `headers`: the request's, for the country of a list that has countries."""
    plats = platforms(env)
    on = enabled(env, plats)
    pid = (query.get('p') or [''])[0]
    if not pid:                                                                # what is on
        cols = [c for c in COLUMNS if any(plats[k]['col'] == c['id'] for k in on)]
        body = json.dumps({'ok': True, 'service': 'shelf-hot', 'columns': cols, 'platforms': [_info(k, plats[k]) for k in on]}, ensure_ascii=False).encode('utf-8')
        return 200, 'application/json; charset=utf-8', body, 'public, max-age=300'
    if pid not in plats or pid not in on:
        return 404, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'platform', 'message': '没有这个榜单', 'message_en': 'No such list'}).encode('utf-8'), 'no-store'
    try:
        n = max(1, min(MAX_COUNT, int((query.get('n') or [DEFAULT_COUNT])[0])))
    except ValueError:
        n = DEFAULT_COUNT
    asked = (query.get('region') or [''])[0]
    region = pick_region(plats[pid], env, asked, headers)
    mine = bool(region) and asked.strip().upper() != region                  # the answer depends on who asks (the address, the language): the CDN must not give it to somebody else
    try:
        rows, at = entries(pid, now, env, region)
    except agg.Refused as e:
        return 502, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'upstream', 'message': '这个榜单现在读不到：%s' % e, 'message_en': 'This list cannot be read right now: %s' % e.en}, ensure_ascii=False).encode('utf-8'), 'no-store'
    rows = rows[:n]
    if (query.get('format') or [''])[0] == 'json':
        return (200, 'application/json; charset=utf-8', json.dumps({'ok': True, 'platform': pid, **_info(pid, plats[pid]), **({'region': region} if region else {}), 'updated': int(at), 'items': rows}, ensure_ascii=False).encode('utf-8'),
                PRIVATE if mine else CDN)
    return 200, 'application/rss+xml; charset=utf-8', rss(pid, plats[pid], rows, now, region).encode('utf-8'), PRIVATE if mine else CDN


def respond(h, env, method):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/hot.py and by the tests)."""
    if method != 'GET':
        h.send_response(405)
        h.send_header('Allow', 'GET')
        h.send_header('Content-Length', '0')
        h.end_headers()
        return
    status, ctype, body, cache = handle(urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query), env, headers=getattr(h, 'headers', None))
    h.send_response(status)
    h.send_header('Content-Type', ctype)
    h.send_header('Cache-Control', cache)
    h.send_header('X-Robots-Tag', 'noindex')
    h.send_header('Access-Control-Allow-Origin', '*')          # public and read-only: any page may read what is on (the page shows only the lists that are)
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    h.wfile.write(body)
