"""Today's listening: songs that play in the page's own player, in lists that change every day.

GET /api/listen              -> the lists there are today (daily picks, charts, play lists, styles), without their songs: {groups: [{id, name, name_en, kind, net}]}
GET /api/listen?g=rock       -> the songs of one list (up to 30; a play list up to 50), each with the address of a sound the page's player plays
GET /api/listen?diag=1       -> for each source whether it answered and how much it gave (nothing secret in it: the keys never leave the server)

A list is made of one or more sources, asked in parallel (a source that is slow is left out of this answer, not waited for):
  - Deezer      charts of the world and of 10 styles, and play lists; 30-second previews that Deezer publishes for apps (no key).
  - Apple Music the most-played chart of a country and of a style (rock, pop, hip-hop ...); the 30-second previews Apple publishes (iTunes lookup, no key).
  - Audius      trending songs of a style, and trending play lists: WHOLE songs that artists put on Audius for anybody to stream (no key).
  - Jamendo     free music under Creative Commons, whole songs. Needs JAMENDO_CLIENT_ID (free to register). A song whose licence forbids commercial use is left out (this site shows ads)
                unless LISTEN_ALLOW_NC=1; one whose licence is not known is left out.
  - NetEase     the charts and play lists of NetEase Cloud Music as whole songs, from `music.163.com/song/media/outer/url` (a redirect to NetEase's sound server that a browser follows for the
                songs open to everybody; each song is checked first, the others are left out). That address is not a documented API and the music is the rights holders': it is off until the owner
                turns it on with LISTEN_NETEASE_DIRECT=1, knowing that.
  - YouTube     the most popular music videos (needs YOUTUBE_API_KEY). YouTube allows its videos only in its own player, so these are not songs of the page's player: the page opens them in a window.
"Different every day" = the day (in China) is the seed of a fixed shuffle (and of which country's chart is used), so every reader sees the same lists on the same day. Nothing is stored: each
answer is made from what the sources said in the last hours (an in-memory copy per warm instance, and the CDN keeps an answer for 30 minutes).
registry/listen.json can add lists of the owner's own (see EXTRA in the README).
"""
import base64
import concurrent.futures
import http.client
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import aggregate as agg
import hotapi

DAY_OFFSET = 8 * 3600                       # the day starts at midnight in China
POOL_SECONDS = 6 * 3600                     # what a source said is kept this long per warm instance
STALE_SECONDS = 36 * 3600                   # ... and served this long after, when the source does not answer
GROUP_SECONDS = 7.0                         # a list waits this long for its sources (a Vercel function has 10 seconds); the slow ones are left out
CDN = 'public, max-age=600, s-maxage=1800, stale-while-revalidate=21600'
JAMENDO_URL = 'https://api.jamendo.com/v3.0/tracks/'
NETEASE = 'https://music.163.com/api/playlist/detail?id=%s'
NE_OUTER = 'https://music.163.com/song/media/outer/url?id=%s.mp3'
YOUTUBE = 'https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&videoCategoryId=10&maxResults=40&regionCode=%s&key=%s'
APPLE_CHART = 'https://rss.applemarketingtools.com/api/v2/%s/music/most-played/25/songs.json'
APPLE_TOP = 'https://itunes.apple.com/%s/rss/topsongs/limit=50/json'                       # (the older feed answers faster and more often than the newer one; the newer is the back-up)
APPLE_GENRE = 'https://itunes.apple.com/%s/rss/topsongs/limit=40/genre=%s/json'
APPLE_LOOKUP = 'https://itunes.apple.com/lookup?id=%s&country=%s'
DEEZER_CHART = 'https://api.deezer.com/chart/%s/tracks?limit=50'
DEEZER_CHART_LISTS = 'https://api.deezer.com/chart/0/playlists?limit=12'
DEEZER_LIST = 'https://api.deezer.com/playlist/%s/tracks?limit=50'
AUDIUS = 'https://api.audius.co/v1'
AUDIUS_APP = 'zhitree-books'
APPLE_REGIONS = {'cn': ('中国', 'China'), 'us': ('美国', 'US'), 'jp': ('日本', 'Japan'), 'kr': ('韩国', 'Korea'), 'tw': ('台湾', 'Taiwan'), 'hk': ('香港', 'Hong Kong'), 'gb': ('英国', 'UK'), 'de': ('德国', 'Germany'), 'fr': ('法国', 'France')}
PROVIDERS = {'apple': 'Apple Music', 'apple_genre': 'Apple Music', 'deezer': 'Deezer', 'deezer_pl': 'Deezer', 'audius': 'Audius', 'audius_pl': 'Audius', 'jamendo': 'Jamendo', 'netease': '网易云音乐', 'youtube': 'YouTube'}
_CACHE = {}

# Jamendo: tags to ask for (and whether only music without singing)
JAMENDO_KINDS = {'chill': (['chillout', 'lounge', 'ambient', 'downtempo'], False), 'focus': (['piano', 'classical', 'jazz', 'instrumental'], True), 'sleep': (['ambient', 'meditation', 'sleep', 'newage'], True),
                 'jazz': (['jazz', 'smoothjazz', 'bossanova'], False), 'classical': (['classical', 'orchestral', 'piano'], True), 'acoustic': (['acoustic', 'folk', 'singersongwriter'], False)}


def _g(gid, zh, en, kind, sources, policy='mix', count=30, shuffle=True, per_artist=2, net='cn'):
    return {'id': gid, 'name': zh, 'name_en': en, 'kind': kind, 'sources': sources, 'policy': policy, 'count': count, 'shuffle': shuffle, 'per_artist': per_artist, 'net': net}


