"""Today's listening: GET /api/listen -> scenes of songs and play lists that change every day, each one playable in the page.
GET /api/listen?diag=1 -> for each source whether it answered and how much it gave (nothing secret in it: the keys never leave the server).

What a scene is made of (registry/listen.json can replace the built-in scenes; see DEFAULT_SCENES):
  - `jamendo`: tracks of Jamendo (free music under Creative Commons) found by tags. They are played by the page itself, in its player bar at the foot, one after the other.
    Needs JAMENDO_CLIENT_ID (free to register). A track whose licence forbids commercial use is left out (this site shows ads) unless LISTEN_ALLOW_NC=1; one whose licence is not known is left out.
  - `apple`: the songs of Apple Music's most-played chart of a country (a different country each day), with the 30-second previews Apple publishes for exactly this (iTunes lookup, no key).
    They are played by the page's own player, one after the other; the row says "30-second preview" and links to the song on Apple Music.
  - `netease`: the songs of one of NetEase Cloud Music's own charts, a different chart each day. By default NetEase's own player plays them in a window of the page (no key; the songs the
    platform does not let others play stay silent in its player). With LISTEN_NETEASE_DIRECT=1 the page's own player plays the whole song instead, from the address `music.163.com/song/media/outer/url`
    (a redirect to NetEase's sound server that works in a browser for the songs that are open to everybody; each song is checked first, the others are left out). That address is not a documented API and
    the music is the rights holders': it is off until the owner turns it on, knowing that.
  - `playlists`: whole play lists (NetEase or Spotify), a few of them each day; played by the platform's own player in a window. No key: Spotify and NetEase publish the player, not the list.
  - `youtube`: the most popular music videos (YouTube's chart of the Music category, the country is YOUTUBE_REGION, else US). Needs YOUTUBE_API_KEY. Played by YouTube's own player.
A scene whose source is off (no key) or does not answer is left out; the others stay. "Different every day" = the day (in China) is the seed of a fixed shuffle, so every reader sees the same
list on the same day, and nothing is stored: each answer is made from what the sources said in the last hours (an in-memory copy per warm instance, and the CDN keeps the answer for 30 minutes).
Spotify and YouTube cannot be opened from mainland China; an item of theirs says so (`net: global`) and the page marks it.
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
CDN = 'public, max-age=600, s-maxage=1800, stale-while-revalidate=21600'
JAMENDO = 'https://api.jamendo.com/v3.0/tracks/'
NETEASE = 'https://music.163.com/api/playlist/detail?id=%s'
YOUTUBE = 'https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&videoCategoryId=10&maxResults=40&regionCode=%s&key=%s'
APPLE_CHART = 'https://rss.applemarketingtools.com/api/v2/%s/music/most-played/25/songs.json'
APPLE_LOOKUP = 'https://itunes.apple.com/lookup?id=%s&country=%s'
NE_OUTER = 'https://music.163.com/song/media/outer/url?id=%s.mp3'
APPLE_REGIONS = {'cn': ('中国', 'China'), 'us': ('美国', 'US'), 'jp': ('日本', 'Japan'), 'kr': ('韩国', 'Korea'), 'tw': ('台湾', 'Taiwan'), 'hk': ('香港', 'Hong Kong'), 'gb': ('英国', 'UK'), 'de': ('德国', 'Germany'), 'fr': ('法国', 'France')}
_CACHE = {}

# NetEase's own charts (ids checked against the platform) and a few Spotify play lists (checked with Spotify's oEmbed).
NE_CHARTS = [(3778678, '热歌榜', 'Hot songs'), (3779629, '新歌榜', 'New songs'), (19723756, '飙升榜', 'Rising songs'), (2884035, '原创榜', 'Original songs'), (2809513713, '欧美热歌榜', 'Western hits')]
DEFAULT_SCENES = [
    {'id': 'hits', 'name': '今日热歌', 'name_en': 'Today’s hits', 'netease': {'charts': [c[0] for c in NE_CHARTS], 'count': 12}},
    {'id': 'apple', 'name': 'Apple Music 热歌', 'name_en': 'Apple Music hits', 'apple': {'regions': ['cn', 'us', 'jp', 'kr'], 'count': 12}},
    {'id': 'relax', 'name': '摸鱼放松', 'name_en': 'Unwind', 'jamendo': {'tags': ['chillout', 'lounge', 'ambient', 'downtempo'], 'instrumental': False}},
    {'id': 'focus', 'name': '专注阅读', 'name_en': 'Focus', 'jamendo': {'tags': ['piano', 'classical', 'jazz', 'instrumental'], 'instrumental': True}},
    {'id': 'sleep', 'name': '冥想睡眠', 'name_en': 'Sleep & meditation', 'jamendo': {'tags': ['ambient', 'meditation', 'sleep', 'newage'], 'instrumental': True}},
    {'id': 'lists', 'name': '今日歌单', 'name_en': 'Play lists of the day', 'playlists': {'pick': 4, 'items': [
        {'src': 'netease', 'id': '71384707', 'title': '网易云古典榜', 'title_en': 'NetEase classical chart'}, {'src': 'netease', 'id': '1978921795', 'title': '网易云电音榜', 'title_en': 'NetEase electronic chart'},
        {'src': 'netease', 'id': '991319590', 'title': '网易云中文说唱榜', 'title_en': 'NetEase Chinese rap chart'}, {'src': 'netease', 'id': '71385702', 'title': '网易云 ACG 榜', 'title_en': 'NetEase ACG chart'},
        {'src': 'netease', 'id': '745956260', 'title': '网易云韩语榜', 'title_en': 'NetEase Korean chart'}, {'src': 'netease', 'id': '2809577409', 'title': '网易云欧美新歌榜', 'title_en': 'NetEase Western new songs chart'},
        {'src': 'netease', 'id': '3812895', 'title': 'Beatport 全球电子舞曲榜', 'title_en': 'Beatport global dance chart'}, {'src': 'netease', 'id': '60198', 'title': '美国 Billboard 榜', 'title_en': 'US Billboard chart'}]}},
    {'id': 'world', 'name': '全球新声', 'name_en': 'Worldwide', 'net': 'global', 'youtube': {'count': 10}, 'playlists': {'pick': 2, 'items': [
        {'src': 'spotify', 'id': '37i9dQZF1DXcBWIGoYBM5M', 'title': 'Today’s Top Hits', 'title_en': 'Today’s Top Hits'}, {'src': 'spotify', 'id': '37i9dQZF1DX4JAvHpjipBk', 'title': 'New Music Friday', 'title_en': 'New Music Friday'},
        {'src': 'spotify', 'id': '37i9dQZF1DX0XUsuxWHRQd', 'title': 'RapCaviar', 'title_en': 'RapCaviar'}, {'src': 'spotify', 'id': '37i9dQZF1DWWQRwui0ExPn', 'title': 'lofi beats', 'title_en': 'lofi beats'}]}},
]
LIST_URL = {'netease': 'https://music.163.com/#/playlist?id=%s', 'spotify': 'https://open.spotify.com/playlist/%s'}
LIST_ID = {'netease': r'\d{4,12}', 'spotify': r'[A-Za-z0-9]{22}'}
LIST_FROM = {'netease': '网易云音乐', 'spotify': 'Spotify'}


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
        out.append({'id': 'jm:%s' % r['id'], 'title': _t(r['name'], 120), 'artist': _t(r.get('artist_name'), 80), 'cover': _https(r.get('image') or r.get('album_image')), 'dur': _secs(r.get('duration')),
                    'mode': 'stream', 'audio': audio, 'url': _https(r.get('shareurl')) or 'https://www.jamendo.com/track/%s' % r['id'], 'lic': lic, 'from': 'Jamendo'})
    return out, dropped


def jamendo_pool(spec, env, now):
    key = str((env or {}).get('JAMENDO_CLIENT_ID') or '').strip()
    if not key:
        return None
    tags = [t for t in spec.get('tags') or [] if re.fullmatch(r'[a-z0-9]{2,24}', str(t))]
    q = {'client_id': key, 'format': 'json', 'limit': '60', 'order': 'popularity_month', 'groupby': 'artist_id', 'include': 'licenses', 'imagesize': '300'}
    if spec.get('instrumental'):
        q['vocalinstrumental'] = 'instrumental'
    url = JAMENDO + '?' + urllib.parse.urlencode(q) + ('&fuzzytags=' + '+'.join(tags) if tags else '')
    allow_nc = str((env or {}).get('LISTEN_ALLOW_NC') or '') == '1'
    return pooled('jm:%s:%s:%s' % ('+'.join(tags), spec.get('instrumental'), allow_nc), now, lambda: jamendo_tracks(_get(url), allow_nc))


# ---------------------------------------------------------------- NetEase
def netease_tracks(raw):
    """-> (the chart's name, its songs) from NetEase's play list answer."""
    d = _json(raw)
    res = d.get('result') if isinstance(d, dict) else None
    res = res if isinstance(res, dict) else {}
    out = []
    for r in res.get('tracks') or []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d{1,12}', str(r.get('id') or '')):
            who = ' / '.join(_t(a.get('name'), 40) for a in r.get('artists') or [] if isinstance(a, dict) and a.get('name'))
            out.append({'id': 'ne:%s' % r['id'], 'title': _t(r['name'], 120), 'artist': who[:80], 'cover': _https((r.get('album') or {}).get('picUrl')), 'dur': _secs(r.get('duration'), 1000),
                        'mode': 'embed', 'url': 'https://music.163.com/#/song?id=%s' % r['id'], 'from': '网易云音乐'})
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


def netease_pool(cid, now, direct=False):
    """The chart's name and songs. `direct`: only the songs that are open to everybody, each one as a sound the page's own player plays (LISTEN_NETEASE_DIRECT=1)."""
    def make():
        name, tracks = netease_tracks(_get(NETEASE % cid, {'Referer': 'https://music.163.com/'}))
        if not direct:
            return name, tracks
        top = tracks[:40]
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            open_ = list(ex.map(lambda t: netease_outer(t['id'][3:]), top))
        playable = [dict(t, mode='stream', audio=NE_OUTER % t['id'][3:], lic='网易云音乐') for t, good in zip(top, open_) if good]
        if not playable:
            raise agg.Refused('这个榜上的歌现在都不能直接播放', 'None of the songs of this chart can be played directly right now')
        return name, playable
    return pooled('ne:%s:%s' % (cid, direct), now, make)


# ---------------------------------------------------------------- Apple Music (the previews Apple publishes: 30 seconds of each song, no key)
def apple_ids(raw):
    d = _json(raw)
    rows = ((d.get('feed') or {}).get('results') or []) if isinstance(d, dict) else []
    return [str(r['id']) for r in rows if isinstance(r, dict) and re.fullmatch(r'\d{4,14}', str(r.get('id') or ''))]


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
        by[str(r['trackId'])] = {'id': 'ap:%s' % r['trackId'], 'title': _t(r['trackName'], 120), 'artist': _t(r.get('artistName'), 80), 'cover': _https(str(r.get('artworkUrl100') or '').replace('100x100', '300x300')), 'dur': 30,
                                 'mode': 'stream', 'audio': sound, 'url': page if re.match(r'https://(music|itunes)\.apple\.com/', page) else '', 'lic': 'Apple Music 30 秒试听', 'from': 'Apple Music', 'preview': True}
    out = [by[i] for i in ids if i in by]
    if not out:
        raise agg.Refused('榜上的歌现在都没有试听', 'None of the songs on the chart has a preview right now')
    return out


def apple_pool(region, now):
    def make():
        ids = apple_ids(_get(APPLE_CHART % region))
        if not ids:
            raise agg.Refused('榜单现在是空的', 'The chart is empty right now')
        return apple_tracks(ids, _get(APPLE_LOOKUP % (','.join(ids), region)))
    return pooled('ap:%s' % region, now, make)


# ---------------------------------------------------------------- YouTube
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


# ---------------------------------------------------------------- the scenes
def scenes_config():
    """The built-in scenes, or the owner's from registry/listen.json ({"scenes": [...]} in the shape of DEFAULT_SCENES); an entry that is not valid is skipped, a file with none is ignored."""
    cfg = agg.read_json(os.path.join(os.path.dirname(agg.reg_path()), 'listen.json'), {})
    out, seen = [], set()
    for c in (cfg.get('scenes') if isinstance(cfg, dict) else None) or []:
        if not isinstance(c, dict) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,19}', str(c.get('id') or '')) or c['id'] in seen or not agg.clean(c.get('name'), 30):
            continue
        s = {'id': c['id'], 'name': agg.clean(c['name'], 30), 'name_en': agg.clean(c.get('name_en'), 40) or agg.clean(c['name'], 30), 'net': 'global' if c.get('net') == 'global' else 'cn'}
        if isinstance(c.get('jamendo'), dict):
            s['jamendo'] = {'tags': [str(x) for x in c['jamendo'].get('tags') or []][:8], 'instrumental': bool(c['jamendo'].get('instrumental'))}
        if isinstance(c.get('netease'), dict):
            s['netease'] = {'charts': [int(x) for x in c['netease'].get('charts') or [] if re.fullmatch(r'\d{4,12}', str(x))][:12], 'count': _int(c['netease'].get('count'), 12, 1, 30)}
        if isinstance(c.get('youtube'), dict):
            s['youtube'] = {'count': _int(c['youtube'].get('count'), 10, 1, 30)}
        if isinstance(c.get('apple'), dict):
            regs = [r for r in (str(x).lower() for x in c['apple'].get('regions') or []) if r in APPLE_REGIONS][:9]
            if regs:
                s['apple'] = {'regions': regs, 'count': _int(c['apple'].get('count'), 12, 1, 25)}
        if isinstance(c.get('playlists'), dict):
            lists = [{'src': p['src'], 'id': str(p['id']), 'title': agg.clean(p.get('title'), 60), 'title_en': agg.clean(p.get('title_en'), 60) or agg.clean(p.get('title'), 60)}
                     for p in c['playlists'].get('items') or [] if isinstance(p, dict) and p.get('src') in LIST_URL and re.fullmatch(LIST_ID[p['src']], str(p.get('id') or '')) and agg.clean(p.get('title'), 60)]
            if lists:
                s['playlists'] = {'pick': _int(c['playlists'].get('pick'), 4, 1, 12), 'items': lists}
        if any(k in s for k in ('jamendo', 'netease', 'youtube', 'playlists', 'apple')):
            seen.add(c['id'])
            out.append(s)
    return out or DEFAULT_SCENES


