"""Sources of music beyond the charts: Bodian (search and whole songs), the Douyin hot-songs chart, a search over every source, the "find the whole song" resolver and the pool of songs a recommender
may draw from. listen.py asks for these from `handle()`; nothing here is stored (a warm instance keeps what a source said for a short while, and how each source has been answering).

  GET /api/listen?search=<q>             the songs that match, the same song of several sources made one, each with the sounds (cands) of the sources
  GET /api/listen?resolve=1&t=&a=&d=&id= fresh sounds for one song (a whole one where there is one) and its lyrics when a source has them
  GET /api/listen?pool=cn|global         songs of the charts, for a recommender in the page (no personal data goes in or out)

Bodian is the "小尘" API (api.xcvts.cn), a personal free interface with no promise of service: every call is short, three failures in a row stop asking for ten minutes, and there is always another source.
It is on unless LISTEN_BODIAN=0. The sound it gives is a time-limited address of a lossless file: it is looked at (HEAD) before it is given, rewritten to https, and never kept longer than `ttl`.
Nothing is proxied: the page's player loads the sound from the source itself.
"""
import concurrent.futures
import hashlib
import http.client
import json
import re
import time
import urllib.parse

import aggregate as agg
import hotapi

BODIAN = 'https://api.xcvts.cn/api/music/bdyy?msg=%s&type=json'
DYCHART = 'https://api.xcvts.cn/api/hotlist/dy/music'
DEEZER_SEARCH = 'https://api.deezer.com/search?q=%s&limit=15'
ITUNES_SEARCH = 'https://itunes.apple.com/search?term=%s&media=music&entity=song&limit=15&country=%s'
AUDIUS_SEARCH = 'https://api.audius.co/v1/tracks/search?query=%s&limit=15&app_name=zhitree-books'
NETEASE_SEARCH = 'https://music.163.com/api/search/get/web?csrf_token=&s=%s&type=1&offset=0&total=true&limit=10'
JAMENDO_SEARCH = 'https://api.jamendo.com/v3.0/tracks/?client_id=%s&format=json&limit=15&include=licenses&imagesize=300&namesearch=%s'

SEARCH_SECONDS = 3.0                        # a search waits this long for its sources: what is there by then is the answer (partial)
RESOLVE_SECONDS = 8.0                       # a resolve waits this long (a Vercel function has 10)
BODIAN_SECONDS = 8
BREAK_AFTER = 3                             # failures in a row ...
BREAK_FOR = 600                             # ... and the source is left alone for this many seconds
DY_SECONDS = 1800                           # the Douyin chart is kept this long per warm instance
CACHE_SEARCH = 'public, max-age=60, s-maxage=600, stale-while-revalidate=3600'
CACHE_RESOLVE = 'public, max-age=30, s-maxage=60'
CACHE_POOL = 'public, max-age=600, s-maxage=3600, stale-while-revalidate=86400'
KUWO = re.compile(r'https://[a-z0-9-]+(\.[a-z0-9-]+)*\.kuwo\.cn/')
BAD_WORDS = ('live', 'remix', 'cover', '伴奏', '翻唱', '现场', 'karaoke', 'instrumental', 'dj版', 'dj ', 'bootleg', 'mashup', 'nightcore', 'slowed', 'sped up')
PROVIDER_NAMES = {'bodian': '波点音乐', 'netease': '网易云音乐', 'audius': 'Audius', 'jamendo': 'Jamendo', 'apple': 'Apple Music', 'deezer': 'Deezer', 'dychart': '抖音热歌榜'}
NET = {'bodian': 'cn', 'netease': 'cn', 'audius': 'any', 'jamendo': 'any', 'apple': 'any', 'deezer': 'any'}
HEALTH = {}                                 # provider -> how it has been answering (per warm instance: a hint, not a record)
_DY = {}


# ---------------------------------------------------------------- how each source has been answering
def _h(pid):
    return HEALTH.setdefault(pid, {'ok': 0, 'fail': 0, 'ms': 0.0, 'streak': 0, 'until': 0.0, 'err': '', 'last_ok': 0})


def paused(pid, now=None):
    return _h(pid)['until'] > (time.time() if now is None else now)


