"""Hot lists as feeds: GET /api/hot?p=github  ->  an RSS feed of what is on that platform's hot list right now (rank, title, heat, a link to the platform's own page).
GET /api/hot?p=github&format=json  ->  the same as JSON (what the hot-list page of the site reads); GET /api/hot  ->  the columns and the platforms that are on.

What a "hot list" site does, and so does this: it asks the platform for the same small JSON (or page) that the platform's own web page shows, with a browser's headers, takes the rank,
the title, the heat and the address, and hands that on. No article page is read or kept. Here the answer is an ordinary RSS feed, so a reader follows a hot list like any blog:
"subscribe" in the shelf, the new entries that came onto the list show up as new articles, a click opens the platform's page. The current list is cached briefly in memory and at the CDN.
When PostgreSQL and migration 007 are configured, one metadata-only ranking snapshot per platform/region/UTC day is retained for the real seven-day view; event tracking stores source links, titles and short feed descriptions, never full articles or copied images.

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
import hmac
import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate
from xml.sax.saxutils import escape

import aggregate as agg
import hotdetail
import translate

BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36', 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
CACHE_SECONDS = 180            # the platform is asked at most this often per warm instance
THIRD_SECONDS = 600            # a list that another site (tophub.today, rebang.today) collected is asked for less often: they limit what comes from one address
STALE_SECONDS = 3600           # when the platform does not answer, the last good copy up to this old is served
DEFAULT_COUNT, MAX_COUNT, KEEP = 30, 50, 100
CDN = 'public, max-age=300, s-maxage=600, stale-while-revalidate=3600'
PRIVATE = 'private, max-age=300'
FEED_ACCEPT = 'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8'
_CACHE = {}

# The columns of the hot-list page, in the order they are shown. `play` is
# accepted as a legacy custom-registry value below, but is never returned.
COLUMNS = [
    {'id': 'news', 'name': '综合热点', 'name_en': 'Top stories'},
    {'id': 'tech', 'name': '科技', 'name_en': 'Tech'},
    {'id': 'ai', 'name': 'AI 最新', 'name_en': 'Latest AI'},
    {'id': 'money', 'name': '金融财经', 'name_en': 'Finance'},
    {'id': 'world', 'name': '国际时事', 'name_en': 'World affairs'},
    {'id': 'entertainment', 'name': '娱乐', 'name_en': 'Entertainment'},
    {'id': 'learning', 'name': '学习与专业提升', 'name_en': 'Learning & growth'},
    {'id': 'media', 'name': '影音与社区', 'name_en': 'Media & communities'},
    {'id': 'shopping', 'name': '购物', 'name_en': 'Shopping', 'default': False},
]
COLUMN_IDS = [c['id'] for c in COLUMNS]
LEGACY_COLUMNS = {'play': 'media'}
WEB = ('http://', 'https://')
SNAPSHOT_DAYS = 7
EVENT_CACHE_SECONDS = 600
EVENT_RETENTION_DAYS = 7
EVENT_FETCH_TIMEOUT = 2.5
EVENT_REFRESH_LOCK_KEY = (871234, 7)

# These are durable event threads, not claims that an event is verified. Each
# refresh adds source-linked reports and old threads disappear from the public
# feed once their reports age out. The GDELT query is deliberately broad; the
# local keyword tests below assign only reports with a clear subject match.
EVENT_TOPICS = [
    {'id': 'iran-israel', 'title': '伊以局势', 'title_en': 'Iran–Israel tensions', 'category': 'world',
     'terms': ('iran', 'israel', 'iranian', 'israeli')},
    {'id': 'russia-ukraine', 'title': '俄乌战争', 'title_en': 'Russia–Ukraine war', 'category': 'world',
     'terms': ('ukraine', 'russia', 'russian', 'ukrainian')},
    {'id': 'gold-market', 'title': '黄金与贵金属', 'title_en': 'Gold and precious metals', 'category': 'money',
     'terms': ('gold', 'bullion', 'precious metal', 'gold price')},
    {'id': 'global-markets', 'title': '全球金融市场', 'title_en': 'Global financial markets', 'category': 'money',
     'terms': ('market', 'markets', 'stock', 'stocks', 'bond', 'bonds', 'inflation', 'central bank', 'economy')},
    {'id': 'ai-news', 'title': 'AI 最新动态', 'title_en': 'Latest AI developments', 'category': 'ai',
     'terms': ('artificial intelligence', 'generative ai', 'openai', 'anthropic', 'deepseek', 'ai model', 'ai models')},
]
EVENT_GDELT_QUERY = '(Iran OR Israel OR Ukraine OR Russia OR gold OR bullion OR markets OR inflation OR "artificial intelligence" OR OpenAI OR Anthropic OR DeepSeek)'


def _database_url(env):
    return str((env or {}).get('OPS_DATABASE_URL') or (env or {}).get('POSTGRES_URL') or (env or {}).get('DATABASE_URL') or '').strip()


class HotStore:
    """Small PostgreSQL store for ranking snapshots and event metadata only."""

    def __init__(self, url):
        import psycopg
        self.connect = lambda: psycopg.connect(url, connect_timeout=4, autocommit=True)

    def save_chart(self, pid, region, fetched_at, rows):
        snapshot_day = datetime.fromtimestamp(float(fetched_at), timezone.utc).date()
        with self.connect() as cx, cx.cursor() as cur:
            cur.execute('INSERT INTO hot.chart_snapshots (platform_id, region, snapshot_date, fetched_at, items) '
                        'VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (platform_id, region, snapshot_date) DO NOTHING',
                        (pid, region or '', snapshot_day, datetime.fromtimestamp(float(fetched_at), timezone.utc), json.dumps(rows, ensure_ascii=False, separators=(',', ':'))))
            return cur.rowcount > 0

    def chart_week(self, pid, region, now):
        today = datetime.fromtimestamp(float(now), timezone.utc).date()
        since = today.fromordinal(today.toordinal() - (SNAPSHOT_DAYS - 1))
        with self.connect() as cx, cx.cursor() as cur:
            cur.execute('SELECT snapshot_date, fetched_at, items FROM hot.chart_snapshots '
                        'WHERE platform_id=%s AND region=%s AND snapshot_date >= %s AND snapshot_date <= %s '
                        'ORDER BY snapshot_date ASC', (pid, region or '', since, today))
            snapshots = cur.fetchall()
        return _aggregate_week(snapshots)

    def latest_event_refresh(self):
        with self.connect() as cx, cx.cursor() as cur:
            cur.execute('SELECT refreshed_at FROM hot.event_refresh_state WHERE singleton=true')
            row = cur.fetchone()
        return row[0].timestamp() if row and row[0] else 0

    def try_event_refresh_lock(self):
        """Take a session lock so only one serverless instance refreshes events at once."""
        cx = self.connect()
        try:
            with cx.cursor() as cur:
                cur.execute('SELECT pg_try_advisory_lock(%s,%s)', EVENT_REFRESH_LOCK_KEY)
                locked = bool(cur.fetchone()[0])
            if locked:
                return cx
            cx.close()
            return None
        except Exception:
            cx.close()
            raise

    def release_event_refresh_lock(self, cx):
        if cx is None:
            return
        try:
            with cx.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(%s,%s)', EVENT_REFRESH_LOCK_KEY)
        finally:
            cx.close()

    def save_events(self, candidates, now):
        refreshed = datetime.fromtimestamp(float(now), timezone.utc)
        with self.connect() as cx, cx.cursor() as cur:
            for topic in EVENT_TOPICS:
                cur.execute('INSERT INTO hot.event_topics (id,title,title_en,category,updated_at) VALUES (%s,%s,%s,%s,%s) '
                            'ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,title_en=EXCLUDED.title_en,category=EXCLUDED.category',
                            (topic['id'], topic['title'], topic['title_en'], topic['category'], refreshed))
            total = 0
            for item in candidates:
                cur.execute('INSERT INTO hot.event_sources '
                            '(event_id,source_key,title,url,published_at,excerpt,first_seen_at,last_seen_at) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
                            'ON CONFLICT (event_id,url) DO UPDATE SET source_key=EXCLUDED.source_key,title=EXCLUDED.title,'
                            'published_at=COALESCE(EXCLUDED.published_at,hot.event_sources.published_at),'
                            'excerpt=CASE WHEN EXCLUDED.excerpt<>\'\' THEN EXCLUDED.excerpt ELSE hot.event_sources.excerpt END,last_seen_at=EXCLUDED.last_seen_at',
                            (item['event_id'], item['source'], item['title'], item['url'],
                             datetime.fromtimestamp(item['published_at'], timezone.utc) if item.get('published_at') else None,
                             item.get('excerpt') or '', refreshed, refreshed))
                cur.execute('UPDATE hot.event_topics SET archived_at=NULL,updated_at=%s WHERE id=%s', (refreshed, item['event_id']))
                total += 1
            cur.execute('UPDATE hot.event_topics t SET archived_at=%s,updated_at=%s WHERE t.archived_at IS NULL AND NOT EXISTS '
                        '(SELECT 1 FROM hot.event_sources s WHERE s.event_id=t.id AND COALESCE(s.published_at,s.last_seen_at) >= %s - (%s * interval \'1 day\'))',
                        (refreshed, refreshed, refreshed, EVENT_RETENTION_DAYS))
            cur.execute('INSERT INTO hot.event_refresh_state (singleton,refreshed_at,candidate_count) VALUES (true,%s,%s) '
                        'ON CONFLICT (singleton) DO UPDATE SET refreshed_at=EXCLUDED.refreshed_at,candidate_count=EXCLUDED.candidate_count', (refreshed, total))
        return total

    def list_events(self, lang, limit, now):
        cutoff = datetime.fromtimestamp(float(now), timezone.utc)
        rows = []
        with self.connect() as cx, cx.cursor() as cur:
            cur.execute('SELECT t.id,t.title,t.title_en,t.category,t.is_pinned,MAX(COALESCE(s.published_at,s.last_seen_at)) AS updated '
                        'FROM hot.event_topics t JOIN hot.event_sources s ON s.event_id=t.id '
                        'WHERE NOT t.is_ignored AND t.archived_at IS NULL AND COALESCE(s.published_at,s.last_seen_at) >= %s - (%s * interval \'1 day\') '
                        'GROUP BY t.id,t.title,t.title_en,t.category,t.is_pinned '
                        'ORDER BY t.is_pinned DESC,updated DESC LIMIT %s', (cutoff, EVENT_RETENTION_DAYS, limit))
            topics = cur.fetchall()
            for topic in topics:
                event_id, title, title_en, category, pinned, updated = topic
                cur.execute('SELECT title,url,source_key,published_at,excerpt,first_seen_at FROM hot.event_sources '
                            'WHERE event_id=%s AND COALESCE(published_at,last_seen_at) >= %s - (%s * interval \'1 day\') '
                            'ORDER BY published_at DESC NULLS LAST,first_seen_at DESC LIMIT 12',
                            (event_id, cutoff, EVENT_RETENTION_DAYS))
                sources = cur.fetchall()
                cur.execute('SELECT COUNT(*) FROM hot.event_sources WHERE event_id=%s AND first_seen_at >= %s - interval \'1 day\'', (event_id, cutoff))
                recent = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM hot.event_sources WHERE event_id=%s AND first_seen_at >= %s - interval \'2 day\' AND first_seen_at < %s - interval \'1 day\'',
                            (event_id, cutoff, cutoff))
                previous = cur.fetchone()[0]
                cur.execute('SELECT COUNT(DISTINCT source_key) FROM hot.event_sources WHERE event_id=%s AND COALESCE(published_at,last_seen_at) >= %s - (%s * interval \'1 day\')',
                            (event_id, cutoff, EVENT_RETENTION_DAYS))
                source_count = cur.fetchone()[0]
                if recent > previous and recent >= 2:
                    trend = 'rising'
                elif previous > recent and previous >= 2:
                    trend = 'cooling'
                else:
                    trend = 'steady'
                rows.append({'id': event_id, 'title': title_en if lang == 'en' else title, 'category': category,
                             'last_updated': int(updated.timestamp()) if updated else 0, 'source_count': int(source_count),
                             'trend': trend, 'pinned': bool(pinned),
                             'items': [{'title': r[0], 'url': r[1], 'source': r[2],
                                        'published_at': int(r[3].timestamp()) if r[3] else 0, 'excerpt': r[4] or ''} for r in sources]})
        return rows


def _store(env):
    url = _database_url(env)
    if not url:
        return None
    try:
        return HotStore(url)
    except ImportError:
        return None


def _aggregate_week(snapshots):
    """Aggregate actual daily snapshots. `items` retain the current row fields."""
    by_url = {}
    dates = set()
    latest_at = 0
    for snapshot in snapshots:
        if len(snapshot) == 3:
            snapshot_day, fetched_at, rows = snapshot
        else:
            continue
        dates.add(snapshot_day.isoformat() if hasattr(snapshot_day, 'isoformat') else str(snapshot_day))
        latest_at = max(latest_at, int(fetched_at.timestamp() if hasattr(fetched_at, 'timestamp') else fetched_at or 0))
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except ValueError:
                rows = []
        for rank, row in enumerate(rows if isinstance(rows, list) else [], 1):
            if not isinstance(row, dict) or not row.get('url'):
                continue
            rec = by_url.setdefault(row['url'], {'item': row, 'score': 0.0, 'appearances': 0, 'best_rank': rank})
            rec['score'] += 100.0 / rank
            rec['appearances'] += 1
            rec['best_rank'] = min(rec['best_rank'], rank)
            rec['item'] = row
    items = []
    for rec in by_url.values():
        items.append({**rec['item'], 'weekly_score': round(rec['score'], 2), 'appearances': rec['appearances'], 'best_rank': rec['best_rank']})
    items.sort(key=lambda r: (r['weekly_score'], r['appearances'], -r['best_rank']), reverse=True)
    return items, {'available': bool(dates), 'complete': len(dates) >= SNAPSHOT_DAYS, 'required_days': SNAPSHOT_DAYS,
                   'snapshots': len(dates), 'dates': sorted(dates), 'updated': latest_at}


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
    """One row as the pages and the feed take it: the five fields (and, when the platform has them, a picture and a text), an http(s) address, no markup. None for a row that is not one."""
    if not isinstance(r, dict):
        return None
    title, url = re.sub(r'\s+', ' ', str(r.get('title') or '')).strip()[:200], _untrack(str(r.get('url') or '').strip())          # (a title is text: the pages escape it)
    p = urllib.parse.urlsplit(url)
    if not title or p.scheme not in ('http', 'https') or not p.netloc or re.search(r'\s', url):
        return None
    alt = str(r.get('alt') or '').strip()
    if urllib.parse.urlsplit(alt).scheme not in ('http', 'https'):
        alt = ''
    out = {'title': title, 'url': url, 'heat': _text(r.get('heat'), 40), 'note': _text(r.get('note'), 160), 'alt': alt, 'date': str(r.get('date') or '')[:10]}
    img, body = hotdetail._https(r.get('img')), _text(r.get('body'), 600)
    if img:
        out['img'] = img                                        # a picture of the row (a cover, a thumbnail): https only
    if body:
        out['body'] = body                                      # what the platform says about the row beyond its title (an excerpt, a description): the site shows it in a card of its own
    return out


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
            kids = r.get('children') if isinstance(r.get('children'), list) else []
            out.append({'title': t['title'], 'url': 'https://www.zhihu.com/question/%s' % t['id'], 'heat': _text(r.get('detail_text')) or ('%s 回答' % t.get('answer_count', 0)),
                        'note': ' · '.join(x for x in (('%s 回答' % t['answer_count']) if t.get('answer_count') else '', ('%s 关注' % t['follower_count']) if t.get('follower_count') else '') if x), 'body': t.get('excerpt'),
                        'img': kids[0].get('thumbnail') if kids and isinstance(kids[0], dict) else ''})
    return out


def p_zhihu_daily(raw):
    return [{'title': r['title'], 'url': r['url'], 'heat': '', 'note': r.get('hint') or ''}
            for r in _json(raw).get('stories') or [] if isinstance(r, dict) and r.get('title') and str(r.get('url') or '').startswith('https://daily.zhihu.com/')]


def p_bilibili(raw):
    rows = (_json(raw).get('data') or {}).get('list') or []
    return [{'title': r['title'], 'url': 'https://www.bilibili.com/video/%s' % r['bvid'], 'heat': '播放 ' + _heat((r.get('stat') or {}).get('view')), 'note': (r.get('owner') or {}).get('name', ''),
             'img': r.get('pic'), 'body': r.get('desc')}
            for r in rows if isinstance(r, dict) and r.get('title') and re.fullmatch(r'BV\w{10}', str(r.get('bvid') or ''))]


def p_toutiao(raw):
    return [{'title': r['Title'], 'url': 'https://www.toutiao.com/trending/%s/' % r['ClusterId'], 'heat': '热度 ' + _heat(r.get('HotValue')), 'note': ''}
            for r in _json(raw).get('data') or [] if isinstance(r, dict) and r.get('Title') and re.fullmatch(r'\d+', str(r.get('ClusterId') or ''))]


def p_douyin(raw):
    rows = (_json(raw).get('data') or {}).get('word_list') or []
    return [{'title': r['word'], 'url': 'https://www.douyin.com/hot/%s' % r['sentence_id'], 'heat': '热度 ' + _heat(r.get('hot_value')), 'note': '', 'img': ((r.get('word_cover') or {}).get('url_list') or [''])[0]}
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
    return [{'title': r['title'], 'url': r['url'], 'heat': '回复 %s' % r.get('replies', 0), 'note': (r.get('node') or {}).get('title', ''), 'body': r.get('content')}
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
        out.append({'title': m.group(1), 'url': 'https://github.com/' + m.group(1), 'heat': ('今日 +%s ★' % today.group(1)) if today else '', 'note': ' · '.join(x for x in (_text(lang.group(1), 30) if lang else '', _text(desc.group(1), 140) if desc else '') if x),
                    'body': _text(desc.group(1), 300) if desc else ''})
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
    return [{'title': r['title'], 'url': r.get('url') or '', 'heat': '♥ %s' % _n(r.get('public_reactions_count')), 'note': ' · '.join(x for x in ((r.get('user') or {}).get('name', ''), '%s 条评论' % _n(r.get('comments_count'))) if x),
             'img': r.get('cover_image') or r.get('social_image'), 'body': r.get('description')}
            for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and r.get('title')]


def p_netease(raw):
    out = []
    for r in (_json(raw).get('result') or {}).get('tracks') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d+', str(r.get('id') or '')):
            who = ' / '.join(a.get('name', '') for a in r.get('artists') or [] if isinstance(a, dict))
            out.append({'title': '%s — %s' % (r['name'], who) if who else r['name'], 'url': 'https://music.163.com/#/song?id=%s' % r['id'], 'heat': '', 'note': (r.get('album') or {}).get('name', ''), 'img': (r.get('album') or {}).get('picUrl')})
    return out


def p_qqmusic(raw):
    out = []
    for r in ((_json(raw).get('detail') or {}).get('data') or {}).get('songInfoList') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'[A-Za-z0-9]{10,16}', str(r.get('mid') or '')):
            who = ' / '.join(a.get('name', '') for a in r.get('singer') or [] if isinstance(a, dict))
            amid = str((r.get('album') or {}).get('mid') or '')
            out.append({'title': '%s — %s' % (r['name'], who) if who else r['name'], 'url': 'https://y.qq.com/n/ryqq/songDetail/%s' % r['mid'], 'heat': '', 'note': (r.get('album') or {}).get('name', ''),
                        'img': ('https://y.gtimg.cn/music/photo_new/T002R300x300M000%s.jpg' % amid) if re.fullmatch(r'[A-Za-z0-9]{10,16}', amid) else ''})
    return out


def p_apple(raw):
    """Apple's marketing-tools charts (songs, podcasts)."""
    out = []
    for r in (_json(raw).get('feed') or {}).get('results') or []:
        if isinstance(r, dict) and r.get('name') and re.match(r'https://(podcasts|music|apps)\.apple\.com/', str(r.get('url') or '')):
            genre = ((r.get('genres') or [{}])[0] or {}).get('name', '')
            out.append({'title': r['name'], 'url': r['url'], 'heat': '', 'note': ' · '.join(x for x in (r.get('artistName') or '', genre) if x), 'img': str(r.get('artworkUrl100') or '').replace('/100x100', '/300x300')})
    return out