def _scene(sc, env, now, report):
    """-> the items of a scene today ([] when its sources are off or did not answer); `report` gets one line per source."""
    day, items = day_text(now), []
    note = {}

    def source(name, fn):
        try:
            got = fn()
        except agg.Refused as e:
            report.append({'scene': sc['id'], 'source': name, 'ok': False, 'error': str(e)[:160]})
            return None
        if got is None:
            report.append({'scene': sc['id'], 'source': name, 'ok': False, 'error': 'no key'})
        return got

    if 'netease' in sc:
        charts = sc['netease']['charts']
        direct = str((env or {}).get('LISTEN_NETEASE_DIRECT') or '') == '1'
        if charts:
            cid = charts[day_of(now) % len(charts)]
            got = source('netease', lambda: netease_pool(cid, now, direct))
            if got:
                name, tracks = got
                pick = shuffled(tracks[:40], 'ne|%s|%s' % (sc['id'], day))[:sc['netease']['count']]
                pick.sort(key=lambda r: tracks.index(r))                                     # (in the chart's own order)
                items += pick
                note = {'sub': '今天放的是网易云音乐的“%s”' % name, 'sub_en': 'Today: NetEase Cloud Music’s “%s”' % name}
                report.append({'scene': sc['id'], 'source': 'netease', 'ok': True, 'count': len(pick), 'chart': name, 'mode': 'direct' if direct else 'their player'})
    if 'apple' in sc:
        regs = sc['apple']['regions']
        reg = regs[day_of(now) % len(regs)]
        got = source('apple', lambda: apple_pool(reg, now))
        if got:
            pick = shuffled(got, 'ap|%s|%s' % (sc['id'], day))[:sc['apple']['count']]
            pick.sort(key=lambda r: got.index(r))
            items += pick
            zh, en = APPLE_REGIONS[reg]
            note = {'sub': '今天放的是 Apple Music 的“%s”热歌榜（每首只有 30 秒试听，点歌名旁的 ↗ 去 Apple Music 听全曲）' % zh, 'sub_en': 'Today: Apple Music’s %s most-played chart (30-second previews; the ↗ goes to the full song on Apple Music)' % en}
            report.append({'scene': sc['id'], 'source': 'apple', 'ok': True, 'count': len(pick), 'chart': reg})
    if 'playlists' in sc:
        lists = shuffled(sc['playlists']['items'], 'pl|%s|%s' % (sc['id'], day))[:sc['playlists']['pick']]
        items += [{'id': 'pl:%s:%s' % (p['src'], p['id']), 'title': p['title'], 'title_en': p['title_en'], 'artist': LIST_FROM[p['src']], 'cover': '', 'dur': 0, 'mode': 'embed', 'kind': 'list', 'url': LIST_URL[p['src']] % p['id'],
                   'from': LIST_FROM[p['src']], **({'net': 'global'} if p['src'] == 'spotify' else {})} for p in lists]
        report.append({'scene': sc['id'], 'source': 'playlists', 'ok': True, 'count': len(lists)})
    if 'jamendo' in sc:
        got = source('jamendo', lambda: jamendo_pool(sc['jamendo'], env, now))
        if got:
            tracks, dropped = got
            pick = shuffled(tracks, 'jm|%s|%s' % (sc['id'], day))[:12]
            items += pick
            report.append({'scene': sc['id'], 'source': 'jamendo', 'ok': bool(tracks), 'count': len(pick), 'pool': len(tracks), 'left_out_for_licence': dropped})
    if 'youtube' in sc:
        got = source('youtube', lambda: youtube_pool(env, now))
        if got:
            pick = shuffled(got, 'yt|%s|%s' % (sc['id'], day))[:sc['youtube']['count']]
            items += pick
            report.append({'scene': sc['id'], 'source': 'youtube', 'ok': True, 'count': len(pick), 'pool': len(got)})
    return items, note


