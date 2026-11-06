"""Story podcasts for the podcast side of "今日听什么": lists by style (mystery, true crime, true stories, history, funny talk, bedtime) and "hot" lists by platform (Ximalaya, Xiaoyuzhou, Lizhi FM).

Where it comes from, and why no private interface of any platform is needed:
  - registry/stories.json: shows picked by hand (the feed is read, the sound answers, it is being updated). That list is the floor.
  - Apple Podcasts' charts of China (true crime 1488, fiction 1483, society & culture 1324, history 1462): the "hot" signal. Apple's lookup gives each show's feed address, and the HOST of the feed says the platform
    (www.ximalaya.com, feed.xyzfm.space, rss.lizhi.fm ...). A show of a chart is taken into the lists only when its NAME says a story (夜话 / 怪谈 / 悬疑 / 睡前 / 野史 ...) and does not say a novel read aloud
    (小说 / 有声书 / 多播 / 穿越 ...: those are other people's books, and the lists here are for stories that people tell).
  - Each list is the NEWEST EPISODE of each show (as the charts of podcasts are), whole, from the show's own server; Apple's lookup gives the address of the sound, so no feed is read here.
Only pages that ask with `caps=resolve` get these lists (listen.py: music_sources.v2_static); an older page sees what it saw."""
import json
import os
import re
import urllib.parse

import aggregate as agg

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'registry', 'stories.json')
CHARTS = ('1488', '1483', '1324', '1462')                      # Apple Podcasts China: true crime, fiction, society & culture, history
CHART_TOP = 60                                                 # the first 60 of each chart
REGION = 'cn'
PLATFORMS = (('ximalaya.com', 'ximalaya'), ('xyzfm.space', 'xiaoyuzhou'), ('lizhi.fm', 'lizhi'), ('wavpub.com', 'wavpub'), ('storyfm.cn', 'wavpub'), ('heymeowfm.com', 'heymeow'))
PLATFORM_NAMES = {'ximalaya': ('喜马拉雅', 'Ximalaya'), 'xiaoyuzhou': ('小宇宙', 'Xiaoyuzhou'), 'lizhi': ('荔枝FM', 'Lizhi FM'), 'wavpub': ('WavPub', 'WavPub'), 'heymeow': ('HeyMeow', 'HeyMeow')}
HOT_PLATFORMS = ('ximalaya', 'xiaoyuzhou', 'lizhi')           # the platforms that have a "hot" list of their own
STORY_WORDS = re.compile(r'夜话|怪谈|怪奇|怪事|诡|灵异|鬼|惊悚|悬疑|悬案|罪案|案件|侦探|犯罪|刑侦|凶案|档案|睡前|助眠|晚安|哄睡|入眠|野史|历史|故事|真实|轶事|都市传说|奇闻')
NOVEL_WORDS = re.compile(r'小说|有声书|多播|有声剧|穿越|重生|爽文|逆袭|修仙|玄幻|总裁|言情|仙尊|快穿|系统|免费|【|》|《|儿童|童话|宝宝|幼儿|西游记|封神|盗墓|鬼吹灯|斗罗')
NOVEL_TITLE = re.compile(r'第\s*\d+\s*[章回]|^\s*\d{2,4}\s*第')                     # (an episode that is a chapter of a novel: the show reads a book aloud)
STYLE_WORDS = (('sleep', re.compile(r'睡前|助眠|晚安|哄睡|入眠')), ('crime', re.compile(r'罪案|案件|侦探|犯罪|刑侦|悬案|凶案')), ('mystery', re.compile(r'怪谈|怪奇|怪事|诡|灵异|鬼|惊悚|悬疑|夜话|都市传说|档案|奇闻')),
               ('fun', re.compile(r'段子|脱口秀|搞笑|爆笑|吐槽')), ('history', re.compile(r'历史|野史|轶事')), ('true', re.compile(r'故事|真实')))
_LOADED = {}


def platform_of(feed):
    host = (urllib.parse.urlsplit(str(feed or '')).hostname or '').lower()
    for dom, pid in PLATFORMS:
        if host == dom or host.endswith('.' + dom):
            return pid
    return ''


def style_of(name):
    for sid, rx in STYLE_WORDS:
        if rx.search(name):
            return sid
    return ''


def story_name(name):
    """A show of a chart is taken in when its name says a story and does not say a novel read aloud."""
    return bool(STORY_WORDS.search(name)) and not NOVEL_WORDS.search(name)