# The lists there are. kind: daily (the day's picks), chart (what is on top now), list (a play list), style (a kind of music). A source is a tuple: its kind and what to ask for.
STATIC = [
    _g('daily', '每日推荐', 'Daily picks', 'daily', [('deezer', 0), ('apple', 'cn'), ('apple', 'us'), ('audius', ''), ('netease', 19723756), ('jamendo', 'chill')], per_artist=1),
    _g('hot-cn', '华语热歌', 'Chinese hits', 'chart', [('netease', 3778678), ('apple', 'cn'), ('apple', 'tw'), ('apple', 'hk')], 'fill', shuffle=False),
    _g('hot-west', '欧美热歌', 'Western hits', 'chart', [('deezer', 0), ('apple', 'us'), ('apple', 'gb'), ('netease', 2809513713)], shuffle=False),
    _g('kr', '韩流', 'Korea', 'chart', [('apple', 'kr'), ('netease', 745956260)], shuffle=False),
    _g('jp', '日系', 'Japan', 'chart', [('apple', 'jp'), ('apple_genre', 'jp', 27), ('netease', 71385702)], shuffle=False),
    _g('dzp-3155776842', 'Deezer 全球 Top 100', 'Deezer Top Worldwide', 'list', [('deezer_pl', 3155776842)], 'first', 50, False),
    _g('dzp-1313621735', 'Deezer 美国 Top 100', 'Deezer Top USA', 'list', [('deezer_pl', 1313621735)], 'first', 50, False),
    _g('dzp-1111142221', 'Deezer 英国 Top 100', 'Deezer Top UK', 'list', [('deezer_pl', 1111142221)], 'first', 50, False),
    _g('rock', '摇滚', 'Rock', 'style', [('deezer', 152), ('audius', 'Rock'), ('apple_genre', 'us', 21), ('apple_genre', 'gb', 21)]),
    _g('pop', '流行', 'Pop', 'style', [('deezer', 132), ('apple_genre', 'us', 14), ('audius', 'Pop')]),
    _g('hiphop', '说唱', 'Hip-hop & rap', 'style', [('deezer', 116), ('audius', 'Hip-Hop/Rap'), ('apple_genre', 'us', 18), ('netease', 991319590)]),
    _g('electronic', '电子', 'Electronic', 'style', [('deezer', 106), ('audius', 'Electronic'), ('apple_genre', 'us', 7), ('netease', 1978921795), ('netease', 3812895)]),
    _g('dance', '舞曲', 'Dance', 'style', [('deezer', 113), ('audius', 'Dubstep'), ('apple_genre', 'us', 17)]),
    _g('rnb', 'R&B 与灵魂', 'R&B & soul', 'style', [('deezer', 165), ('deezer', 169), ('audius', 'R&B/Soul'), ('apple_genre', 'us', 15)]),
    _g('country', '乡村', 'Country', 'style', [('deezer', 84), ('audius', 'Country'), ('apple_genre', 'us', 6)]),
    _g('alternative', '另类', 'Alternative', 'style', [('deezer', 85), ('audius', 'Alternative'), ('apple_genre', 'us', 20)]),
    _g('metal', '金属', 'Metal', 'style', [('deezer', 464), ('audius', 'Metal')]),
    _g('jazz', '爵士', 'Jazz', 'style', [('deezer', 129), ('audius', 'Jazz'), ('apple_genre', 'us', 11), ('jamendo', 'jazz')]),
    _g('classical', '古典', 'Classical', 'style', [('deezer', 98), ('audius', 'Classical'), ('apple_genre', 'us', 5), ('netease', 71384707), ('jamendo', 'classical')]),
    _g('folk', '民谣与原声', 'Folk & acoustic', 'style', [('deezer', 466), ('audius', 'Folk'), ('audius', 'Acoustic'), ('apple_genre', 'us', 1289), ('apple_genre', 'us', 10), ('jamendo', 'acoustic')]),
    _g('latin', '拉丁', 'Latin', 'style', [('deezer', 197), ('audius', 'Latin'), ('apple_genre', 'us', 12)]),
    _g('reggae', '雷鬼', 'Reggae', 'style', [('deezer', 144), ('audius', 'Reggae'), ('apple_genre', 'us', 24)]),
    _g('blues', '蓝调', 'Blues', 'style', [('deezer', 153), ('audius', 'Blues'), ('apple_genre', 'us', 2)]),
    _g('chill', '放松', 'Chill', 'style', [('jamendo', 'chill'), ('audius', 'Ambient'), ('apple_genre', 'us', 13)]),
    _g('focus', '专注', 'Focus', 'style', [('jamendo', 'focus'), ('audius', 'Classical'), ('audius', 'Ambient'), ('apple_genre', 'us', 53)]),
    _g('sleep', '助眠', 'Sleep', 'style', [('jamendo', 'sleep'), ('audius', 'Ambient'), ('apple_genre', 'us', 13)]),
    _g('acg', 'ACG 与动漫', 'Anime & games', 'style', [('netease', 71385702), ('deezer', 173), ('apple_genre', 'us', 29), ('apple_genre', 'jp', 29)]),
    _g('video', '音乐视频', 'Music videos', 'style', [('youtube',)], 'first', 20, False, net='global'),
]
NE_LISTS = [(3778678, '热歌榜', 'Hot songs'), (3779629, '新歌榜', 'New songs'), (19723756, '飙升榜', 'Rising'), (2884035, '原创榜', 'Original'), (71384707, '古典榜', 'Classical'), (1978921795, '电音榜', 'Electronic'),
            (991319590, '说唱榜', 'Rap'), (71385702, 'ACG 榜', 'ACG'), (745956260, '韩语榜', 'Korean'), (2809513713, '欧美热歌榜', 'Western hits'), (2809577409, '欧美新歌榜', 'Western new songs'),
            (3812895, 'Beatport 电子舞曲榜', 'Beatport dance'), (60198, 'Billboard 榜', 'Billboard')]
KIND_ORDER = {'daily': 0, 'chart': 1, 'list': 2, 'style': 3}
SOURCE_ARGS = {'apple': (str,), 'apple_genre': (str, int), 'deezer': (int,), 'deezer_pl': (int,), 'audius': (str,), 'audius_pl': (str,), 'jamendo': (str,), 'netease': (int,), 'youtube': ()}


# ---------------------------------------------------------------- small helpers
def day_of(now):
    return int((now + DAY_OFFSET) // 86400)


def day_text(now):
    return time.strftime('%Y-%m-%d', time.gmtime(now + DAY_OFFSET))


def _t(s, n=120):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(s if s is not None else ''))).strip()[:n]


def _https(u):
    u = str(u or '').strip()
    if u.startswith('http://'):
        u = 'https://' + u[7:]
    return u if re.fullmatch(r'https://[^\s"<>]{4,500}', u) else ''


def _secs(v, per=1):
    try:
        n = int(float(v) / per)
    except (TypeError, ValueError):
        return 0
    return n if 0 < n < 6 * 3600 else 0


def _int(v, default, low, high):
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(low, min(high, n))