def guarded(pid, fn):
    """Call `fn()` for a source: counted, timed, and not even tried while the source is on its ten minutes of rest. Raises agg.Refused when it fails or rests."""
    h = _h(pid)
    now = time.time()
    if h['until'] > now:
        raise agg.Refused('%s 暂时不问（连续出错）' % PROVIDER_NAMES.get(pid, pid), '%s is resting after failures' % PROVIDER_NAMES.get(pid, pid))
    t0 = time.time()
    try:
        val = fn()
    except agg.Refused as e:
        h['fail'] += 1
        h['streak'] += 1
        h['err'] = str(e)[:120]
        if h['streak'] >= BREAK_AFTER:
            h['until'] = time.time() + BREAK_FOR
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        h['fail'] += 1
        h['streak'] += 1
        h['err'] = 'format'
        if h['streak'] >= BREAK_AFTER:
            h['until'] = time.time() + BREAK_FOR
        raise agg.Refused('%s 的格式变了' % PROVIDER_NAMES.get(pid, pid), 'The format of %s has changed' % PROVIDER_NAMES.get(pid, pid))
    ms = (time.time() - t0) * 1000
    h['ok'] += 1
    h['streak'] = 0
    h['ms'] = ms if not h['ms'] else h['ms'] * 0.7 + ms * 0.3
    h['last_ok'] = int(time.time())
    return val


def providers_report(env):
    out = []
    for pid in ('bodian', 'dychart', 'netease', 'audius', 'jamendo', 'apple', 'deezer'):
        h = _h(pid)
        on = enabled(pid, env)
        row = {'id': pid, 'on': on, 'ok': h['ok'], 'fail': h['fail'], 'ms': int(h['ms'])}
        if h['err']:
            row['err'] = h['err']
        if h['last_ok']:
            row['last_ok_at'] = h['last_ok']
        if h['until'] > time.time():
            row['resting_for'] = int(h['until'] - time.time())
        out.append(row)
    return out


def enabled(pid, env):
    env = env or {}
    if pid in ('bodian', 'dychart'):
        return str(env.get('LISTEN_BODIAN') or '') != '0'
    if pid == 'netease':
        return str(env.get('LISTEN_NETEASE_DIRECT') or '') == '1'
    if pid == 'jamendo':
        return bool(str(env.get('JAMENDO_CLIENT_ID') or '').strip())
    return True


# ---------------------------------------------------------------- small things
def _L():
    import listen                           # (listen asks for this module from inside its functions: by then both are loaded)
    return listen


def norm(s):
    return re.sub(r'[\W_]+', '', str(s or '').lower())


CJK = re.compile(r'[㐀-鿿]')


def artists_of(s):
    return [n for n in (norm(x) for x in re.split(r'\s*(?:/|&|、|,|，|;|；|\bfeat\.?\b|\bft\.?\b|\band\b| x |×)\s*', str(s or ''), flags=re.I)) if n]


def close_names(p, q):
    """The same artist written two ways: equal, one inside the other ("Jay 周杰伦" and "周杰伦"), or the same name in traditional and simplified characters (周杰倫 / 周杰伦: most characters are the same)."""
    if p == q:
        return True
    if min(len(p), len(q)) > 2 and (p in q or q in p):
        return True
    if CJK.search(p) and CJK.search(q):
        a, b = set(p), set(q)
        return len(a & b) / max(len(a), len(b)) >= 0.6 and min(len(a), len(b)) >= 2
    return False


def same_artist(a, b):
    x, y = artists_of(a), artists_of(b)
    if not x or not y:
        return True                         # (one of them does not say: the title has to do)
    return any(close_names(p, q) for p in x for q in y)


def clean_title(s, artist=''):
    """The name of a song without the things in brackets, and without what a dash adds: "Song (Live at ..)" -> "Song", "Song - Remastered" -> "Song", "周杰伦-七里香" with the artist 周杰伦 -> "七里香"."""
    t = re.sub(r'\s*[\(\[（【].*?[\)\]）】]\s*', ' ', str(s or '')).strip()
    parts = [x.strip() for x in re.split(r'\s+[-–—]\s+|(?<=[㐀-鿿])\s*-\s*(?=[㐀-鿿])', t) if x.strip()]
    if len(parts) > 1:
        t = ' - '.join(parts[1:]) if artist and same_artist(parts[0], artist) else parts[0]
    return t.strip()