def p_polymarket(raw, period='day'):
    """High-volume Polymarket events. These are prediction-market signals, not verified news."""
    data = _json(raw)
    if not isinstance(data, list):
        data = data.get('events') or [] if isinstance(data, dict) else []
    out = []
    for event in data:
        if not isinstance(event, dict) or not event.get('title') or not re.fullmatch(r'[A-Za-z0-9-]{1,160}', str(event.get('slug') or '')):
            continue
        volume = (event.get('volume1wk') or event.get('volume_1wk') or event.get('volume1w')) if period == 'week' else (event.get('volume24hr') or event.get('volume_24hr') or event.get('volume24h'))
        try:
            amount = float(volume or 0)
        except (TypeError, ValueError):
            amount = 0
        heat = (('近 7 天' if period == 'week' else '24h') + ' 成交量 $%s' % _heat(amount)) if amount > 0 else ''
        out.append({'title': event['title'], 'url': 'https://polymarket.com/event/' + event['slug'], 'heat': heat,
                    'note': '预测市场热度 · 非新闻结论或投资建议'})
    return out


def p_x(raw):
    """Recent public X posts from a small curated topic query."""
    data = _json(raw)
    users = {str(u.get('id')): u.get('username') for u in (data.get('includes') or {}).get('users', []) if isinstance(u, dict)}
    out = []
    for post in data.get('data') or []:
        if not isinstance(post, dict) or not re.fullmatch(r'\d{8,24}', str(post.get('id') or '')) or not post.get('text'):
            continue
        metrics = post.get('public_metrics') or {}
        engagement = sum(int(metrics.get(k) or 0) for k in ('like_count', 'retweet_count', 'reply_count', 'quote_count') if str(metrics.get(k) or '0').isdigit())
        author = users.get(str(post.get('author_id') or ''), '')
        out.append({'title': agg.clean(post['text'], 180), 'url': 'https://x.com/i/web/status/' + str(post['id']),
                    'heat': ('互动 %s' % _n(engagement)) if engagement else '', 'note': ('@' + author) if author else '', 'date': post.get('created_at') or ''})
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
                    'note': ' · '.join(x for x in (b.get('author') or '', ('推荐值 %.1f%%' % (rating / 10)) if isinstance(rating, (int, float)) and rating > 0 else '') if x), 'img': b.get('cover'), 'body': b.get('intro')})
    return out


