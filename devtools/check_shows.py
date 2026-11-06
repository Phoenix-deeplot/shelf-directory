"""Check shows before they go into registry/stories.json: ask Apple Podcasts (China) for each name, then read the feed: is it readable, how many episodes, when was the newest, does the sound of the
newest episode answer (Range request). Usage:  python -X utf8 devtools_check_shows.py "鲶鱼夜话" "哈喽怪谈" ...   (or without names: the built-in candidates)
Prints one line per show: apple id | name | platform | episodes | newest | sound | feed."""
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'}
CANDIDATES = ['鲶鱼夜话', '哈喽怪谈', 'TSP怪奇档案', '生人勿近', '黑猫侦探社', '入夜电波', '落日之后', '不吱声', '松鼠大侦探', '贝壳回音', '故事FM', '野史下酒', '岩中花述', '三个火呛手',
              '晓东讲故事-奇闻档案', '成人睡前故事', '淘白白的晚安故事（深度助眠）', '清评书苑', '怪谈', '睡前故事', '午夜怪谈', '鬼故事', '夜听', '真实故事', '故事里的中国', '刑侦', '大案纪实', '悬疑']
PLATFORMS = (('ximalaya.com', 'ximalaya'), ('xyzfm.space', 'xiaoyuzhou'), ('lizhi.fm', 'lizhi'), ('wavpub.com', 'wavpub'), ('storyfm.cn', 'wavpub'), ('soundon.fm', 'soundon'), ('firstory.me', 'firstory'))


def get(url, data=False, timeout=30):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
    return r.read()


def platform(feed):
    host = urllib.parse.urlsplit(feed).hostname or ''
    for dom, p in PLATFORMS:
        if host == dom or host.endswith('.' + dom):
            return p
    return host


def check(name):
    import json
    d = json.loads(get('https://itunes.apple.com/search?media=podcast&entity=podcast&limit=5&country=cn&term=' + urllib.parse.quote(name)))
    rows = d.get('results') or []
    if not rows:
        print('%-22s NOT FOUND' % name)
        return
    hit = next((r for r in rows if r.get('collectionName') == name), rows[0])
    feed = hit.get('feedUrl') or ''
    line = '%s | %s | %s | ' % (hit['collectionId'], hit['collectionName'][:30], platform(feed))
    try:
        raw = get(feed, timeout=40)
        root = ET.fromstring(raw)
        items = root.find('channel').findall('item')
        dates = []
        for it in items[:3]:
            try:
                dates.append(parsedate_to_datetime(it.findtext('pubDate')).date().isoformat())
            except Exception:
                pass
        newest = max(dates) if dates else '?'
        enc = items[0].find('enclosure')
        sound = '-'
        if enc is not None:
            try:
                rr = urllib.request.urlopen(urllib.request.Request(enc.get('url'), headers={**UA, 'Range': 'bytes=0-1'}), timeout=20)
                sound = '%s %s' % (rr.status, (rr.headers.get('Content-Type') or '')[:12])
            except Exception as e:
                sound = 'ERR ' + str(e)[:30]
        line += '%d eps | %s | %s | ' % (len(items), newest, sound)
    except Exception as e:
        line += 'FEED ERR %s | ' % str(e)[:50]
    print(line + feed)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    for n in (sys.argv[1:] or CANDIDATES):
        try:
            check(n)
        except Exception as e:
            print('%-22s ERR %s' % (n, str(e)[:60]))