def same_song(t1, a1, d1, t2, a2, d2):
    n1, n2 = norm(clean_title(t1, a1)), norm(clean_title(t2, a2))
    if not n1 or not n2 or not (n1 == n2 or (min(len(n1), len(n2)) >= 4 and (n1 in n2 or n2 in n1))):
        return False
    if not same_artist(a1, a2):
        return False
    if d1 and d2 and abs(int(d1) - int(d2)) > 3 and min(d1, d2) > 45:         # (a preview of 30 seconds says nothing about the length)
        return False
    return True


def _has(word, text):
    if CJK.search(word) or ' ' in word:
        return word in text
    return re.search(r'(?<![a-z0-9])' + re.escape(word) + r'(?![a-z0-9])', text) is not None            # ("live" is not in "Alive")


def wanted(title_found, query):
    """A live version, a remix, a cover, an instrumental is left out unless the query asks for it."""
    q, t = str(query or '').lower(), str(title_found or '').lower()
    return not any(_has(w.strip(), t) and not _has(w.strip(), q) for w in BAD_WORDS)


def cand(p, k, audio='', url='', **extra):
    """One sound of one source for a song. `k`: full | preview | embed | link. A candidate with `resolve: true` has no address yet (a resolve gives it)."""
    out = {'p': p, 'k': k, 'net': NET.get(p, 'any')}
    if audio:
        out['audio'] = audio
    if url:
        out['url'] = url
    out.update({k2: v for k2, v in extra.items() if v not in (None, '', False) or v is True})
    return out


def sort_cands(cs, region='cn'):
    """Whole songs before previews; inside that the sources of the reader's side first."""
    def rank(c):
        full = 0 if c.get('k') == 'full' else 1 if c.get('k') == 'preview' else 2
        side = 0 if c.get('net') in (region, 'any') else 1
        stub = 1 if c.get('resolve') else 0
        return (full, stub, side)
    return sorted(cs, key=rank)


# ---------------------------------------------------------------- Bodian (the "小尘" API: search, the whole song as a time-limited address, the lyrics)
def bodian_rows(raw):
    """-> the matches of a search: [{n, title, artist, cover}] (n is the number to ask the detail with, from 1)."""
    d = json.loads(raw)
    rows = d.get('data') if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise agg.Refused('波点的格式变了', 'The format of Bodian has changed')
    out = []
    for i, r in enumerate(rows):
        if isinstance(r, dict) and r.get('name'):
            out.append({'n': i + 1, 'title': _L()._t(r.get('name'), 120), 'artist': _L()._t(r.get('artist') or r.get('_singer'), 80), 'cover': _L()._https(r.get('pic'))})
    return out


def bodian_detail(raw):
    """-> {title, artist, cover, audio, url, lrc} of one song; audio is an https address of a sound (or '' when there is none)."""
    d = json.loads(raw)
    r = d.get('data') if isinstance(d, dict) else None
    if not isinstance(r, dict):
        raise agg.Refused('波点没有这首歌的详情', 'Bodian has no details of this song')
    audio = _L()._https(r.get('play_url'))
    return {'title': _L()._t(r.get('name'), 120), 'artist': _L()._t(r.get('artist'), 80), 'cover': _L()._https(r.get('cover')), 'audio': audio if KUWO.match(audio) else '',
            'url': _L()._https(r.get('detail_page')), 'lrc': str(r.get('lrc') or '')[:30000]}