def build(env, now=None):
    """-> (the scenes of today as the page takes them, a report of the sources)."""
    now = time.time() if now is None else now
    off = {x.strip() for x in str((env or {}).get('LISTEN_DISABLE') or '').lower().split(',') if x.strip()}
    scenes, report = [], []
    for sc in scenes_config():
        if sc['id'] in off:
            continue
        items, note = _scene(sc, env, now, report)
        need = 1 if 'playlists' in sc or 'netease' in sc else 3                      # (a scene of three songs is not worth a tab)
        if len(items) >= need:
            scenes.append({'id': sc['id'], 'name': sc['name'], 'name_en': sc['name_en'], 'net': sc.get('net', 'cn'), **note, 'items': items})
    return scenes, report


# ---------------------------------------------------------------- the answer
def _fail(status, error, zh, en):
    return status, 'application/json; charset=utf-8', json.dumps({'ok': False, 'error': error, 'message': zh, 'message_en': en}, ensure_ascii=False).encode('utf-8'), 'no-store'


def handle(query, env, now=None):
    """-> (status, content type, body bytes, cache header). `query`: what parse_qs makes of the part after "?"."""
    now = time.time() if now is None else now
    if str((env or {}).get('LISTEN') or '') == '0':
        return _fail(404, 'off', '这个功能没有开启', 'Not turned on')
    scenes, report = build(env, now)
    if (query.get('diag') or [''])[0]:
        body = json.dumps({'ok': True, 'keys': {'JAMENDO_CLIENT_ID': bool(str((env or {}).get('JAMENDO_CLIENT_ID') or '').strip()), 'YOUTUBE_API_KEY': bool(str((env or {}).get('YOUTUBE_API_KEY') or '').strip())},
                           'scenes': [{'id': s['id'], 'items': len(s['items'])} for s in scenes], 'sources': report}, ensure_ascii=False).encode('utf-8')
        return 200, 'application/json; charset=utf-8', body, 'no-store'
    if not scenes:
        return _fail(502, 'empty', '今天还没有可听的内容', 'Nothing to listen to today yet')
    body = json.dumps({'ok': True, 'date': day_text(now), 'updated': int(now), 'scenes': scenes}, ensure_ascii=False).encode('utf-8')
    return 200, 'application/json; charset=utf-8', body, CDN


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