def load(path=None):
    """-> {'styles': [{id, name, name_en}], 'shows': [{id, name, name_en, platform, style, apple_id, feed, why, why_en}]} from registry/stories.json; an entry that is not valid is left out, a missing or broken file gives nothing."""
    path = path or PATH
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {'styles': [], 'shows': []}
    hit = _LOADED.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    cfg = agg.read_json(path, {})
    styles, shows, seen = [], [], set()
    for s in (cfg.get('styles') if isinstance(cfg, dict) else None) or []:
        if isinstance(s, dict) and re.fullmatch(r'[a-z]{2,12}', str(s.get('id') or '')) and agg.clean(s.get('name'), 20):
            styles.append({'id': s['id'], 'name': agg.clean(s['name'], 20), 'name_en': agg.clean(s.get('name_en'), 40) or agg.clean(s['name'], 20)})
    ok = {s['id'] for s in styles}
    for r in (cfg.get('shows') if isinstance(cfg, dict) else None) or []:
        if not isinstance(r, dict) or not re.fullmatch(r'[a-z0-9]{2,16}', str(r.get('id') or '')) or r['id'] in seen:
            continue
        if not agg.clean(r.get('name'), 60) or r.get('style') not in ok or not re.fullmatch(r'\d{4,14}', str(r.get('apple_id') or '')) or not re.match(r'https?://', str(r.get('feed') or '')):
            continue
        seen.add(r['id'])
        shows.append({'id': r['id'], 'name': agg.clean(r['name'], 60), 'name_en': agg.clean(r.get('name_en'), 80) or agg.clean(r['name'], 60), 'platform': platform_of(r['feed']) or agg.clean(r.get('platform'), 20), 'style': r['style'],
                      'apple_id': str(r['apple_id']), 'feed': r['feed'], 'why': agg.clean(r.get('why'), 80), 'why_en': agg.clean(r.get('why_en'), 160)})
    out = {'styles': styles, 'shows': shows}
    _LOADED[path] = (mt, out)
    return out


def available():
    return bool(load()['shows'])


def groups():
    """-> the lists of story podcasts (as listen._g makes them): one for each style that has a show picked, one for each of the hot platforms that has a show picked."""
    import listen
    cfg = load()
    out = []
    for s in cfg['styles']:
        if any(x['style'] == s['id'] for x in cfg['shows']):
            out.append(listen._g('st-' + s['id'], s['name'], s['name_en'], 'list', [('stories', 'style', s['id'])], 'mix', 60, False, 1, net='cn', typ='podcast', lang='zh'))
    for p in HOT_PLATFORMS:
        if any(x['platform'] == p for x in cfg['shows']):
            zh, en = PLATFORM_NAMES[p]
            out.append(listen._g('st-hot-' + p, zh + ' · 故事热门', en + ' · hot stories', 'list', [('stories', 'plat', p)], 'mix', 60, False, 1, net='cn', typ='podcast', lang='zh'))
    return out


def feeds_of(raws):
    """-> {collection id: feed address} out of the answers of iTunes lookup."""
    import listen
    out = {}
    for raw in raws:
        d = listen._json(raw)
        for r in (d.get('results') or []) if isinstance(d, dict) else []:
            if isinstance(r, dict) and r.get('feedUrl') and re.fullmatch(r'\d{4,14}', str(r.get('collectionId') or '')):
                out.setdefault(str(r['collectionId']), str(r['feedUrl']))
    return out


def entries(now):
    """-> the shows of the lists: [{item, plat, style, rank, order}] (item: the newest episode of the show, as the podcast charts have it); kept like what a chart said (listen.pooled)."""
    import concurrent.futures
    import listen

    def make():
        cfg = load()
        by_id = {x['apple_id']: (i, x) for i, x in enumerate(cfg['shows'])}
        ranks, chart_ids = {}, []

        def chart(g):
            try:
                return listen.apple_ids(listen._get(listen.POD_GENRE % (REGION, g)))[:CHART_TOP]
            except agg.Refused:
                return []
        with concurrent.futures.ThreadPoolExecutor(len(CHARTS)) as ex:
            charts = list(ex.map(chart, CHARTS))
        for ids in charts:
            for pos, cid in enumerate(ids):
                ranks[cid] = min(ranks.get(cid, 10 ** 6), pos)
                if cid not in chart_ids:
                    chart_ids.append(cid)
        ids = list(by_id) + [c for c in chart_ids if c not in by_id]
        if not ids:
            raise agg.Refused('故事播客的名单现在是空的', 'The list of story podcasts is empty right now')

        def one(batch):
            try:
                return listen._get(listen.POD_LOOKUP % (','.join(batch), REGION))
            except agg.Refused:
                return None
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            raws = [r for r in ex.map(one, [ids[i:i + 50] for i in range(0, len(ids), 50)]) if r]
        if not raws:
            raise agg.Refused('读不到故事播客的新一集', 'The newest episodes of the story podcasts cannot be read right now')
        feeds = feeds_of(raws)
        out = []
        for it in listen.podcast_items(ids, raws):
            cid = it['show']
            if cid in by_id:
                i, show = by_id[cid]
                plat, style, order = show['platform'], show['style'], i
            else:
                name = it['artist']
                style = style_of(name)
                if not story_name(name) or not style or NOVEL_TITLE.search(it['title']):
                    continue
                plat, order = platform_of(feeds.get(cid, '')), 10 ** 5
            out.append({'item': {**it, 'plat': plat}, 'plat': plat, 'style': style, 'rank': ranks.get(cid, 10 ** 6), 'order': order})
        if not out:
            raise agg.Refused('故事播客现在都没有可播的新一集', 'None of the story podcasts has an episode to play right now')
        return out
    return listen.pooled('stories:%s' % (os.path.getmtime(PATH) if os.path.exists(PATH) else 0), now, make)


def items(kind, arg, now):
    """-> the episodes of one list: shows that are on a chart first (by their place), then the ones picked by hand in the order they were picked."""
    es = [e for e in entries(now) if (e['style'] == arg if kind == 'style' else e['plat'] == arg)]
    es.sort(key=lambda e: (e['rank'] >= 10 ** 6, e['rank'], e['order']))
    return [dict(e['item']) for e in es]