def shuffled(items, seed):
    """The same order for the same seed, on any machine and on any day it is asked for."""
    out = list(items)
    random.Random(seed).shuffle(out)
    return out


def _get(url, headers=None):
    """-> the text of an answer. Raises agg.Refused (its words never contain the address, so a key in it stays private)."""
    final, h, body = agg.fetch(url, 'application/json, */*;q=0.5', 2 * 1024 * 1024, headers={**hotapi.BROWSER, **(headers or {})}, truncate=True)
    return agg.decode(body, h)


def _json(text):
    try:
        return json.loads(text)
    except ValueError:
        raise agg.Refused('来源的格式变了', 'The format of the source has changed')


def pooled(key, now, make):
    """A source's answer, kept POOL_SECONDS; an older copy is served (up to STALE_SECONDS) when it does not answer."""
    hit = _CACHE.get(key)
    if hit and now - hit[0] < POOL_SECONDS:
        return hit[1]
    try:
        try:
            val = make()
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):          # (an answer that is not shaped as it was)
            raise agg.Refused('来源的格式变了', 'The format of the source has changed')
    except agg.Refused:
        if hit and now - hit[0] < STALE_SECONDS:
            return hit[1]
        raise
    _CACHE[key] = (now, val)
    return val


def _item(sid, title, artist, cover, dur, audio, url, lic, frm, preview=False):
    return {'id': sid, 'title': _t(title, 120), 'artist': _t(artist, 80), 'cover': _https(cover), 'dur': dur, 'mode': 'stream', 'audio': audio, 'url': url, 'lic': lic, 'from': frm, **({'preview': True} if preview else {})}


# ---------------------------------------------------------------- Jamendo
def cc_label(url):
    """-> (label, may be shown on a page with ads) for a Creative Commons licence address; ('', False) when it is not one."""
    m = re.search(r'creativecommons\.org/licenses/([a-z-]+)/([\d.]+)', str(url or '').lower())
    if m:
        kinds = m.group(1).split('-')
        return 'CC %s %s' % (m.group(1).upper(), m.group(2)), 'nc' not in kinds
    if re.search(r'creativecommons\.org/(publicdomain/zero|publicdomain/mark)', str(url or '').lower()):
        return 'CC0', True
    return '', False


def jamendo_tracks(raw, allow_nc=False):
    """-> (tracks, dropped): the playable ones with a licence that allows them here. `dropped`: how many were left out for their licence."""
    d = _json(raw)
    head = d.get('headers') if isinstance(d, dict) else None
    if not isinstance(head, dict) or head.get('status') != 'success':
        msg = _t((head or {}).get('error_message'), 120) if isinstance(head, dict) else ''
        raise agg.Refused('Jamendo 不接受这次请求：%s' % (msg or '格式不对'), 'Jamendo refused the request: %s' % (msg or 'unexpected answer'))
    out, dropped = [], 0
    for r in d.get('results') or []:
        if not isinstance(r, dict) or not re.fullmatch(r'\d{1,12}', str(r.get('id') or '')) or not r.get('name'):
            continue
        lic, ok = cc_label(r.get('license_ccurl'))
        if not lic or not (ok or allow_nc):
            dropped += 1
            continue
        audio = _https(r.get('audio'))
        if not re.match(r'https://[a-z0-9.-]*jamendo\.com[/?]', audio):
            continue
        out.append(_item('jm:%s' % r['id'], r['name'], r.get('artist_name'), r.get('image') or r.get('album_image'), _secs(r.get('duration')), audio,
                         _https(r.get('shareurl')) or 'https://www.jamendo.com/track/%s' % r['id'], lic, 'Jamendo'))
    return out, dropped


def jamendo_pool(kind, env, now):
    """-> (the songs, how many were left out for their licence), None without a key."""
    key = str((env or {}).get('JAMENDO_CLIENT_ID') or '').strip()
    tags, instrumental = JAMENDO_KINDS[kind]
    if not key:
        return None
    q = {'client_id': key, 'format': 'json', 'limit': '60', 'order': 'popularity_month', 'groupby': 'artist_id', 'include': 'licenses', 'imagesize': '300'}
    if instrumental:
        q['vocalinstrumental'] = 'instrumental'
    url = JAMENDO_URL + '?' + urllib.parse.urlencode(q) + '&fuzzytags=' + '+'.join(tags)
    allow_nc = str((env or {}).get('LISTEN_ALLOW_NC') or '') == '1'
    return pooled('jm:%s:%s' % (kind, allow_nc), now, lambda: jamendo_tracks(_get(url), allow_nc))


# ---------------------------------------------------------------- NetEase (whole songs, only when the owner turned it on)
def netease_tracks(raw):
    """-> (the chart's name, its songs) from NetEase's play list answer."""
    d = _json(raw)
    res = d.get('result') if isinstance(d, dict) else None
    res = res if isinstance(res, dict) else {}
    out = []
    for r in res.get('tracks') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d{1,12}', str(r.get('id') or '')):
            who = ' / '.join(_t(a.get('name'), 40) for a in r.get('artists') or [] if isinstance(a, dict) and a.get('name'))
            out.append(_item('ne:%s' % r['id'], r['name'], who, (r.get('album') or {}).get('picUrl'), _secs(r.get('duration'), 1000), NE_OUTER % r['id'], 'https://music.163.com/#/song?id=%s' % r['id'], '网易云音乐', '网易云音乐'))
    if not out:
        raise agg.Refused('榜单现在是空的', 'The chart is empty right now')
    return _t(res.get('name'), 40), out


def netease_outer(song_id):
    """Is the song open to everybody? NetEase answers the address of a song with a redirect to its sound server when it is, and to its 404 page when it is not (VIP, no rights). Nothing is downloaded."""
    conn = http.client.HTTPSConnection('music.163.com', timeout=6)
    try:
        conn.request('HEAD', '/song/media/outer/url?id=%s.mp3' % song_id, headers={'User-Agent': hotapi.BROWSER['User-Agent']})
        r = conn.getresponse()
        return r.status in (301, 302) and bool(re.match(r'https?://[a-z0-9.-]+\.music\.126\.net/', r.getheader('Location') or ''))
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def netease_pool(cid, env, now):
    """The songs of a chart that are open to everybody, None when the owner has not turned the direct play on."""
    if str((env or {}).get('LISTEN_NETEASE_DIRECT') or '') != '1':
        return None

    def make():
        name, tracks = netease_tracks(_get(NETEASE % cid, {'Referer': 'https://music.163.com/'}))
        top = tracks[:40]
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            open_ = list(ex.map(lambda t: netease_outer(t['id'][3:]), top))
        playable = [t for t, good in zip(top, open_) if good]
        if not playable:
            raise agg.Refused('这个榜上的歌现在都不能直接播放', 'None of the songs of this chart can be played directly right now')
        return playable
    return pooled('ne:%s' % cid, now, make)