def probe(env, now=None, say=print):
    """The check to run on your own computer before (or after) deploying: `python -X utf8 listen.py` with the keys in the environment. Says which keys are set and valid (and why not), what each source gave,
    and, for one streamed track of each group, whether its sound can be fetched from here. Nothing secret is printed. -> True when every group that was made has something to play."""
    now = time.time() if now is None else now
    env = env or {}
    say('今日听什么 · 自检（%s，中国时间）' % day_text(now))
    for k in ('JAMENDO_CLIENT_ID', 'YOUTUBE_API_KEY', 'SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET'):
        say('  %-20s %s' % (k, '已设置' if str(env.get(k) or '').strip() else '没设置'))
    say('  %-20s %s' % ('LISTEN_NETEASE_DIRECT', '1：网易云的歌用站内播放条放整首' if str(env.get('LISTEN_NETEASE_DIRECT') or '') == '1' else '没开：网易云用它自己的播放器'))
    youtube_check(env, say)
    spotify_check(env, say)
    scenes, report = build(env, now)
    for r in report:
        extra = '，'.join('%s %s' % (k, r[k]) for k in ('count', 'pool', 'left_out_for_licence', 'chart', 'mode') if k in r)
        say('  [%s] %s：%s%s' % (r['scene'], r['source'], '读到了' if r['ok'] else '没读到（%s）' % r.get('error', ''), ('，' + extra) if extra else ''))
    good = True
    for s in scenes:
        say('组“%s”：%d 条，%s' % (s['name'], len(s['items']), '需要海外网络' if s['net'] == 'global' or any(i.get('net') == 'global' for i in s['items']) else '国内可用的来源'))
        stream = [i for i in s['items'] if i['mode'] == 'stream']
        if stream and stream[0]['audio'].startswith('https://music.163.com/song/media/outer/'):
            say('    网易云直连：服务已确认这首歌会给出声音地址；真能不能放，用 python -X utf8 listen.py --browser 在浏览器里试')
        elif stream:
            i = stream[0]
            try:
                _, h, body = agg.fetch(i['audio'], 'audio/*,*/*;q=0.5', 4096, headers={'Range': 'bytes=0-1023', **hotapi.BROWSER}, truncate=True)
                kind = h.get('content-type', '?')
                ok = kind.startswith('audio/') or 'octet-stream' in kind
                say('    试取第一首的声音（%s）：%s，%s，%d 字节' % (i['title'][:30], '可以' if ok else '不像声音', kind, len(body)))
                good = good and ok
            except agg.Refused as e:
                say('    试取第一首的声音（%s）：失败，%s' % (i['title'][:30], e))
                good = False
        else:
            say('    用平台自己的播放器播（在页面里点开试；平台不让别人播的歌，播放器里会是灰的）：' + s['items'][0]['url'])
    if not scenes:
        say('今天一组都没做出来：上面“没读到”的原因就是答案。')
    say('结论：' + ('这些组都能用。' if scenes and good else '有问题，见上面。'))
    return bool(scenes) and good