def head_ok(url, timeout=3):
    """Does the address answer (HEAD)? -> (True/False, size in bytes or 0). Only the hosts of the source are asked."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or not KUWO.match(url):
        return False, 0
    conn = http.client.HTTPSConnection(u.hostname, timeout=timeout)
    try:
        conn.request('HEAD', (u.path or '/') + (('?' + u.query) if u.query else ''), headers={'User-Agent': hotapi.BROWSER['User-Agent']})
        r = conn.getresponse()
        size = int(r.getheader('Content-Length') or 0) if r.status == 200 else 0
        return r.status == 200, size
    except (OSError, http.client.HTTPException, ValueError):
        return False, 0
    finally:
        conn.close()


def bodian_search(q):
    return guarded('bodian', lambda: bodian_rows(_L()._get(BODIAN % urllib.parse.quote(q, safe=''))))


def bodian_resolve(title, artist, query=None):
    """-> (candidate, lyrics text) of the best match for a song, or (None, '') when Bodian has nothing for it."""
    q = ('%s %s' % (clean_title(title, artist), artist or '')).strip()
    rows = bodian_search(q)
    pick = next((r for r in rows if same_song(title, artist, 0, r['title'], r['artist'], 0) and wanted(r['title'], query or title)), None)
    if pick is None:
        return None, ''
    det = guarded('bodian', lambda: bodian_detail(_L()._get(BODIAN % urllib.parse.quote(q, safe='') + '&n=%d' % pick['n'])))
    if not det['audio']:
        return None, det['lrc']
    good, size = head_ok(det['audio'])
    if not good:
        return None, det['lrc']
    return cand('bodian', 'full', det['audio'], det['url'], big=True, size=size or None, lyr=True if det['lrc'] else None, lic='波点音乐'), det['lrc']


# ---------------------------------------------------------------- the Douyin hot-songs chart (names and ranks only: the sounds are found when a song is played)
def dychart_rows(raw):
    d = json.loads(raw)
    rows = d.get('data') if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise agg.Refused('抖音热歌榜的格式变了', 'The format of the Douyin chart has changed')
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not r.get('title'):
            continue
        sid = 'dy:%s' % (hashlib.sha1(('%s|%s' % (norm(r.get('title')), norm(r.get('artist')))).encode('utf-8')).hexdigest()[:10])
        out.append({'id': sid, 'title': _L()._t(r['title'], 120), 'artist': _L()._t(r.get('artist'), 80), 'cover': _L()._https(r.get('pic')), 'dur': 0, 'mode': 'stream', 'audio': '', 'url': '', 'lic': '',
                    'from': '抖音热歌榜', 'hot': i + 1, 'resolve': True, 'cands': []})
    if not out:
        raise agg.Refused('抖音热歌榜现在是空的', 'The Douyin chart is empty right now')
    return out


def dychart_pool(env, now):
    """The songs of the chart (None when Bodian's site is turned off): kept DY_SECONDS, an older copy up to a day when the site does not answer."""
    if not enabled('dychart', env):
        return None
    hit = _DY.get('rows')
    if hit and now - hit[0] < DY_SECONDS:
        return hit[1]
    try:
        rows = guarded('dychart', lambda: dychart_rows(_L()._get(DYCHART)))
    except agg.Refused:
        if hit and now - hit[0] < 24 * 3600:
            return hit[1]
        raise
    _DY['rows'] = (now, rows)
    return rows


# ---------------------------------------------------------------- the search of the other sources, each one into the same shape: [{title, artist, dur, cover, cand}]
def _row(title, artist, dur, cover, c):
    return {'title': _L()._t(title, 120), 'artist': _L()._t(artist, 80), 'dur': dur, 'cover': cover, 'cand': c}


def tracks_or_none(parse, raw):
    """The tracks of a source's answer; [] when it answered and has nothing for the words (that is an answer, not a failure: it must not count against the source)."""
    try:
        return parse(raw)
    except agg.Refused as e:
        if e.en == 'The chart is empty right now':
            return []
        raise


def deezer_search_rows(raw):
    out = []
    for t in tracks_or_none(_L().deezer_tracks, raw):
        out.append(_row(t['title'], t['artist'], t['dur'], t['cover'], cand('deezer', 'preview', t['audio'], t['url'], lic=t['lic'])))
    return out


def itunes_search_rows(raw):
    d = json.loads(raw)
    out = []
    for r in (d.get('results') or []) if isinstance(d, dict) else []:
        if not isinstance(r, dict) or r.get('kind') != 'song' or not r.get('trackName'):
            continue
        sound, page = _L()._https(r.get('previewUrl')), _L()._https(r.get('trackViewUrl'))
        if not re.match(r'https://audio-ssl\.itunes\.apple\.com/', sound):
            continue
        art = _L()._https(str(r.get('artworkUrl100') or '').replace('100x100', '300x300'))
        out.append(_row(r['trackName'], r.get('artistName'), _L()._secs(r.get('trackTimeMillis'), 1000), art,
                        cand('apple', 'preview', sound, page if re.match(r'https://(music|itunes)\.apple\.com/', page) else '', lic='Apple Music 30 秒试听')))
    return out


def audius_search_rows(raw):
    out = []
    for t in tracks_or_none(_L().audius_tracks, raw):
        out.append(_row(t['title'], t['artist'], t['dur'], t['cover'], cand('audius', 'full', t['audio'], t['url'], lic='Audius')))
    return out


def jamendo_search_rows(raw):
    tracks, _ = _L().jamendo_tracks(raw)
    return [_row(t['title'], t['artist'], t['dur'], t['cover'], cand('jamendo', 'full', t['audio'], t['url'], lic=t['lic'])) for t in tracks]


def netease_search_rows(raw):
    d = json.loads(raw)
    res = d.get('result') if isinstance(d, dict) else None
    out = []
    for r in ((res or {}).get('songs') or []) if isinstance(res, dict) else []:
        if isinstance(r, dict) and r.get('name') and re.fullmatch(r'\d{1,12}', str(r.get('id') or '')):
            who = ' / '.join(_L()._t(a.get('name'), 40) for a in r.get('artists') or [] if isinstance(a, dict) and a.get('name'))
            alb = r.get('album') if isinstance(r.get('album'), dict) else {}
            out.append(_row(r['name'], who, _L()._secs(r.get('duration'), 1000), _L()._https(alb.get('picUrl')),
                            cand('netease', 'full', _L().NE_OUTER % r['id'], 'https://music.163.com/#/song?id=%s' % r['id'], lic='网易云音乐', ne_id=r['id'])))
    return out


def provider_rows(pid, q, env, lang):
    """-> the rows one source finds for `q` (raises agg.Refused). Bodian's rows have no address: their candidate says `resolve`."""
    quoted = urllib.parse.quote(q, safe='')
    L = _L()
    if pid == 'deezer':
        return guarded(pid, lambda: deezer_search_rows(L._get(DEEZER_SEARCH % quoted)))
    if pid == 'apple':                      # (the store of China answers no search at all; Taiwan's has the Chinese songs, in traditional characters)
        return guarded(pid, lambda: itunes_search_rows(L._get(ITUNES_SEARCH % (quoted, 'tw' if lang == 'zh' else 'us'))))
    if pid == 'audius':
        return guarded(pid, lambda: audius_search_rows(L._get(AUDIUS_SEARCH % quoted)))
    if pid == 'jamendo':
        key = urllib.parse.quote(str((env or {}).get('JAMENDO_CLIENT_ID') or '').strip(), safe='')
        return guarded(pid, lambda: jamendo_search_rows(L._get(JAMENDO_SEARCH % (key, quoted))))
    if pid == 'netease':
        return guarded(pid, lambda: netease_search_rows(L._get(NETEASE_SEARCH % quoted, {'Referer': 'https://music.163.com/'})))
    if pid == 'bodian':
        return [_row(r['title'], r['artist'], 0, r['cover'], cand('bodian', 'full', big=True, lyr=True, resolve=True, lic='波点音乐')) for r in bodian_search(q)]
    return []


def search_providers(env):
    return [p for p in ('deezer', 'apple', 'audius', 'jamendo', 'bodian') if enabled(p, env) and not paused(p)]


# ---------------------------------------------------------------- merging: the same song of several sources is one song with several sounds
def merge(rows_by_provider, query, region='cn'):
    """-> the songs, the best match first: one per song, with every source's sound in `cands` (whole before preview)."""
    songs = []
    for pid, rows in rows_by_provider:
        for r in rows:
            if not wanted(r['title'], query):
                continue
            for s in songs:
                if same_song(s['title'], s['artist'], s['dur'], r['title'], r['artist'], r['dur']):
                    if all(c['p'] != r['cand']['p'] for c in s['cands']):                    # (one sound of each source is enough: the first it found)
                        s['cands'].append(r['cand'])
                    s['hay'].append('%s %s' % (r['title'], r['artist']))
                    s['cover'] = s['cover'] or r['cover']
                    if r['dur'] > 45 and s['dur'] <= 45:                      # (the length of a whole song, not of a preview)
                        s['dur'] = r['dur']
                    break
            else:
                songs.append({'title': r['title'], 'artist': r['artist'], 'dur': r['dur'], 'cover': r['cover'], 'cands': [r['cand']], 'p0': pid, 'hay': ['%s %s' % (r['title'], r['artist'])]})
    out = []
    for s in songs:
        cs = sort_cands(s['cands'], region)
        first = next((c for c in cs if c.get('audio')), None)
        sid = 'sr:' + hashlib.sha1(('%s|%s' % (norm(clean_title(s['title'], s['artist'])), (artists_of(s['artist']) or [''])[0])).encode('utf-8')).hexdigest()[:10]
        item = {'id': sid, 'title': s['title'], 'artist': s['artist'], 'cover': s['cover'], 'dur': s['dur'], 'mode': 'stream', 'audio': first['audio'] if first else '', 'url': next((c['url'] for c in cs if c.get('url')), ''),
                'lic': (first or {}).get('lic', ''), 'from': PROVIDER_NAMES.get(first['p'] if first else s['p0'], ''), 'cands': cs}
        if first and first['k'] == 'preview':
            item['preview'] = True
        if not first:
            item['resolve'] = True
        # the words of the query in the title or the artist (in any source's spelling) first, a title that IS one of the words before a title that has it inside; then the songs that several sources know
        # (the surer matches), a whole song before a preview
        rel = relevance(s['hay'], query)
        words = query_words(query)
        item['_rank'] = (-rel, 0 if norm(clean_title(s['title'], s['artist'])) in words else 1, -len(cs), 0 if any(c['k'] == 'full' for c in cs) else 1)
        item['_rel'] = rel
        out.append(item)
    if any(x['_rel'] > 0 for x in out):                                                          # (what has none of the words is what a source found by chance)
        out = [x for x in out if x['_rel'] > 0]
    out.sort(key=lambda x: x['_rank'])
    for x in out:
        del x['_rank'], x['_rel']
    return out[:30]


def query_words(query):
    return [w for w in (norm(x) for x in re.split(r'\s+', str(query or ''))) if w]


def relevance(hays, query):
    """How many of the words of the query are in the titles and artists a song is known by (0 to 1)."""
    words = query_words(query)
    if not words:
        return 1.0
    hay = norm(' '.join(hays))
    return round(sum(1 for w in words if w in hay) / len(words), 3)


def search(q, env, lang='zh', region='cn', deadline=None):
    """-> (songs, partial): `q` searched in every source at the same time; what has come in by the deadline is the answer."""
    deadline = SEARCH_SECONDS if deadline is None else deadline
    pids = search_providers(env)
    ex = concurrent.futures.ThreadPoolExecutor(max(1, len(pids)))
    futs = [(p, ex.submit(provider_rows, p, q, env, lang)) for p in pids]
    concurrent.futures.wait([f for _, f in futs], timeout=deadline)
    ex.shutdown(wait=False, cancel_futures=True)
    got, partial = [], False
    for p, f in futs:
        if f.done() and f.exception() is None:
            got.append((p, f.result()))
        else:
            partial = True
    return merge(got, q, region), partial


# ---------------------------------------------------------------- resolve: fresh sounds for one song
def resolve(title, artist, dur, env, lang='zh', region='cn', song_id='', deadline=None):
    """-> (candidates, lyrics {fmt, text, from} or None) of one song. Every source that has a sound for it is asked at the same time (Bodian's is the whole lossless file; NetEase when the owner turned it on;
    Audius and Jamendo whole songs; Apple and Deezer a preview at least). Sources that fail or are slow are left out."""
    deadline = RESOLVE_SECONDS if deadline is None else deadline
    q = ('%s %s' % (clean_title(title, artist), artist or '')).strip()
    ex = concurrent.futures.ThreadPoolExecutor(6)
    futs = {}
    if enabled('bodian', env) and not paused('bodian'):
        futs['bodian'] = ex.submit(bodian_resolve, title, artist, title)
    for p in ('netease', 'audius', 'jamendo', 'apple', 'deezer'):
        if enabled(p, env) and not paused(p):
            futs[p] = ex.submit(provider_rows, p, q, env, lang)
    concurrent.futures.wait(list(futs.values()), timeout=deadline)
    ex.shutdown(wait=False, cancel_futures=True)
    cands, lyrics = [], None
    for p, f in futs.items():
        if not f.done() or f.exception() is not None:
            continue
        r = f.result()
        if p == 'bodian':
            c, lrc = r
            if c:
                cands.append(c)
            if lrc:
                lyrics = {'fmt': 'lrc' if re.search(r'\[\d{1,2}:\d{2}', lrc) else 'text', 'text': lrc, 'from': PROVIDER_NAMES['bodian']}
            continue
        for row in r:
            if wanted(row['title'], title) and same_song(title, artist, dur, row['title'], row['artist'], row['dur']):
                c = row['cand']
                if p == 'netease':
                    if not _L().netease_outer(c.pop('ne_id', '')):
                        continue
                cands.append(c)
                break
    return sort_cands(cands, region), lyrics


# ---------------------------------------------------------------- the pool of songs a recommender draws from
POOL_GROUPS = {'cn': (('hot-cn', 'cn'), ('kr', 'jp-kr'), ('jp', 'jp-kr'), ('pop', 'cn')), 'global': (('hot-west', 'west'), ('dzp-3155776842', 'global'), ('dzp-1313621735', 'west'), ('rock', 'west'), ('hiphop', 'west'), ('electronic', 'west'))}


def pool(side, env, now, lang='zh', deadline=7.0):
    """-> the songs of the charts of one side ('cn' or 'global') with their place on the chart, their market and their style: nothing about a reader goes in."""
    L = _L()
    gs = {g['id']: g for g in L.groups(env, now, dynamic=False, lang=lang)}
    want = [(gid, market) for gid, market in POOL_GROUPS.get(side, ()) if gid in gs]
    ex = concurrent.futures.ThreadPoolExecutor(max(1, len(want)))
    futs = [(gid, market, ex.submit(L.fetch_group, gs[gid], env, now, None, 6.0, lang)) for gid, market in want]
    concurrent.futures.wait([f for _, _, f in futs], timeout=deadline)
    ex.shutdown(wait=False, cancel_futures=True)
    out, seen = [], set()
    for gid, market, f in futs:
        if not f.done() or f.exception() is not None:
            continue
        tag = gs[gid]['id'] if gs[gid]['kind'] == 'style' else ''
        for i, x in enumerate(f.result()[0][:60]):
            key = (norm(x['title']), norm(x['artist']))
            if key in seen:
                continue
            seen.add(key)
            out.append({'id': x['id'], 'title': x['title'], 'artist': x['artist'], 'cover': x['cover'], 'dur': x['dur'], 'rank': i + 1, 'market': market, 'tags': [tag] if tag else [], 'from': x['from']})
    return out


# ---------------------------------------------------------------- the lists for a page that asks for `caps=resolve`
REGION_OF = {'hot-cn': 'cn', 'hot-west': 'west', 'kr': 'jp-kr', 'jp': 'jp-kr', 'acg': 'jp-kr', 'dzp-3155776842': 'global', 'dzp-1313621735': 'west', 'dzp-1111142221': 'west', 'dy-hot': 'cn',
             'pod-cn': 'cn', 'pod-tw': 'cn', 'pod-hk': 'cn', 'pod-us': 'west', 'pod-gb': 'west', 'pod-jp': 'jp-kr', 'nep-745956260': 'jp-kr', 'nep-71385702': 'jp-kr'}
REGION_RANK = {'cn': {'cn': 0, 'jp-kr': 1, 'global': 2, 'west': 3}, 'global': {'west': 0, 'global': 1, 'jp-kr': 2, 'cn': 3}}
SCENES = ('chill', 'focus', 'sleep')                                  # (these were styles; for a page that can ask for more they are scenes)
SCENE_GROUPS = (('workout', '运动', 'Workout', [('deezer', 113), ('deezer', 116), ('audius', 'Electronic'), ('audius', 'Trap'), ('jamendo', 'workout'), ('apple_genre', 'us', 17)]),
                ('commute', '通勤', 'Commute', [('deezer', 132), ('deezer', 85), ('audius', 'Pop'), ('jamendo', 'commute'), ('apple_genre', 'us', 14)]),
                ('study', '学习', 'Study', [('jamendo', 'study'), ('audius', 'Lo-Fi'), ('audius', 'Classical'), ('deezer', 98), ('apple_genre', 'us', 5)]))


def v2_static():
    """The fixed lists as a page that can ask for a whole song sees them: chill, focus and sleep as scenes (with three more), and the Douyin chart. Copies: the lists of listen.STATIC are never changed."""
    L = _L()
    out = []
    for g in L.STATIC:
        out.append(dict(g, kind='scene') if g['id'] in SCENES else g)
    out += [L._g(gid, zh, en, 'scene', srcs) for gid, zh, en, srcs in SCENE_GROUPS]
    out.append(dict(L._g('dy-hot', '抖音热歌榜', 'Douyin hot songs', 'chart', [('dychart',)], 'first', 100, False, 3, lang='zh'), resolve=True))
    return out


def region_of(g):
    if g.get('region'):
        return g['region']
    gid = g['id']
    if gid in REGION_OF:
        return REGION_OF[gid]
    if gid.startswith('nep-'):
        return {'zh': 'cn', 'en': 'west'}.get(g.get('lang'), 'global')
    return 'global' if gid != 'daily' else ''


def side_of(region, lang):
    r = str(region or '').strip().lower()
    return r if r in ('cn', 'global') else ('cn' if lang == 'zh' else 'global')


def arrange(gs, side, lang):
    """-> the lists, each with its part of the world (`region`), the lists of the reader's side first inside their kind."""
    ranks = REGION_RANK[side]
    out = [dict(g, region=region_of(g)) for g in gs]
    return sorted(out, key=lambda g: (_L().KIND_ORDER[g['kind']], ranks.get(g['region'], 2), 0 if g.get('lang') in (lang, '*') else 1 if not g.get('lang') else 2))


def public_extra(g):
    out = {}
    if g.get('region'):
        out['region'] = g['region']
    cover = _L().COVERS.get(g['id'])
    if cover:
        out['cover'] = cover
    if g.get('resolve'):
        out['resolve'] = True
    return out


SOUND_OF = {'Deezer': ('deezer', 'preview'), 'Apple Music': ('apple', 'preview'), 'Audius': ('audius', 'full'), 'Jamendo': ('jamendo', 'full'), '网易云音乐': ('netease', 'full'), 'Apple Podcasts': ('podcast', 'full')}


def sound_of(x):
    """The sound a song of a list has by itself, as a candidate (None for a song with no address: the Douyin chart, a video)."""
    p = SOUND_OF.get(x.get('from'))
    if not p or not x.get('audio'):
        return None
    c = cand(p[0], p[1], x['audio'], x.get('url') or '', lic=x.get('lic') or '')
    c['net'] = 'cn' if p[0] == 'netease' else 'any'
    return c


def with_sounds(items, lists, g):
    """Each song of a list with the sounds every source gave for it (the same song on several charts of one list is one row with several sounds), its place on the list and the market of the list."""
    by = {}
    for l in lists:
        for x in l:
            c = sound_of(x)
            if c:
                by.setdefault((norm(x['title']), norm(x['artist'])), []).append(c)
    market = region_of(g)
    out = []
    for i, x in enumerate(items):
        y = dict(x)
        cs, seen = [], set()
        for c in by.get((norm(x['title']), norm(x['artist'])), []):
            if c['p'] not in seen:
                seen.add(c['p'])
                cs.append(c)
        y['cands'] = sort_cands(cs, 'cn')
        if g['kind'] in ('chart', 'list') and 'hot' not in y:
            y['hot'] = i + 1
        if market and market != 'global':
            y['market'] = market
        out.append(y)
    return out


# ---------------------------------------------------------------- the answers
def _clean_q(s, n):
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f<>]', ' ', str(s or ''))).strip()[:n]