def p_openlibrary(raw):
    out = []
    for r in _json(raw).get('works') or []:
        if isinstance(r, dict) and r.get('title') and re.fullmatch(r'/works/OL\d+W', str(r.get('key') or '')):
            out.append({'title': r['title'], 'url': 'https://openlibrary.org' + r['key'], 'heat': '', 'note': ' · '.join(x for x in ((r.get('author_name') or [''])[0], str(r.get('first_publish_year') or '')) if x),
                        'img': ('https://covers.openlibrary.org/b/id/%s-M.jpg' % r['cover_i']) if re.fullmatch(r'\d+', str(r.get('cover_i') or '')) else ''})
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
            out.append({'title': r['name'], 'url': 'https://store.steampowered.com/app/%s/' % r['id'], 'heat': _price(r.get('final_price'), r.get('currency')), 'note': ('-%s%%' % off) if off else '', 'img': r.get('header_image')})
    return out


def p_youtube(raw):
    out = []
    for r in _json(raw).get('items') or []:
        s = r.get('snippet') if isinstance(r, dict) else None
        if isinstance(s, dict) and s.get('title') and re.fullmatch(r'[\w-]{11}', str(r.get('id') or '')):
            th = s.get('thumbnails') if isinstance(s.get('thumbnails'), dict) else {}
            pic = th.get('medium') or th.get('high') or th.get('default')
            out.append({'title': s['title'], 'url': 'https://www.youtube.com/watch?v=%s' % r['id'], 'heat': ('观看 ' + _heat((r.get('statistics') or {}).get('viewCount'))) if (r.get('statistics') or {}).get('viewCount') else '', 'note': s.get('channelTitle') or '',
                        'img': pic.get('url') if isinstance(pic, dict) else '', 'body': s.get('description')})
    return out