# ---------------------------------------------------------------- Apple Music (the previews Apple publishes: 30 seconds of each song, no key)
def apple_ids(raw):
    """The ids of the songs of a chart: of the new feed (feed.results) or of the old per-style one (feed.entry)."""
    d = _json(raw)
    feed = d.get('feed') if isinstance(d, dict) else None
    feed = feed if isinstance(feed, dict) else {}
    ids = [str(r['id']) for r in feed.get('results') or [] if isinstance(r, dict) and re.fullmatch(r'\d{4,14}', str(r.get('id') or ''))]
    entries = feed.get('entry')
    for e in [entries] if isinstance(entries, dict) else entries or []:
        i = (((e or {}).get('id') or {}).get('attributes') or {}).get('im:id') if isinstance(e, dict) else None
        if re.fullmatch(r'\d{4,14}', str(i or '')):
            ids.append(str(i))
    return ids


def apple_tracks(ids, raw):
    """-> the songs of the chart that have a preview, in the chart's order."""
    d = _json(raw)
    by = {}
    for r in (d.get('results') or []) if isinstance(d, dict) else []:
        if not isinstance(r, dict) or r.get('kind') != 'song' or not r.get('trackName'):
            continue
        sound, page = _https(r.get('previewUrl')), _https(r.get('trackViewUrl'))
        if not re.match(r'https://audio-ssl\.itunes\.apple\.com/', sound) or not re.fullmatch(r'\d{4,14}', str(r.get('trackId') or '')):
            continue
        by[str(r['trackId'])] = _item('ap:%s' % r['trackId'], r['trackName'], r.get('artistName'), str(r.get('artworkUrl100') or '').replace('100x100', '300x300'), 30, sound,
                                      page if re.match(r'https://(music|itunes)\.apple\.com/', page) else '', 'Apple Music 30 秒试听', 'Apple Music', True)
    out = [by[i] for i in ids if i in by]
    if not out:
        raise agg.Refused('榜上的歌现在都没有试听', 'None of the songs on the chart has a preview right now')
    return out


def apple_pool(region, now, genre=None):
    def make():
        ids, failed = [], None
        for url in [APPLE_GENRE % (region, genre)] if genre else [APPLE_TOP % region, APPLE_CHART % region]:
            try:
                ids = apple_ids(_get(url))
            except agg.Refused as e:
                failed = e
                continue
            if ids:
                break
        if not ids:
            raise failed or agg.Refused('榜单现在是空的', 'The chart is empty right now')
        return apple_tracks(ids[:50], _get(APPLE_LOOKUP % (','.join(ids[:50]), region)))
    return pooled('ap:%s:%s' % (region, genre or ''), now, make)


# ---------------------------------------------------------------- Deezer (30-second previews, charts by style, play lists; no key)
def deezer_tracks(raw):
    d = _json(raw)
    out = []
    for r in (d.get('data') or []) if isinstance(d, dict) else []:
        if not isinstance(r, dict) or not re.fullmatch(r'\d{1,14}', str(r.get('id') or '')) or not r.get('title'):
            continue
        sound, link = _https(r.get('preview')), _https(r.get('link'))
        if not re.match(r'https://[a-z0-9.-]*dzcdn\.net/', sound):
            continue
        art = r.get('artist') if isinstance(r.get('artist'), dict) else {}
        alb = r.get('album') if isinstance(r.get('album'), dict) else {}
        out.append(_item('dz:%s' % r['id'], r['title'], art.get('name'), alb.get('cover_medium') or alb.get('cover'), 30, sound, link if link.startswith('https://www.deezer.com/') else '', 'Deezer 30 秒试听', 'Deezer', True))
    if not out:
        raise agg.Refused('榜单现在是空的', 'The chart is empty right now')
    return out


def deezer_chart(genre, now):
    return pooled('dz:%s' % genre, now, lambda: deezer_tracks(_get(DEEZER_CHART % genre)))


def deezer_playlist(pid, now):
    return pooled('dzp:%s' % pid, now, lambda: deezer_tracks(_get(DEEZER_LIST % pid)))


def deezer_chart_lists(now):
    """-> [(id, title, number of songs)] of the play lists that are on top at Deezer now."""
    def make():
        d = _json(_get(DEEZER_CHART_LISTS))
        return [(int(r['id']), _t(r.get('title'), 60), _int(r.get('nb_tracks'), 0, 0, 10000)) for r in (d.get('data') or []) if isinstance(r, dict) and re.fullmatch(r'\d{1,14}', str(r.get('id') or '')) and r.get('title')]
    return pooled('dzl', now, make)


# ---------------------------------------------------------------- Audius (whole songs that artists put there for anybody to stream; no key)
def audius_tracks(raw):
    d = _json(raw)
    out = []
    for r in (d.get('data') or []) if isinstance(d, dict) else []:
        if not isinstance(r, dict) or not re.fullmatch(r'[A-Za-z0-9]{3,14}', str(r.get('id') or '')) or not r.get('title') or r.get('is_streamable') is False or r.get('is_delete'):
            continue
        user = r.get('user') if isinstance(r.get('user'), dict) else {}
        art = r.get('artwork') if isinstance(r.get('artwork'), dict) else {}
        perma = str(r.get('permalink') or '')
        out.append(_item('au:%s' % r['id'], r['title'], user.get('name'), art.get('480x480') or art.get('150x150'), _secs(r.get('duration')), '%s/tracks/%s/stream?app_name=%s' % (AUDIUS, r['id'], AUDIUS_APP),
                         ('https://audius.co' + perma) if re.fullmatch(r'/[\w./%~-]{1,300}', perma) else '', 'Audius', 'Audius'))
    if not out:
        raise agg.Refused('榜单现在是空的', 'The chart is empty right now')
    return out