def handle_search(q, env, lang, side):
    L = _L()
    q = _clean_q(q, 60)
    if not q:
        return L._fail(400, 'query', '没有要搜的词', 'Nothing to search for')
    items, partial = search(q, env, lang, side)
    cache = CACHE_SEARCH if items and not partial else 'public, max-age=10, s-maxage=60'
    return L._ok({'q': q, 'items': items, **({'partial': True} if partial else {})}, cache)


def handle_resolve(query, env, lang, side):
    L = _L()
    title, artist = _clean_q((query.get('t') or [''])[0], 120), _clean_q((query.get('a') or [''])[0], 80)
    if not title:
        return L._fail(400, 'title', '缺少歌名', 'The title is missing')
    dur = L._int((query.get('d') or [''])[0], 0, 0, 6 * 3600)
    sid = (query.get('id') or [''])[0]
    sid = sid if re.fullmatch(r'[\w:.-]{1,40}', sid) else ''
    cands, lyrics = resolve(title, artist, dur, env, lang, side, sid)
    ttl = 600 if any(c['p'] == 'bodian' for c in cands) else 3600
    body = {'id': sid, 'ttl': ttl if cands else 60, 'cands': cands}
    if lyrics:
        body['lyrics'] = lyrics
    return L._ok(body, CACHE_RESOLVE if cands else 'no-store')


def handle_pool(side, env, now, lang):
    L = _L()
    if side not in ('cn', 'global'):
        return L._fail(400, 'region', 'pool 只有 cn 和 global', 'pool is cn or global')
    stale = L.STATS['stale']
    items = pool(side, env, now, lang)
    if not items:
        return L._fail(502, 'empty', '现在读不到', 'Cannot be read right now')
    return L._ok({'updated': int(now), **({'stale': True} if L.STATS['stale'] > stale else {}), 'items': items}, CACHE_POOL)