PROBE_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>今日听什么 · 在浏览器里试播</title>
<style>body{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#222}h2{margin:22px 0 6px;font-size:17px}ul{padding-left:20px}li{margin:3px 0}.ok{color:#0a7f3f}.bad{color:#c0392b}.wait{color:#888}code{background:#eee;padding:0 4px;border-radius:3px}iframe{border:0;display:block;margin:6px 0}</style></head><body>
<h1>今日听什么 · 在你的浏览器里试播</h1>
<p>每一组里“站内播放”的歌，都会在这个页面里用 <code>&lt;audio&gt;</code> 真放一下（和站内播放条是同一种方式，用的是你的网络和浏览器）。<b class="ok">✓</b> 是真的放出声音了，<b class="bad">✗</b> 后面写着原因。站点是 https 的：Apple 和 Jamendo 的地址本来就是 https，网易云的地址浏览器会自动升级。</p>
<div id="out">读取中……</div><p id="sum"></p>
<script>
const TEST=src=>new Promise(res=>{const a=new Audio();let done=false;const fin=r=>{if(done)return;done=true;try{a.pause();}catch(e){}res(r);};
  a.addEventListener('playing',()=>setTimeout(()=>fin({ok:true,dur:isFinite(a.duration)?a.duration:0}),1500));
  a.addEventListener('error',()=>fin({ok:false,msg:(a.error&&a.error.message)||'error '+(a.error&&a.error.code)}));
  a.src=src;const p=a.play();if(p&&p.catch)p.catch(e=>{if(!done)setTimeout(()=>fin({ok:false,msg:String(e).slice(0,80)}),500);});setTimeout(()=>fin({ok:false,msg:'15 秒内没有开始播放'}),15000);});
(async()=>{
  const d=await (await fetch('/probe.json')).json(), out=document.getElementById('out'); out.textContent='';
  let tried=0,good=0;
  if(!d.scenes.length) out.textContent='今天一组都没做出来（见终端里的原因）。';
  for(const s of d.scenes){
    const h=document.createElement('h2');h.textContent=s.name+'（'+s.items.length+' 条）';out.appendChild(h);
    const ul=document.createElement('ul');out.appendChild(ul);
    const stream=s.items.filter(i=>i.mode==='stream').slice(0,3),embed=s.items.filter(i=>i.mode!=='stream');
    for(const i of stream){
      const li=document.createElement('li');li.className='wait';li.textContent='试播：'+i.title+' — '+i.artist+' ……';ul.appendChild(li);tried++;
      const r=await TEST(i.audio);li.className=r.ok?'ok':'bad';
      li.textContent=(r.ok?'✓ ':'✗ ')+i.title+' — '+i.artist+(r.ok?'（放出声音了'+(r.dur?'，全长 '+Math.round(r.dur)+' 秒':'')+'）':'（放不出来：'+r.msg+'）');if(r.ok)good++;
    }
    if(embed.length){const li=document.createElement('li');li.textContent='另有 '+embed.length+' 条要用平台自己的播放器播（这里只测一个视频）。';ul.appendChild(li);}
    const yt=embed.find(i=>i.from==='YouTube');
    if(yt){const f=document.createElement('iframe');f.width=320;f.height=180;f.allow='encrypted-media';f.src='https://www.youtube-nocookie.com/embed/'+yt.id.slice(3);out.appendChild(f);
      const p=document.createElement('p');p.textContent='上面是 YouTube 的嵌入播放器：能看到画面、能点播放，就说明你的网络能用它。';out.appendChild(p);}
  }
  document.getElementById('sum').textContent='结果：'+good+' / '+tried+' 首放出了声音。';
})();
</script></body></html>"""


def serve_probe(env, port=8899):
    """`python -X utf8 listen.py --browser`: a page on this computer that really plays the first tracks of each group in a browser (your own network, your own browser)."""
    import http.server
    import webbrowser
    scenes, report = build(env)
    payload = json.dumps({'scenes': scenes, 'report': report}, ensure_ascii=False).encode('utf-8')
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