def audius_trending(genre, now):
    url = '%s/tracks/trending?time=week&limit=40&app_name=%s%s' % (AUDIUS, AUDIUS_APP, ('&genre=' + urllib.parse.quote(genre, safe='')) if genre else '')
    return pooled('au:%s' % genre, now, lambda: audius_tracks(_get(url)))


def audius_playlist(pid, now):
    return pooled('aup:%s' % pid, now, lambda: audius_tracks(_get('%s/playlists/%s/tracks?app_name=%s' % (AUDIUS, pid, AUDIUS_APP))))


def audius_chart_lists(now):
    """-> [(id, name, number of songs)] of the play lists that are trending on Audius now."""
    def make():
        d = _json(_get('%s/playlists/trending?time=week&limit=8&app_name=%s' % (AUDIUS, AUDIUS_APP)))
        return [(str(r['id']), _t(r.get('playlist_name'), 60), _int(r.get('track_count'), 0, 0, 10000)) for r in (d.get('data') or [])
                if isinstance(r, dict) and re.fullmatch(r'[A-Za-z0-9]{3,14}', str(r.get('id') or '')) and r.get('playlist_name')]
    return pooled('aul', now, make)


# ---------------------------------------------------------------- YouTube (videos: opened in YouTube's own player, not a sound for the page's player)
def youtube_rows(raw):
    out = []
    for r in hotapi.p_youtube(raw):
        m = re.fullmatch(r'https://www\.youtube\.com/watch\?v=([\w-]{11})', r['url'])
        if m:
            out.append({'id': 'yt:%s' % m.group(1), 'title': _t(r['title'], 120), 'artist': _t(r.get('note'), 80), 'cover': _https(r.get('img')), 'dur': 0, 'mode': 'embed', 'url': r['url'], 'from': 'YouTube', 'net': 'global'})
    if not out:
        raise agg.Refused('榜单现在是空的', 'The chart is empty right now')
    return out


def youtube_pool(env, now):
    key = str((env or {}).get('YOUTUBE_API_KEY') or '').strip()
    if not key:
        return None
    region = str((env or {}).get('YOUTUBE_REGION') or 'US').strip().upper()
    region = region if region in hotapi.YT_REGIONS else 'US'
    url = YOUTUBE % (region, urllib.parse.quote(key, safe=''))
    return pooled('yt:%s' % region, now, lambda: youtube_rows(_get(url)))


# ---------------------------------------------------------------- the lists
def extra_groups():
    """The owner's own lists from registry/listen.json: {"groups": [{"id": "mine", "name": "我的", "name_en": "Mine", "kind": "style", "sources": [["deezer", 152], ["audius", "Rock"]]}]}.
    An entry that is not valid is skipped. A source is [kind, what to ask for] (see SOURCE_ARGS); `kind` is chart, list or style."""
    cfg = agg.read_json(os.path.join(os.path.dirname(agg.reg_path()), 'listen.json'), {})
    out = []
    for c in (cfg.get('groups') if isinstance(cfg, dict) else None) or []:
        if not isinstance(c, dict) or not re.fullmatch(r'x-[a-z0-9-]{1,17}', str(c.get('id') or '')) or not agg.clean(c.get('name'), 30) or c.get('kind') not in ('chart', 'list', 'style'):
            continue
        srcs = []
        for s in c.get('sources') or []:
            try:
                kind, args = str(s[0]), list(s[1:])
                want = SOURCE_ARGS[kind]
                if len(args) != len(want):
                    continue
                spec = (kind,) + tuple(w(a) for w, a in zip(want, args))
                if kind in ('apple', 'apple_genre') and spec[1] not in APPLE_REGIONS or kind == 'jamendo' and spec[1] not in JAMENDO_KINDS or kind in ('audius', 'audius_pl') and not re.fullmatch(r'[A-Za-z0-9 /&-]{0,30}', spec[1]):
                    continue
                srcs.append(spec)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        if srcs:
            out.append(_g(c['id'], agg.clean(c['name'], 30), agg.clean(c.get('name_en'), 40) or agg.clean(c['name'], 30), c['kind'], srcs, 'mix', 30, c['kind'] == 'style'))
    return out


def available(spec, env):
    kind = spec[0]
    if kind == 'jamendo':
        return bool(str((env or {}).get('JAMENDO_CLIENT_ID') or '').strip())
    if kind == 'youtube':
        return bool(str((env or {}).get('YOUTUBE_API_KEY') or '').strip())
    if kind == 'netease':
        return str((env or {}).get('LISTEN_NETEASE_DIRECT') or '') == '1'
    return True


def groups(env, now, dynamic=True):
    """-> every list of today, with its sources (the index of the page is these without them). dynamic: also the play lists that are on top at Deezer and Audius today (two more questions, asked in parallel and given 2.5 seconds)."""
    off = {x.strip() for x in str((env or {}).get('LISTEN_DISABLE') or '').lower().split(',') if x.strip()}
    out = STATIC + extra_groups()
    dyn = []
    if dynamic:
        ex = concurrent.futures.ThreadPoolExecutor(2)
        futs = {'dz': ex.submit(deezer_chart_lists, now), 'au': ex.submit(audius_chart_lists, now)}
        concurrent.futures.wait(futs.values(), timeout=2.5)
        ex.shutdown(wait=False, cancel_futures=True)
        seen = {g['id'] for g in out}
        f = futs['dz']
        if f.done() and not f.exception():
            for pid, title, n in f.result()[:6]:
                if 'dzp-%s' % pid not in seen:
                    dyn.append(dict(_g('dzp-%s' % pid, title, title, 'list', [('deezer_pl', pid)], 'first', 50, False), n=n))
        f = futs['au']
        if f.done() and not f.exception():
            for pid, title, n in f.result()[:3]:
                dyn.append(dict(_g('aup-%s' % pid, title, title, 'list', [('audius_pl', pid)], 'first', 50, False), n=n))
    for cid, zh, en in NE_LISTS:
        dyn.append(_g('nep-%s' % cid, '网易云 · ' + zh, 'NetEase · ' + en, 'list', [('netease', cid)], 'first', 50, False))
    out += dyn
    out = [g for g in out if g['id'] not in off and any(available(s, env) for s in g['sources'])]
    return sorted(out, key=lambda g: KIND_ORDER[g['kind']])                       # (a stable sort: inside a kind the order above stays)