def p_coingecko(raw):
    out = []
    for c in _json(raw).get('coins') or []:
        r = c.get('item') if isinstance(c, dict) else None
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'[\w.-]+', str(r.get('id') or '')):
            out.append({'title': '%s（%s）' % (r['name'], r.get('symbol') or ''), 'url': 'https://www.coingecko.com/en/coins/%s' % r['id'], 'heat': ('市值排名 #%s' % r['market_cap_rank']) if r.get('market_cap_rank') else '', 'note': '', 'img': r.get('large') or r.get('small')})
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
            out.append({'title': r['title'], 'url': r['url'], 'heat': ('%s 人分享' % _n(people)) if people else '', 'note': r.get('provider_name') or '', 'img': r.get('image'), 'body': r.get('description')})
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
    `via`: where the list really comes from, when that is not the platform. `read`: what the entries point to are articles the page may read in its reader (news, blogs); the others open the platform's own page.
    `detail`: the page behind a row can be had as text (hotdetail.py: V2EX, Zhihu Daily, a GitHub README, an Apple Podcasts show). `kw`: the row is only a phrase (a hot search): the site shows it in a card with the news about it."""
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
    'weibo': P('news', '微博热搜', 'Weibo hot searches', 'https://s.weibo.com/top/summary', 'https://weibo.com/ajax/side/hotSearch', p_weibo, headers={'Referer': 'https://weibo.com/'}, kw=True),
    'zhihu': P('media', '知乎热榜', 'Zhihu hot questions', 'https://www.zhihu.com/hot', 'https://api.zhihu.com/topstory/hot-lists/total?limit=50', p_zhihu, kw=True),
    'douyin': P('news', '抖音热点', 'Douyin hot topics', 'https://www.douyin.com/hot', 'https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1', p_douyin, headers={'Referer': 'https://www.douyin.com/'}, kw=True),
    'xiaohongshu': P('news', '小红书热门', 'Xiaohongshu hot notes', 'https://www.xiaohongshu.com/explore', 'https://rebang.today/?tab=xiaohongshu', p_xiaohongshu, via='rebang.today', ttl=THIRD_SECONDS, kw=True),
    'bilibili': P('news', 'B 站热门视频', 'Bilibili popular videos', 'https://www.bilibili.com/v/popular/all', 'https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1', p_bilibili, headers={'Referer': 'https://www.bilibili.com/'}),
    'reddit': P('news', 'Reddit 今日热门', 'Reddit popular today', 'https://www.reddit.com/r/popular/top/?t=day', 'https://www.reddit.com/r/popular/top/.rss?t=day', lang='en'),
    'wechat': TH('news', '公众号热文', 'WeChat articles', 'https://weixin.sogou.com/', 'WnBe01o371', ('weixin.qq.com',)),
    'toutiao': P('news', '今日头条热榜', 'Toutiao hot board', 'https://www.toutiao.com/', 'https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc', p_toutiao, kw=True),
    'hupu': TH('media', '虎扑步行街热帖', 'Hupu hot posts', 'https://bbs.hupu.com/', 'G47o8weMmN', ('hupu.com',)),
    'zhihu_daily': P('news', '知乎日报', 'Zhihu Daily', 'https://daily.zhihu.com/', 'https://plink.anyfeeder.com/zhihu/daily', read=True, detail=True),
    # ------------------------------------------------------------ 科技与开发
    'github': P('tech', 'GitHub Trending（今日）', 'GitHub Trending (today)', 'https://github.com/trending', 'https://github.com/trending?since=daily', p_github, lang='en', detail=True),
    'juejin': P('tech', '掘金热榜', 'Juejin hot articles', 'https://juejin.cn/hot/articles', 'https://api.juejin.cn/content_api/v1/content/article_rank?category_id=1&type=hot', p_juejin, read=True),
    'v2ex': P('tech', 'V2EX 今日热议', 'V2EX hot topics', 'https://www.v2ex.com/', 'https://www.v2ex.com/api/topics/hot.json', p_v2ex, detail=True, read=True),
    'hackernews': P('tech', 'Hacker News', 'Hacker News', 'https://news.ycombinator.com/', 'https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50', p_hn, lang='en', read=True),
    'lobsters': P('tech', 'Lobsters', 'Lobsters', 'https://lobste.rs/', 'https://lobste.rs/hottest.json', p_lobsters, lang='en', read=True),
    'devto': P('tech', 'DEV 社区（本周）', 'DEV Community (top)', 'https://dev.to/top/week', 'https://dev.to/api/articles?top=7&per_page=50', p_devto, lang='en', read=True),
    'producthunt': P('tech', 'Product Hunt', 'Product Hunt', 'https://www.producthunt.com/', 'https://www.producthunt.com/feed', lang='en', read=False),
    'sspai': P('tech', '少数派', 'sspai', 'https://sspai.com/', 'https://sspai.com/feed'),
    'geekpark': P('tech', '极客公园', 'GeekPark', 'https://www.geekpark.net/', 'https://www.geekpark.net/rss'),
    'ithome': P('tech', 'IT 之家', 'ITHome', 'https://www.ithome.com/', 'https://www.ithome.com/rss/'),
    'cnblogs': P('tech', '博客园', 'cnblogs', 'https://www.cnblogs.com/', 'https://feed.cnblogs.com/blog/sitehome/rss'),
    'kr36': TH('tech', '36氪 24 小时热榜', '36Kr 24-hour hot list', 'https://36kr.com/hot-list/catalog', 'Q1Vd5Ko85R', ('36kr.com',), read=True),
    'theverge': P('tech', 'The Verge', 'The Verge', 'https://www.theverge.com/', 'https://www.theverge.com/rss/index.xml', lang='en'),
    'arstechnica': P('tech', 'Ars Technica', 'Ars Technica', 'https://arstechnica.com/', 'https://feeds.arstechnica.com/arstechnica/index', lang='en'),
    'techcrunch': P('tech', 'TechCrunch', 'TechCrunch', 'https://techcrunch.com/', 'https://techcrunch.com/feed/', lang='en'),
    'hackaday': P('tech', 'Hackaday（硬件与创客）', 'Hackaday', 'https://hackaday.com/', 'https://hackaday.com/blog/feed/', lang='en'),
    'slashdot': P('tech', 'Slashdot', 'Slashdot', 'https://slashdot.org/', 'https://rss.slashdot.org/Slashdot/slashdotMain', lang='en'),
    # ------------------------------------------------------------ 财经
    'xueqiu': TH('money', '雪球热帖', 'Xueqiu hot posts', 'https://xueqiu.com/', 'X12owXzvNV', ('xueqiu.com',)),
    'ths': P('money', '同花顺热股', 'Tonghuashun hot stocks', 'https://www.10jqka.com.cn/', 'https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal', p_ths),
    'wallstreetcn': P('money', '华尔街见闻', 'WallStreetCN', 'https://wallstreetcn.com/', 'https://dedicated.wallstreetcn.com/rss.xml'),
    'cnbc': P('money', 'CNBC', 'CNBC', 'https://www.cnbc.com/', 'https://www.cnbc.com/id/100003114/device/rss/rss.html', lang='en'),
    'bloomberg': P('money', 'Bloomberg Markets', 'Bloomberg Markets', 'https://www.bloomberg.com/markets', 'https://feeds.bloomberg.com/markets/news.rss', lang='en', read=False),
    'economist': P('money', '经济学人 · 金融与经济', 'The Economist · Finance & economics', 'https://www.economist.com/finance-and-economics', 'https://www.economist.com/finance-and-economics/rss.xml', lang='en', read=False),
    'coingecko': P('money', '加密货币热搜（CoinGecko）', 'Trending coins (CoinGecko)', 'https://www.coingecko.com/en/highlights/trending-crypto', 'https://api.coingecko.com/api/v3/search/trending', p_coingecko, lang='en'),
    # ------------------------------------------------------------ 国际
    'gtrends': P('world', 'Google 热搜', 'Google Trends', 'https://trends.google.com/trending', GT + '{REGION}', p_gtrends, lang='en', regions=GT_REGIONS, region='US', kw=True),
    'wikipedia': P('world', '维基百科阅读榜（英文）', 'Wikipedia most read (English)', 'https://en.wikipedia.org/wiki/Wikipedia:Top_25_Report', WIKI % 'en', wiki('en'), lang='en', read=True),
    'wikipedia_zh': P('world', '维基百科阅读榜（中文）', 'Wikipedia most read (Chinese)', 'https://zh.wikipedia.org/', WIKI % 'zh', wiki('zh'), read=True),
    'bbc': P('world', 'BBC 国际', 'BBC World', 'https://www.bbc.com/news/world', 'https://feeds.bbci.co.uk/news/world/rss.xml', lang='en', read=False),
    'bbc_zh': P('world', 'BBC 中文', 'BBC Chinese', 'https://www.bbc.com/zhongwen/simp', 'https://feeds.bbci.co.uk/zhongwen/simp/rss.xml'),
    'rfi_zh': P('world', '法广中文', 'RFI Chinese', 'https://www.rfi.fr/cn/', 'https://www.rfi.fr/cn/rss'),
    'nyt_world': P('world', '纽约时报 · 国际', 'The New York Times · World', 'https://www.nytimes.com/section/world', 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml', lang='en', read=False),
    'nyt_cn': P('world', '纽约时报中文网', 'The New York Times (Chinese)', 'https://cn.nytimes.com/', 'https://cn.nytimes.com/rss/', read=False),
    'aljazeera': P('world', '半岛电视台', 'Al Jazeera', 'https://www.aljazeera.com/', 'https://www.aljazeera.com/xml/rss/all.xml', lang='en'),
    'guardian': P('world', '卫报 · 国际', 'The Guardian · World', 'https://www.theguardian.com/world', 'https://www.theguardian.com/world/rss', lang='en'),
    'mastodon': P('world', 'Mastodon 热门链接', 'Mastodon trending links', 'https://mastodon.social/explore/links', 'https://mastodon.social/api/v1/trends/links?limit=40', p_mastodon, lang='en', read=True),
    'dw': P('world', 'DW 德国之声（英文）', 'DW News (English)', 'https://www.dw.com/en/', 'https://rss.dw.com/rdf/rss-en-all', lang='en'),
    'france24': P('world', 'France 24（英文）', 'France 24 (English)', 'https://www.france24.com/en/', 'https://www.france24.com/en/rss', lang='en'),
    'conversation': P('world', 'The Conversation（学者写时事）', 'The Conversation', 'https://theconversation.com/global', 'https://theconversation.com/global/articles.atom', lang='en'),
    # ------------------------------------------------------------ AI 与学术
    'hf_papers': P('learning', 'Hugging Face 每日论文', 'Hugging Face daily papers', 'https://huggingface.co/papers', 'https://huggingface.co/api/daily_papers?limit=50', p_hf_papers, lang='en', read=True),
    'hf_models': P('ai', 'Hugging Face 热门模型', 'Hugging Face trending models', 'https://huggingface.co/models?sort=trending', 'https://huggingface.co/api/models?sort=trendingScore&limit=50', p_hf_models, lang='en', read=True),
    'arxiv_ai': P('learning', 'arXiv · cs.AI', 'arXiv · cs.AI', 'https://arxiv.org/list/cs.AI/new', 'https://rss.arxiv.org/rss/cs.AI', lang='en'),
    'arxiv_cl': P('learning', 'arXiv · cs.CL', 'arXiv · cs.CL', 'https://arxiv.org/list/cs.CL/new', 'https://rss.arxiv.org/rss/cs.CL', lang='en'),
    'openai': P('ai', 'OpenAI 动态', 'OpenAI news', 'https://openai.com/news/', 'https://openai.com/news/rss.xml', lang='en', read=False),
    'mitnews': P('learning', 'MIT 新闻', 'MIT News', 'https://news.mit.edu/', 'https://news.mit.edu/rss/feed', lang='en'),
    'qbitai': P('ai', '量子位', 'QbitAI', 'https://www.qbitai.com/', 'https://www.qbitai.com/feed'),
    'mit_ai': P('ai', 'MIT Technology Review · AI', 'MIT Technology Review · AI', 'https://www.technologyreview.com/topic/artificial-intelligence/', 'https://www.technologyreview.com/topic/artificial-intelligence/feed', lang='en'),
    'techcrunch_ai': P('ai', 'TechCrunch · AI', 'TechCrunch · AI', 'https://techcrunch.com/category/artificial-intelligence/', 'https://techcrunch.com/category/artificial-intelligence/feed/', lang='en'),
    'nature': P('learning', 'Nature 新闻', 'Nature', 'https://www.nature.com/nature/articles?type=news', 'https://www.nature.com/nature.rss', lang='en'),
    'science_news': P('learning', 'Science 新闻', 'Science news', 'https://www.science.org/news', 'https://www.science.org/rss/news_current.xml', lang='en', read=False),
    'physorg': P('learning', 'Phys.org', 'Phys.org', 'https://phys.org/', 'https://phys.org/rss-feed/', lang='en'),
    'quanta': P('learning', 'Quanta 杂志（数学与科学）', 'Quanta Magazine', 'https://www.quantamagazine.org/', 'https://www.quantamagazine.org/feed/', lang='en'),
    'sciam': P('learning', '科学美国人', 'Scientific American', 'https://www.scientificamerican.com/', 'https://www.scientificamerican.com/platform/syndication/rss/', lang='en'),
    'newscientist': P('learning', '新科学家', 'New Scientist', 'https://www.newscientist.com/', 'https://www.newscientist.com/feed/home/', lang='en'),
    # ------------------------------------------------------------ 读书与影音
    'weread_rising': P('learning', '微信读书 · 飙升榜', 'WeRead · rising books', 'https://weread.qq.com/web/category/rising', 'https://weread.qq.com/web/bookListInCategory/rising?rank=1', p_weread, headers={'Referer': 'https://weread.qq.com/'}),
    'weread_novel': P('entertainment', '微信读书 · 小说榜', 'WeRead · novels', 'https://weread.qq.com/web/category/100000', 'https://weread.qq.com/web/bookListInCategory/100000?maxIndex=0', p_weread, headers={'Referer': 'https://weread.qq.com/'}),
    'qidian': TH('entertainment', '起点中文网热榜', 'Qidian novels', 'https://www.qidian.com/rank/', 'VaobmGneAj', ('qidian.com',)),
    'qidian_f': TH('entertainment', '起点女生网热榜', 'Qidian Women novels', 'https://www.qdmm.com/rank/', 'DgeygBwvZq', ('qdmm.com', 'qidian.com')),
    'zongheng': TH('entertainment', '纵横中文网热榜', 'Zongheng novels', 'https://www.zongheng.com/rank/', 'b0vmYyJvB1', ('zongheng.com',)),
    'tuishujun': TH('entertainment', '推书君网文榜', 'Tuishujun novels', 'https://www.tuishujun.com/', '47o8QGjvMm', ('tuishujun.com',)),
    'openlibrary': P('learning', 'Open Library 近期热门', 'Open Library trending', 'https://openlibrary.org/trending/daily', 'https://openlibrary.org/trending/daily.json?limit=50', p_openlibrary, lang='en'),
    'gutenberg': P('learning', 'Project Gutenberg 热门公版书', 'Project Gutenberg popular books', 'https://www.gutenberg.org/browse/scores/top', 'https://gutendex.com/books/?sort=popular&languages=en', p_gutendex, lang='en'),
    'netease': P('media', '网易云音乐飙升榜', 'NetEase Cloud Music: rising songs', 'https://music.163.com/#/discover/toplist?id=19723756', 'https://music.163.com/api/playlist/detail?id=19723756', p_netease),
    'qqmusic': P('media', 'QQ 音乐飙升榜', 'QQ Music: rising songs', 'https://y.qq.com/n/ryqq/toplist/62', 'https://u.y.qq.com/cgi-bin/musicu.fcg?data=%7B%22detail%22%3A%7B%22module%22%3A%22musicToplist.ToplistInfoServer%22%2C%22method%22%3A%22GetDetail%22%2C%22param%22%3A%7B%22topId%22%3A62%2C%22offset%22%3A0%2C%22num%22%3A50%7D%7D%7D', p_qqmusic, headers={'Referer': 'https://y.qq.com/'}),
    'apple_pod_cn': P('media', 'Apple 播客榜 · 中国', 'Apple Podcasts · China', 'https://podcasts.apple.com/cn/charts', APPLE % 'cn/podcasts/top/50/podcasts.json', p_apple, detail=True),
    'apple_pod_us': P('media', 'Apple 播客榜 · 美国', 'Apple Podcasts · US', 'https://podcasts.apple.com/us/charts', APPLE % 'us/podcasts/top/50/podcasts.json', p_apple, lang='en', detail=True),
    'apple_music_us': P('media', 'Apple Music 最多播放 · 美国', 'Apple Music most played · US', 'https://music.apple.com/us/new', APPLE % 'us/music/most-played/50/songs.json', p_apple, lang='en'),
    'appstore_free_us': P('tech', 'App Store 免费榜 · 美国', 'App Store Top Free · US', 'https://apps.apple.com/us/charts/iphone/top-free-apps/36', APPLE % 'us/apps/top-free/50/apps.json', p_apple, lang='en'),
    'appstore_free_cn': P('tech', 'App Store 免费榜 · 中国', 'App Store Top Free · China', 'https://apps.apple.com/cn/charts/iphone/top-free-apps/36', APPLE % 'cn/apps/top-free/50/apps.json', p_apple),
    'polymarket': P('news', 'Polymarket 预测市场热度', 'Polymarket market activity', 'https://polymarket.com/', 'https://gamma-api.polymarket.com/events?active=true&closed=false&order={POLY_ORDER}&ascending=false&limit=50', p_polymarket, lang='en'),
    'x_trending': P('news', 'X 热门讨论（关键词）', 'X popular discussions (curated query)', 'https://x.com/explore', 'https://api.x.com/2/tweets/search/recent?query=%28Iran%20OR%20Israel%20OR%20Ukraine%20OR%20gold%20OR%20OpenAI%20OR%20Anthropic%20OR%20DeepSeek%29%20-lang%3Aund%20-is%3Arepost&max_results=50&tweet.fields=created_at%2Cpublic_metrics%2Cauthor_id&expansions=author_id&user.fields=username', p_x, lang='en', needs=['X_BEARER_TOKEN'], headers={'Authorization': 'Bearer {X_BEARER_TOKEN}'}),
    'steam': P('entertainment', 'Steam 热销', 'Steam top sellers', 'https://store.steampowered.com/search/?filter=topsellers', 'https://store.steampowered.com/api/featuredcategories?cc=cn&l=schinese', p_steam),
    'taptap': TH('entertainment', 'TapTap 热门游戏', 'TapTap hot games', 'https://www.taptap.cn/top/download', '6ARe1k2v7n', ('taptap.cn', 'taptap.io')),
    'douban_movie': TH('entertainment', '豆瓣新片榜', 'Douban new films', 'https://movie.douban.com/chart', 'mDOvnyBoEB', ('douban.com',)),
    'longreads': P('learning', 'Longreads 长文精选', 'Longreads', 'https://longreads.com/', 'https://longreads.com/feed/', lang='en'),
    'psyche': P('learning', 'Psyche（观念与生活）', 'Psyche', 'https://psyche.co/', 'https://psyche.co/feed', lang='en'),
    'pitchfork': P('media', 'Pitchfork 乐评', 'Pitchfork', 'https://pitchfork.com/', 'https://pitchfork.com/feed/feed-news/rss', lang='en'),
    'variety': P('entertainment', 'Variety 影视', 'Variety', 'https://variety.com/', 'https://variety.com/feed/', lang='en'),
    'letterboxd': P('media', 'Letterboxd 影评', 'Letterboxd Journal', 'https://letterboxd.com/journal/', 'https://letterboxd.com/journal/rss/', lang='en'),
    'polygon': P('entertainment', 'Polygon 游戏', 'Polygon', 'https://www.polygon.com/', 'https://www.polygon.com/rss/index.xml', lang='en'),
    'youtube': P('news', 'YouTube 热门', 'YouTube most popular', 'https://www.youtube.com/feed/trending',
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
        col = c.get('col') if c.get('col') in COLUMN_IDS or c.get('col') in LEGACY_COLUMNS else 'news'
        col = LEGACY_COLUMNS.get(col, col)
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


def _url(spec, env, now, region='', period='day'):
    """The address, with what changes filled in: the day of Wikipedia's list, YouTube's key, the country of a list that has countries."""
    u = spec['url']
    if '{' not in u:
        return u
    vals = {'DATE': time.strftime('%Y/%m/%d', time.gmtime(now - 2 * 86400)), 'YOUTUBE_API_KEY': urllib.parse.quote(str((env or {}).get('YOUTUBE_API_KEY') or '').strip(), safe=''),
            'POLY_ORDER': 'volume1wk' if period == 'week' else 'volume24hr',
            'REGION': region if re.fullmatch(r'[A-Z]{2}', region or '') else spec.get('region', 'US')}
    for k, v in vals.items():
        u = u.replace('{%s}' % k, v)
    return u


def feed_rows(body, headers, url):
    _, items = agg.parse_feed(body, headers, url)
    return [{'title': i.get('title'), 'url': i.get('url'), 'heat': '', 'note': '', 'date': i.get('date') or '', 'img': i.get('image'), 'body': i.get('summary')} for i in items]


def entries(pid, now=None, env=None, region='', timeout=None, period='day'):
    """-> (the platform's rows now, when they were read). A copy is kept for CACHE_SECONDS (for each country of a list that has countries); an old copy is served when the platform does not answer.
    Raises agg.Refused."""
    now = time.time() if now is None else now
    spec = platforms(env)[pid]
    key = '%s:%s' % (pid, region) if spec.get('regions') else '%s:%s' % (pid, period) if pid == 'polymarket' else pid
    hit = _CACHE.get(key)
    if hit and now - hit[0] < spec.get('ttl', CACHE_SECONDS):
        return hit[1], hit[0]
    try:
        request_headers = {k: re.sub(r'\{([A-Z0-9_]+)\}', lambda m: str((env or {}).get(m.group(1)) or ''), v) for k, v in spec['headers'].items()}
        final, h, body = agg.fetch(_url(spec, env, now, region, period), FEED_ACCEPT if spec['feed'] else '*/*', 3 * 1024 * 1024, headers={**BROWSER, **request_headers}, truncate=True, timeout=timeout)
        try:
            decoded = agg.decode(body, h)
            rows = feed_rows(body, h, final) if spec['feed'] else spec['parse'](decoded, period=period) if pid == 'polymarket' else spec['parse'](decoded)
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
        desc = ' · '.join(x for x in bits if x) + (' — ' + r['body'] if r.get('body') and r['body'] not in r['note'] else '')
        thumb = '<media:thumbnail url="%s"/>' % escape(r['img'], {'"': '&quot;'}) if r.get('img') else ''
        items.append('<item><title>%s</title><link>%s</link><guid isPermaLink="false">hot:%s:%s</guid><pubDate>%s</pubDate><description>%s</description>%s</item>'
                     % (escape(r['title']), escape(r['url']), pid, hashlib.sha1(r['url'].encode('utf-8')).hexdigest()[:12], stamp, escape(desc), thumb))
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>%s</title><link>%s</link><description>%s</description><language>%s</language><lastBuildDate>%s</lastBuildDate>%s</channel></rss>'
            % (escape(title), escape(spec['home']), escape('%s：平台当前的榜单（排名、标题、热度和平台自己的链接），由知树阅读按需生成；配置数据库时只保存榜单元数据快照，不保存文章全文或图片。' % title), 'zh-CN' if spec['lang'] == 'zh' else 'en', stamp, ''.join(items)))


def _info(k, v):
    d = {'id': k, 'name': v['name'], 'name_en': v['name_en'], 'home': v['home'], 'lang': v['lang'], 'col': v['col'], 'ranked': v['ranked']}
    if v.get('via'):
        d['via'] = v['via']
    if v.get('read'):
        d['read'] = True
    if v.get('detail'):
        d['detail'] = True
    if v.get('kw'):
        d['kw'] = True
    if v.get('regions'):
        d['regions'] = list(v['regions'])
    return d


def _fail(status, error, zh, en):
    return status, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': error, 'message': zh, 'message_en': en}, ensure_ascii=False).encode('utf-8'), 'no-store'


def _event_matches(topic, text):
    value = re.sub(r'\s+', ' ', str(text or '')).lower()
    if topic['id'] == 'iran-israel':
        return ('iran' in value and 'israel' in value) or ('iranian' in value and 'israeli' in value)
    if topic['id'] == 'russia-ukraine':
        return ('ukraine' in value and ('russia' in value or 'russian' in value)) or ('ukrainian' in value and 'war' in value)
    if topic['id'] == 'global-markets' and any(x in value for x in ('gold', 'bullion', 'precious metal')):
        return False
    return any(term in value for term in topic['terms'])


def _event_epoch(value):
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    value = str(value).strip()
    for fmt in ('%Y%m%dT%H%M%SZ', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d'):
        try:
            return int(datetime.strptime(value[:len(datetime.now().strftime(fmt))], fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return 0


def _event_candidate(topic, title, url, source, published_at=0, excerpt=''):
    title = _text(title, 260)
    url = _untrack(str(url or '').strip())
    parsed = urllib.parse.urlsplit(url)
    if not title or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or re.search(r'\s', url):
        return None
    source = _text(source or parsed.hostname.lower(), 100).lower()
    return {'event_id': topic['id'], 'title': title, 'url': url, 'source': source,
            'published_at': int(published_at or 0), 'excerpt': _text(excerpt, 280)}


def _event_candidates(env, now):
    """Fetch only report metadata; never fetch article pages or retain images."""
    candidates = {}
    source_succeeded = False
    params = urllib.parse.urlencode({'query': EVENT_GDELT_QUERY, 'mode': 'ArtList', 'format': 'json', 'maxrecords': 250, 'sort': 'HybridRel', 'timespan': '7d'})
    try:
        _, headers, body = agg.fetch('https://api.gdeltproject.org/api/v2/doc/doc?' + params, 'application/json', 2 * 1024 * 1024,
                                     headers=BROWSER, truncate=True, timeout=EVENT_FETCH_TIMEOUT)
        source_succeeded = True
        data = _json(agg.decode(body, headers))
        articles = data.get('articles') if isinstance(data, dict) else []
        for article in articles if isinstance(articles, list) else []:
            if not isinstance(article, dict):
                continue
            title, url = _text(article.get('title'), 260), str(article.get('url') or '')
            for topic in EVENT_TOPICS:
                if not _event_matches(topic, title):
                    continue
                source = _text(article.get('domain') or urllib.parse.urlsplit(url).hostname or '', 100).lower()
                item = _event_candidate(topic, title, url, source, _event_epoch(article.get('seendate')))
                if item:
                    candidates[(item['event_id'], item['url'])] = item
    except (agg.Refused, ValueError, TypeError, AttributeError):
        pass

    # These first-party feeds complement cross-source discovery in their areas.
    # Summaries are capped and remain linked to the source; no page is fetched.
    for pid in ('wallstreetcn', 'openai'):
        try:
            rows, _ = entries(pid, now, env, timeout=EVENT_FETCH_TIMEOUT)
            source_succeeded = True
        except (agg.Refused, KeyError, TypeError, ValueError):
            continue
        for row in rows:
            title = _text(row.get('title'), 260)
            snippet = _text(row.get('body') or row.get('note'), 280)
            for topic in EVENT_TOPICS:
                if not _event_matches(topic, title + ' ' + snippet):
                    continue
                url = str(row.get('url') or '')
                source = urllib.parse.urlsplit(url).hostname or pid
                item = _event_candidate(topic, title, url, source, _event_epoch(row.get('date')), snippet)
                if item:
                    key = (item['event_id'], item['url'])
                    previous = candidates.get(key)
                    if previous and len(item['excerpt']) <= len(previous['excerpt']):
                        continue
                    candidates[key] = item
    if not source_succeeded:
        raise agg.Refused('暂时无法读取热点来源', 'Event sources are temporarily unavailable')
    return list(candidates.values())


def _refresh_events(store, env, now, force=False, fetcher=None):
    """Refresh once per cache window; PostgreSQL advisory locking protects concurrent instances."""
    last = store.latest_event_refresh()
    if last and not force and now - last < EVENT_CACHE_SECONDS:
        return {'cached': True, 'busy': False, 'updated': last, 'candidate_count': 0, 'stored_count': 0}
    lock = store.try_event_refresh_lock()
    if not lock:
        return {'cached': True, 'busy': True, 'updated': last, 'candidate_count': 0, 'stored_count': 0}
    try:
        # Another request may have completed while this one waited for a DB connection.
        last = store.latest_event_refresh()
        if last and not force and now - last < EVENT_CACHE_SECONDS:
            return {'cached': True, 'busy': False, 'updated': last, 'candidate_count': 0, 'stored_count': 0}
        candidates = (fetcher or _event_candidates)(env, now)
        stored = store.save_events(candidates, now)
        return {'cached': False, 'busy': False, 'updated': now,
                'candidate_count': len(candidates), 'stored_count': stored}
    finally:
        store.release_event_refresh_lock(lock)


def handle_events(query, env, now=None):
    """Public event feed; stale data remains available while a throttled refresh is attempted."""
    now = time.time() if now is None else now
    lang = str((query.get('lang') or ['zh'])[0]).strip().lower()
    lang = 'en' if lang.startswith('en') else 'zh'
    try:
        limit = max(1, min(30, int((query.get('limit') or [12])[0])))
    except (TypeError, ValueError):
        limit = 12
    store = _store(env)
    if store is None:
        return _fail(503, 'database', '热点追踪数据库尚未配置', 'The event store is not configured')
    try:
        events = store.list_events(lang, limit, now)
        updated = store.latest_event_refresh()
    except Exception:
        return _fail(503, 'database', '热点追踪数据库不可用，请确认已执行 007_hotlist_events.sql', 'The event store is unavailable; check that migration 007_hotlist_events.sql has been applied')
    refresh_failed = False
    if not updated or now - updated >= EVENT_CACHE_SECONDS:
        try:
            _refresh_events(store, env, now)
            events = store.list_events(lang, limit, now)
            updated = store.latest_event_refresh()
        except Exception:
            # Network failures, lock contention, or a transient write error must not
            # make a previously populated public feed disappear.
            refresh_failed = True
    if not events and not updated and refresh_failed:
        return _fail(502, 'upstream', '热点来源暂时不可用，尚无可用缓存；请稍后重试', 'Hot-topic sources are unavailable and no cached results exist; retry later')
    body = json.dumps({'ok': True, 'updated': int(updated or 0), 'events': events}, ensure_ascii=False).encode('utf-8')
    return 200, 'application/json; charset=utf-8', body, 'public, max-age=60, s-maxage=180, stale-while-revalidate=300'


def handle_event_refresh(method, headers, env, query=None, now=None, fetcher=None):
    """Authenticated scheduler endpoint. Supports GET (Vercel Cron) and POST."""
    if method not in ('GET', 'POST'):
        return 405, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'method', 'message': '仅支持 GET 或 POST'}, ensure_ascii=False).encode('utf-8'), 'no-store'
    env = env or {}
    secret = str(env.get('HOT_REFRESH_SECRET') or env.get('CRON_SECRET') or '').strip()
    if not secret:
        return _fail(503, 'config', '未配置 HOT_REFRESH_SECRET', 'HOT_REFRESH_SECRET is not configured')
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    authorization = h.get('authorization', '')
    supplied = authorization[7:].strip() if authorization.lower().startswith('bearer ') else ''
    if not supplied or not hmac.compare_digest(supplied, secret):
        return _fail(401, 'unauthorized', '没有权限刷新热点', 'Not authorized to refresh events')
    now = time.time() if now is None else now
    store = _store(env)
    if store is None:
        return _fail(503, 'database', '热点追踪数据库尚未配置', 'The event store is not configured')
    try:
        force = str(((query or {}).get('force') or [''])[0]).lower() in ('1', 'true', 'yes')
        result = _refresh_events(store, env, now, force=force, fetcher=fetcher)
    except agg.Refused as e:
        return _fail(502, 'upstream', '热点源暂时不可用：%s' % e, 'The event source is unavailable: %s' % e.en)
    except Exception:
        return _fail(503, 'database', '热点追踪数据库不可用，请确认已执行 007_hotlist_events.sql', 'The event store is unavailable; check that migration 007_hotlist_events.sql has been applied')
    payload = {'ok': True, 'cached': result['cached'], 'updated': int(result['updated'] or 0),
               'candidate_count': result['candidate_count'], 'stored_count': result['stored_count']}
    if result['busy']:
        payload['busy'] = True
    return 200, 'application/json; charset=utf-8', json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'no-store'


def diagnose(pid, env):
    """Why a platform cannot be read from where this runs: its host name resolved, then one connection to it (a platform of the list only; nothing is sent)."""
    import socket
    spec = platforms(env).get(pid)
    host = urllib.parse.urlsplit(spec['url']).hostname if spec else ''
    if not host:
        return None
    out = {'ok': True, 'platform': pid, 'host': host, 'region': str((env or {}).get('VERCEL_REGION') or '')}
    t = time.time()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        out['dns'] = 'ok'
        out['ips'] = sorted({i[4][0] for i in infos})[:3]
    except OSError as e:
        out['dns'] = 'failed: %s' % str(e)[:80]
        return out
    finally:
        out['dns_ms'] = int((time.time() - t) * 1000)
    t = time.time()
    try:
        socket.create_connection((infos[0][4][0], 443), 4).close()
        out['connect'] = 'ok'
    except OSError as e:
        out['connect'] = 'failed: %s' % (str(e)[:80] or 'timed out')
    out['connect_ms'] = int((time.time() - t) * 1000)
    return out


def _extra(query, env, now):
    """What is behind a row (`detail=<address of the row>`) or the news about a phrase (`related=<words>&lang=zh|en`). HOT_DETAIL=0 / HOT_RELATED=0 turn them off.
    `diag=<platform>` says why a platform cannot be reached from here (name not found, no connection)."""
    pid = (query.get('diag') or [''])[0]
    if pid:
        d = diagnose(pid, env)
        if not d:
            return _fail(404, 'platform', '没有这个榜单', 'No such list')
        return 200, 'application/json; charset=utf-8', json.dumps(d, ensure_ascii=False).encode('utf-8'), 'no-store'
    ok = lambda d, ttl: (200, 'application/json; charset=utf-8', json.dumps({'ok': True, **d}, ensure_ascii=False).encode('utf-8'), 'public, max-age=%d, s-maxage=%d, stale-while-revalidate=3600' % (ttl // 2, ttl))
    try:
        url = (query.get('detail') or [''])[0]
        if url:
            if str((env or {}).get('HOT_DETAIL') or '') == '0':
                return _fail(404, 'off', '这个功能没有开启', 'Not turned on')
            return ok(hotdetail.detail(url, now), 1800)
        if str((env or {}).get('HOT_RELATED') or '') == '0':
            return _fail(404, 'off', '这个功能没有开启', 'Not turned on')
        lang = (query.get('lang') or ['zh'])[0]
        return ok({'items': hotdetail.related((query.get('related') or [''])[0], lang, now)}, 1200)
    except hotdetail.Nothing:
        return _fail(404, 'none', '这个地址没有可以在站内显示的内容', 'Nothing to show in the page for this address')
    except agg.Refused as e:
        return _fail(502, 'upstream', '现在读不到：%s' % e, 'Cannot be read right now: %s' % e.en)


def handle(query, env, now=None, headers=None):
    """-> (status, content type, body bytes, cache header). `query`: the part after "?" (a dict of lists, as parse_qs makes it); `headers`: the request's, for the country of a list that has countries."""
    plats = platforms(env)
    on = enabled(env, plats)
    pid = (query.get('p') or [''])[0]
    if (query.get('detail') or [''])[0] or (query.get('related') or [''])[0] or (query.get('diag') or [''])[0]:
        return _extra(query, env, now)
    if not pid:                                                                # what is on
        cols = [{**c, 'enabled': any(plats[k]['col'] == c['id'] for k in on)} for c in COLUMNS]
        body = json.dumps({'ok': True, 'service': 'shelf-hot', 'columns': cols, 'platforms': [_info(k, plats[k]) for k in on]}, ensure_ascii=False).encode('utf-8')
        return 200, 'application/json; charset=utf-8', body, 'public, max-age=300'
    if pid not in plats or pid not in on:
        return 404, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'platform', 'message': '没有这个榜单', 'message_en': 'No such list'}).encode('utf-8'), 'no-store'
    try:
        n = max(1, min(MAX_COUNT, int((query.get('n') or [DEFAULT_COUNT])[0])))
    except ValueError:
        n = DEFAULT_COUNT
    period = str((query.get('period') or ['day'])[0]).strip().lower()
    if period not in ('day', 'week'):
        period = 'day'
    asked = (query.get('region') or [''])[0]
    region = pick_region(plats[pid], env, asked, headers)
    mine = bool(region) and asked.strip().upper() != region                  # the answer depends on who asks (the address, the language): the CDN must not give it to somebody else
    store = _store(env)
    history = {'available': False, 'complete': False, 'required_days': SNAPSHOT_DAYS, 'snapshots': 0, 'dates': []}
    if period == 'week':
        if pid == 'polymarket':
            try:
                rows, at = entries(pid, now, env, region, period='week')
                history = {'available': True, 'complete': True}
            except agg.Refused as e:
                return 502, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'upstream', 'message': '这个榜单现在读不到：%s' % e, 'message_en': 'This list cannot be read right now: %s' % e.en}, ensure_ascii=False).encode('utf-8'), 'no-store'
        elif store is None:
            rows, at = [], 0
        else:
            try:
                rows, history = store.chart_week(pid, region, now if now is not None else time.time())
                at = history.get('updated', 0)
            except Exception:
                rows, at = [], 0
        if not history['available']:
            history = {**history, 'available': False}
    else:
        try:
            rows, at = entries(pid, now, env, region, period=period)
        except agg.Refused as e:
            return 502, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': 'upstream', 'message': '这个榜单现在读不到：%s' % e, 'message_en': 'This list cannot be read right now: %s' % e.en}, ensure_ascii=False).encode('utf-8'), 'no-store'
        if store is not None:
            try:
                inserted = store.save_chart(pid, region, at, rows)
                history = {'available': True, 'complete': False, 'required_days': SNAPSHOT_DAYS,
                           'snapshots': 1 if inserted else 1, 'dates': [datetime.fromtimestamp(float(at), timezone.utc).date().isoformat()]}
            except Exception:
                history = {'available': False, 'complete': False, 'required_days': SNAPSHOT_DAYS, 'snapshots': 0, 'dates': []}
    rows = rows[:n]
    if (query.get('format') or [''])[0] == 'json':
        if (query.get('tr') or [''])[0] == 'zh' and plats[pid]['lang'] == 'en' and translate.provider(env):          # the page is Chinese and the list English: the Chinese of the titles, for a small line under them
            zh = translate.titles(rows, env, persist=False, limit=60)
            rows = [{**r, 'title_zh': zh[r['title']]} if zh.get(r['title']) else r for r in rows]
        return (200, 'application/json; charset=utf-8', json.dumps({'ok': True, 'platform': pid, **_info(pid, plats[pid]), **({'region': region} if region else {}), 'updated': int(at), 'items': rows, 'period': period, 'history': history}, ensure_ascii=False).encode('utf-8'),
                PRIVATE if mine else CDN)
    if period == 'week':
        return _fail(406, 'format', '周榜仅提供 JSON 格式', 'The weekly chart is available as JSON only')
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