def fetch_source(spec, env, now):
    """-> the songs of one source (a list), None when it is off (no key, direct play not turned on). Raises agg.Refused."""
    kind = spec[0]
    if not available(spec, env):
        return None
    if kind == 'apple':
        return apple_pool(spec[1], now)
    if kind == 'apple_genre':
        return apple_pool(spec[1], now, spec[2])
    if kind == 'deezer':
        return deezer_chart(spec[1], now)
    if kind == 'deezer_pl':
        return deezer_playlist(spec[1], now)
    if kind == 'audius':
        return audius_trending(spec[1], now)
    if kind == 'audius_pl':
        return audius_playlist(spec[1], now)
    if kind == 'jamendo':
        got = jamendo_pool(spec[1], env, now)
        return got[0] if got else None
    if kind == 'netease':
        return netease_pool(spec[1], env, now)
    if kind == 'youtube':
        return youtube_pool(env, now)
    return None


def _norm(s):
    return re.sub(r'[\W_]+', '', str(s).lower())


def combine(lists, seed, count, shuffle, per_artist):
    """One list out of several: round by round one song from each source, never the same song twice and never more than `per_artist` songs of one artist."""
    pools = [shuffled(l, '%s|%d' % (seed, i)) if shuffle else list(l) for i, l in enumerate(lists) if l]
    out, seen, per = [], set(), {}
    while len(out) < count and any(pools):
        for p in pools:
            while p:
                x = p.pop(0)
                key, who = (_norm(x['title']), _norm(x['artist'])), _norm(x['artist'])
                if key in seen or (who and per.get(who, 0) >= per_artist):
                    continue
                seen.add(key)
                per[who] = per.get(who, 0) + 1
                out.append(x)
                break
            if len(out) >= count:
                break
    return out


def fetch_group(g, env, now, report=None, deadline=None):
    """-> the songs of a list today. The sources are asked at the same time; one that has not answered in `deadline` seconds is left out. `report` gets a line for each source."""
    deadline = GROUP_SECONDS if deadline is None else deadline
    ex = concurrent.futures.ThreadPoolExecutor(max(1, min(8, len(g['sources']))))
    futs = [(s, ex.submit(fetch_source, s, env, now)) for s in g['sources']]
    concurrent.futures.wait([f for _, f in futs], timeout=deadline)
    ex.shutdown(wait=False, cancel_futures=True)
    got = []                                                                       # [(provider, songs)] of the sources that gave songs, in the order of the list
    for s, f in futs:
        name = '%s:%s' % (s[0], ','.join(str(a) for a in s[1:]))
        line = {'group': g['id'], 'source': name}
        if not f.done():
            line.update(ok=False, error='timeout')
        elif f.exception() is not None:
            e = f.exception()
            line.update(ok=False, error=str(e)[:160] if isinstance(e, agg.Refused) else 'error')
        else:
            r = f.result()
            if r is None:
                line.update(ok=False, error='off')
            else:
                line.update(ok=True, count=len(r))
                got.append((PROVIDERS[s[0]], r))
        if report is not None:
            report.append(line)
    day = day_text(now)
    if g['policy'] == 'first':                                                     # the first source that gave something worth a list (the others are the back-up)
        got = [p for p in got if len(p[1]) >= 3][:1]
    elif g['policy'] == 'fill':                                                    # one source after the other until the list is full (the first is the one wanted, the others fill what it lacks)
        got = [p for p in got if p[1]]
        flat = [x for _, l in got for x in l]
        got = [(got[0][0], flat)] + [(name, []) for name, _ in got[1:]] if got else []
    items = combine([l for _, l in got], '%s|%s' % (g['id'], day), g['count'], g['shuffle'], g['per_artist'])
    return items, list(dict.fromkeys(x['from'] for x in items))                   # (the sources whose songs are in the list, in the order they first appear)


def public(g):
    return {k: g[k] for k in ('id', 'name', 'name_en', 'kind', 'net') if k in g} | ({'n': g['n']} if g.get('n') else {})


# ---------------------------------------------------------------- the answer
def _fail(status, error, zh, en):
    return status, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': error, 'message': zh, 'message_en': en}, ensure_ascii=False).encode('utf-8'), 'no-store'


def _ok(obj, cache=CDN):
    return 200, 'application/json; charset=utf-8', json.dumps({'ok': True, **obj}, ensure_ascii=False).encode('utf-8'), cache


def diag(env, now):
    keys = {k: bool(str((env or {}).get(k) or '').strip()) for k in ('JAMENDO_CLIENT_ID', 'YOUTUBE_API_KEY')}
    keys['LISTEN_NETEASE_DIRECT'] = str((env or {}).get('LISTEN_NETEASE_DIRECT') or '') == '1'
    gs = groups(env, now)
    report, sizes = [], {}
    for gid in ('daily', 'hot-cn', 'hot-west', 'rock', 'chill'):
        g = next((x for x in gs if x['id'] == gid), None)
        if g:
            sizes[gid] = len(fetch_group(g, env, now, report)[0])
    return {'keys': keys, 'groups': len(gs), 'songs': sizes, 'sources': report}


def handle(query, env, now=None):
    """-> (status, content type, body bytes, cache header). `query`: what parse_qs makes of the part after "?"."""
    now = time.time() if now is None else now
    if str((env or {}).get('LISTEN') or '') == '0':
        return _fail(404, 'off', '这个功能没有开启', 'Not turned on')
    if (query.get('diag') or [''])[0]:
        return _ok(diag(env, now), 'no-store')
    gid = (query.get('g') or [''])[0]
    gs = groups(env, now, dynamic=not gid or bool(re.match(r'(dzp|aup)-', gid)) and gid not in {g['id'] for g in STATIC})
    if not gid:
        return _ok({'date': day_text(now), 'updated': int(now), 'groups': [public(g) for g in gs]})
    g = next((x for x in gs if x['id'] == gid), None)
    if g is None:
        return _fail(404, 'none', '没有这个榜单', 'No such list')
    items, names = fetch_group(g, env, now)
    if not items:
        return _fail(502, 'empty', '这个榜单现在读不到', 'This list cannot be read right now')
    return _ok({'date': day_text(now), 'updated': int(now), 'group': {**public(g), 'sub': '来源：' + ' · '.join(names), 'sub_en': 'From ' + ' · '.join(names), 'items': items}})


# ---------------------------------------------------------------- the check to run on your own computer
def _call(url, data=None, headers=None):
    """-> (HTTP status, the JSON answer or {}). For the checks of the keys only: the addresses are fixed ones, and a key never comes back in what is shown."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read(1000000) or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(100000) or b'{}')
        except ValueError:
            return e.code, {}
    except (OSError, ValueError, http.client.HTTPException):
        return 0, {}


YT_HINTS = {'keyInvalid': '密钥不对（复制时多了空格或少了字符？）', 'badRequest': '密钥不对，或请求被拒', 'accessNotConfigured': '这个 Google Cloud 项目里没有启用 YouTube Data API v3', 'SERVICE_DISABLED': '这个 Google Cloud 项目里没有启用 YouTube Data API v3',
            'quotaExceeded': '今天的配额用完了（每天 1 万点，太平洋时间午夜重置）', 'dailyLimitExceeded': '今天的配额用完了', 'ipRefererBlocked': '密钥做了 IP / 网址限制，Vercel 和你的电脑都不在白名单里',
            'API_KEY_IP_ADDRESS_BLOCKED': '密钥做了 IP 限制，这台电脑不在白名单里', 'API_KEY_HTTP_REFERRER_BLOCKED': '密钥做了网址限制，这里的请求不带网址', 'API_KEY_SERVICE_BLOCKED': '密钥限制了可用的 API，没有勾 YouTube Data API v3'}


def youtube_check(env, say):
    key = str(env.get('YOUTUBE_API_KEY') or '').strip()
    if not key:
        say('  YouTube：YOUTUBE_API_KEY 没设置，跳过')
        return
    region = str(env.get('YOUTUBE_REGION') or 'US').strip().upper()
    st, d = _call('https://www.googleapis.com/youtube/v3/videos?part=snippet&chart=mostPopular&videoCategoryId=10&maxResults=3&regionCode=%s&key=%s' % (region if region in hotapi.YT_REGIONS else 'US', urllib.parse.quote(key, safe='')))
    if st == 200:
        say('  YouTube：钥匙有效，音乐分类的热门读到 %d 条（视频只能用官方嵌入播放器看，不能只取声音）' % len((d.get('items') or []) if isinstance(d, dict) else []))
        return
    err = d.get('error') if isinstance(d, dict) and isinstance(d.get('error'), dict) else {}
    why = ''
    for e in err.get('errors') or []:
        why = why or str((e or {}).get('reason') or '')
    for e in err.get('details') or []:
        why = why or str((e or {}).get('reason') or '')
    say('  YouTube：不行（HTTP %s %s）：%s' % (st or '连不上', why, YT_HINTS.get(why) or str(err.get('message') or '连不上 googleapis.com（这台电脑要能访问 Google）')[:100]))


def spotify_check(env, say):
    cid, sec = str(env.get('SPOTIFY_CLIENT_ID') or '').strip(), str(env.get('SPOTIFY_CLIENT_SECRET') or '').strip()
    if not (cid and sec):
        say('  Spotify：SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET 没设置，跳过')
        return
    auth = base64.b64encode(('%s:%s' % (cid, sec)).encode('utf-8')).decode('ascii')
    st, d = _call('https://accounts.spotify.com/api/token', b'grant_type=client_credentials', {'Authorization': 'Basic ' + auth, 'Content-Type': 'application/x-www-form-urlencoded'})
    tok = d.get('access_token') if isinstance(d, dict) else ''
    if not tok:
        say('  Spotify：换不到令牌（HTTP %s）：Client ID / Secret 不对，或这个应用被停用' % (st or '连不上'))
        return
    head = {'Authorization': 'Bearer ' + tok}
    s1, d1 = _call('https://api.spotify.com/v1/search?q=jay%20chou&type=track&limit=5&market=US', headers=head)
    tracks = [t for t in ((d1.get('tracks') or {}).get('items') or []) if isinstance(t, dict)] if isinstance(d1, dict) else []
    prev = sum(1 for t in tracks if t.get('preview_url'))
    s2, _ = _call('https://api.spotify.com/v1/browse/new-releases?limit=5', headers=head)
    say('  Spotify：令牌换到了。搜索 HTTP %s，读到 %d 首，其中带试听地址的 %d 首；“新发行”接口 HTTP %s%s' % (s1, len(tracks), prev, s2, '（被拒：这个应用拿不到新发行）' if s2 in (403, 404, 410) else ''))
    say('       站内播放条只能放“试听地址”；%s' % ('有，可以接。' if prev else '没有（新建的应用这个字段是空的），Spotify 只能用它的官方嵌入播放器。'))


PROBE_GROUPS = ('daily', 'hot-cn', 'hot-west', 'rock', 'hiphop', 'classical', 'chill', 'video')


def probe(env, now=None, say=print, deadline=None):
    """The check to run on your own computer before (or after) deploying: `python -X utf8 listen.py` with the keys in the environment. Says which keys are set and valid (and why not), what each
    source gave for a few lists, and, for one song of each provider, whether its sound can be fetched from here. Nothing secret is printed. -> True when every provider that gave songs gave a sound."""
    now = time.time() if now is None else now
    env = env or {}
    say('今日听什么 · 自检（%s，中国时间）' % day_text(now))
    for k in ('JAMENDO_CLIENT_ID', 'YOUTUBE_API_KEY', 'SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET'):
        say('  %-20s %s' % (k, '已设置' if str(env.get(k) or '').strip() else '没设置'))
    say('  %-20s %s' % ('LISTEN_NETEASE_DIRECT', '1：网易云的歌用站内播放条放整首' if str(env.get('LISTEN_NETEASE_DIRECT') or '') == '1' else '没开：网易云的歌不出现'))
    youtube_check(env, say)
    spotify_check(env, say)
    gs = groups(env, now)
    say('今天有 %d 个榜单 / 歌单 / 风格。' % len(gs))
    firsts = {}
    for gid in PROBE_GROUPS:
        g = next((x for x in gs if x['id'] == gid), None)
        if not g:
            say('  [%s] 没有（它的来源都没开）' % gid)
            continue
        report = []
        items, names = fetch_group(g, env, now, report, deadline)
        for r in report:
            say('  [%s] %s：%s' % (gid, r['source'], ('读到 %d 首' % r['count']) if r['ok'] else ('没读到（%s）' % r['error'])))
        say('  → %s：%d 首，来源 %s' % (g['name'], len(items), '、'.join(names) or '无'))
        for x in items:
            if x['mode'] == 'stream' and x['from'] not in firsts:
                firsts[x['from']] = x
    good = bool(firsts)
    for frm, x in firsts.items():
        if x['audio'].startswith('https://music.163.com/song/media/outer/'):
            say('  声音（%s）：服务已确认这首歌会给出声音地址；真能不能放，用 python -X utf8 listen.py --browser 在浏览器里试' % frm)
            continue
        try:
            _, h, body = agg.fetch(x['audio'], 'audio/*,*/*;q=0.5', 4096, headers={'Range': 'bytes=0-1023', **hotapi.BROWSER}, truncate=True)
            kind = h.get('content-type', '?')
            ok = kind.startswith('audio/') or 'octet-stream' in kind
            say('  声音（%s，%s）：%s，%s，%d 字节' % (frm, x['title'][:24], '可以' if ok else '不像声音', kind, len(body)))
            good = good and ok
        except agg.Refused as e:
            say('  声音（%s，%s）：失败，%s' % (frm, x['title'][:24], e))
            good = False
    if not firsts:
        say('今天一首能在站内播的歌都没有：上面“没读到”的原因就是答案。')
    say('结论：' + ('这些来源都能用。' if good else '有问题，见上面。'))
    return good


PROBE_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>今日听什么 · 在浏览器里试播</title>
<style>body{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#222}h2{margin:22px 0 6px;font-size:17px}ul{padding-left:20px}li{margin:3px 0}.ok{color:#0a7f3f}.bad{color:#c0392b}.wait{color:#888}code{background:#eee;padding:0 4px;border-radius:3px}iframe{border:0;display:block;margin:6px 0}</style></head><body>
<h1>今日听什么 · 在你的浏览器里试播</h1>
<p>每个来源的前几首歌，都会在这个页面里用 <code>&lt;audio&gt;</code> 真放一下（和站内播放条是同一种方式，用的是你的网络和浏览器）。<b class="ok">✓</b> 是真的放出声音了，<b class="bad">✗</b> 后面写着原因。站点是 https 的：Apple、Deezer、Audius、Jamendo 的地址本来就是 https，网易云的地址浏览器会自动升级。</p>
<div id="out">读取中……</div><p id="sum"></p>
<script>
const TEST=src=>new Promise(res=>{const a=new Audio();let done=false;const fin=r=>{if(done)return;done=true;try{a.pause();}catch(e){}res(r);};
  a.addEventListener('playing',()=>setTimeout(()=>fin({ok:true,dur:isFinite(a.duration)?a.duration:0}),1500));
  a.addEventListener('error',()=>fin({ok:false,msg:(a.error&&a.error.message)||'error '+(a.error&&a.error.code)}));
  a.src=src;const p=a.play();if(p&&p.catch)p.catch(e=>{if(!done)setTimeout(()=>fin({ok:false,msg:String(e).slice(0,80)}),500);});setTimeout(()=>fin({ok:false,msg:'15 秒内没有开始播放'}),15000);});
(async()=>{
  const d=await (await fetch('/probe.json')).json(), out=document.getElementById('out'); out.textContent='';
  let tried=0,good=0;
  if(!Object.keys(d.providers).length) out.textContent='今天一首能在站内播的歌都没有（见终端里的原因）。';
  for(const [name,items] of Object.entries(d.providers)){
    const h=document.createElement('h2');h.textContent=name;out.appendChild(h);
    const ul=document.createElement('ul');out.appendChild(ul);
    for(const i of items){
      const li=document.createElement('li');li.className='wait';li.textContent='试播：'+i.title+' — '+i.artist+' ……';ul.appendChild(li);tried++;
      const r=await TEST(i.audio);li.className=r.ok?'ok':'bad';
      li.textContent=(r.ok?'✓ ':'✗ ')+i.title+' — '+i.artist+(r.ok?'（放出声音了'+(r.dur?'，全长 '+Math.round(r.dur)+' 秒':'')+'）':'（放不出来：'+r.msg+'）');if(r.ok)good++;
    }
  }
  if(d.video){const h=document.createElement('h2');h.textContent='YouTube（只能内嵌视频）';out.appendChild(h);
    const f=document.createElement('iframe');f.width=320;f.height=180;f.allow='encrypted-media';f.src='https://www.youtube-nocookie.com/embed/'+d.video;out.appendChild(f);
    const p=document.createElement('p');p.textContent='能看到画面、能点播放，就说明你的网络能用 YouTube 的嵌入播放器。';out.appendChild(p);}
  document.getElementById('sum').textContent='结果：'+good+' / '+tried+' 首放出了声音。';
})();
</script></body></html>"""


def serve_probe(env, port=8899):
    """`python -X utf8 listen.py --browser`: a page on this computer that really plays the first songs of each provider in a browser (your own network, your own browser)."""
    import http.server
    import webbrowser
    now = time.time()
    gs = groups(env, now)
    providers, video = {}, ''
    for gid in PROBE_GROUPS:
        g = next((x for x in gs if x['id'] == gid), None)
        if not g:
            continue
        items, _ = fetch_group(g, env, now)
        for x in items:
            if x['mode'] == 'stream' and len(providers.setdefault(x['from'], [])) < 2 and x not in providers[x['from']]:
                providers[x['from']].append(x)
            elif x['mode'] == 'embed' and x['from'] == 'YouTube' and not video:
                video = x['id'][3:]
    payload = json.dumps({'providers': providers, 'video': video}, ensure_ascii=False).encode('utf-8')
    page = PROBE_PAGE.encode('utf-8')

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, ctype = (payload, 'application/json; charset=utf-8') if self.path.startswith('/probe.json') else (page, 'text/html; charset=utf-8')
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = 'http://127.0.0.1:%d/' % port
    print('在浏览器里打开 %s 看试播结果（已经替你打开了）；看完按 Ctrl+C 结束。' % url)
    try:
        webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    if '--browser' in (sys.argv[1:] if argv is None else argv):
        probe(os.environ)
        serve_probe(os.environ)
    else:
        probe(os.environ)


def respond(h, env, method):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/listen.py and by the tests)."""
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
    h.send_header('Access-Control-Allow-Origin', '*')          # public and read-only: any page may read what is on
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    h.wfile.write(body)


if __name__ == '__main__':
    main()
