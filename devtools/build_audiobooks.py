"""Make registry/audiobooks.json: the audiobooks of the directory (public domain or the broadcaster's own: nothing else goes in). Run it again when the list changes:

    python -X utf8 devtools/build_audiobooks.py            look at what it would write (every book: feed read, chapters counted, order checked)
    python -X utf8 devtools/build_audiobooks.py --write    write registry/audiobooks.json

LibriVox books (public domain, English) are looked up by title in the LibriVox API (https://librivox.org/api/feed/audiobooks/): its RSS gives the chapters in order, the sounds are on archive.org, and archive.org
says how often each book was downloaded: that order is "hot". The Chinese books are public-domain classics read aloud by their publisher's own feed (the text is old, the recording is the publisher's); each is
checked the same way. `order`: asc = the first chapter is the first item of the feed; desc = the feed has the newest first (the page turns it round)."""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppData/Chrome/124 Safari/537.36'}
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'registry', 'audiobooks.json')

STYLES = [
    {'id': 'classic', 'name': '经典名著', 'name_en': 'Classics'},
    {'id': 'story', 'name': '故事悬疑', 'name_en': 'Stories & mystery'},
    {'id': 'fairy', 'name': '童话寓言', 'name_en': 'Fairy tales & fables'},
    {'id': 'guoxue', 'name': '国学经典', 'name_en': 'Chinese classics'},
]
# (query for the title at LibriVox, name in Chinese, styles). The first recording whose title is the query and that has a sensible number of chapters is taken.
LIBRIVOX = [
    ('Adventures of Sherlock Holmes', '福尔摩斯探案集：冒险史', ['story']),
    ("Alice's Adventures in Wonderland", '爱丽丝梦游仙境', ['fairy', 'classic']),
    ('Pride and Prejudice', '傲慢与偏见', ['classic']),
    ('Jane Eyre', '简·爱', ['classic']),
    ("Grimms' Fairy Tales", '格林童话', ['fairy']),
    ("Aesop's Fables", '伊索寓言', ['fairy']),
    ('Count of Monte Cristo', '基督山伯爵', ['classic', 'story']),
    ('Frankenstein', '弗兰肯斯坦（科学怪人）', ['story', 'classic']),
    ('Dracula', '德古拉', ['story', 'classic']),
    ('Canterville Ghost', '坎特维尔的幽灵', ['story']),
    ('Hound of the Baskervilles', '巴斯克维尔的猎犬', ['story']),
    ('Treasure Island', '金银岛', ['story', 'classic']),
    ('Christmas Carol', '圣诞颂歌', ['story', 'classic']),
    ('Strange Case of Dr. Jekyll and Mr. Hyde', '化身博士', ['story', 'classic']),
]
# the Chinese books: id, title, English title, author, narrator, styles, Apple Podcasts id (for the picture), feed, summary
CHINESE = [
    ('hlm', '红楼梦', 'Dream of the Red Chamber', '曹雪芹', '个人音色 AI 朗读', ['guoxue', 'classic'], 1713901541, 'https://www.ximalaya.com/album/78660042.xml', '曹雪芹的长篇小说，原著全文，一回一集。'),
    ('xyj', '西游记', 'Journey to the West', '吴承恩', '男中音朗读', ['guoxue', 'classic'], 1713902335, 'https://www.ximalaya.com/album/78633443.xml', '吴承恩的神魔小说，原著全文。'),
    ('shz', '水浒传', 'Water Margin', '施耐庵', '男中音朗读', ['guoxue', 'classic'], 1713902748, 'https://www.ximalaya.com/album/78661218.xml', '施耐庵的英雄传奇，原著全文。'),
    ('lz', '聊斋志异', 'Strange Tales from a Chinese Studio', '蒲松龄', 'AI 朗读', ['guoxue', 'story'], 1777337891, 'https://www.ximalaya.com/album/84403983.xml', '蒲松龄的志怪短篇，狐仙鬼怪与人间百态。'),
]


def get(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def jget(url, timeout=60):
    return json.loads(get(url, timeout))


def secs_of(text):
    t = (text or '').strip()
    if not t:
        return 0
    try:
        if ':' in t:
            n = 0
            for p in t.split(':'):
                n = n * 60 + float(p)
            return int(n)
        return int(float(t))
    except ValueError:
        return 0


def feed_info(url):
    """-> (chapters, total seconds, first title, last title, first pubDate, last pubDate) of an RSS feed."""
    root = ET.fromstring(get(url, 90))
    items = root.find('channel').findall('item')
    ns = {'it': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
    secs = sum(secs_of(i.findtext('it:duration', namespaces=ns)) for i in items)
    return len(items), secs, (items[0].findtext('title') or '')[:30], (items[-1].findtext('title') or '')[:30], (items[0].findtext('pubDate') or '')[:16], (items[-1].findtext('pubDate') or '')[:16]


def order_of(first_date, last_date, n):
    """asc when the first item is the oldest (or the dates are all the same), desc when the newest is first."""
    from email.utils import parsedate_to_datetime
    try:
        a, b = parsedate_to_datetime(first_date), parsedate_to_datetime(last_date)
    except Exception:
        return 'asc'
    return 'desc' if a > b else 'asc'


def clean(text, n):
    t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(text or ''))).strip()
    return t[:n].rsplit(' ', 1)[0] + '…' if len(t) > n else t


def librivox_books():
    out = []
    for query, zh, styles in LIBRIVOX:
        url = 'https://librivox.org/api/feed/audiobooks/?format=json&extended=1&limit=12&title=' + urllib.parse.quote('^' + query)
        try:
            d = jget(url)
        except Exception as e:
            print('!! LibriVox', query, str(e)[:60]); continue
        cands = [b for b in d.get('books') or [] if b.get('language') == 'English' and b['title'].strip().lower().startswith(query.lower()) and 3 <= int(b.get('num_sections') or 0) <= 130]
        if not cands:
            print('!! no recording', query); continue
        b = cands[0]
        au = (b.get('authors') or [{}])[0]
        ident = (b.get('url_iarchive') or '').rstrip('/').rsplit('/', 1)[-1]
        out.append({'id': 'lv-%s' % b['id'], 'title': zh, 'title_en': b['title'].strip(), 'author': ('%s %s' % (au.get('first_name', ''), au.get('last_name', ''))).strip(), 'narrator': 'LibriVox 志愿者', 'narrator_en': 'LibriVox volunteers',
                    'lang': 'en', 'style': styles, 'rights': 'public-domain', 'net': 'global', 'source': 'librivox', 'feed': b['url_rss'], 'order': 'asc', 'ident': ident,
                    'cover': ('https://archive.org/services/img/' + ident) if ident else '', 'summary': clean(b.get('description'), 150), 'n': int(b['num_sections']), 'secs': int(b.get('totaltimesecs') or 0), 'url': b.get('url_librivox', '')})
    # how often archive.org gave each of them away: the order of "hot"
    idents = [x['ident'] for x in out if x['ident']]
    if idents:
        q = ' OR '.join('identifier:"%s"' % i for i in idents)
        d = jget('https://archive.org/advancedsearch.php?q=%s&fl[]=identifier&fl[]=downloads&rows=50&output=json' % urllib.parse.quote(q))
        dl = {x['identifier']: int(x.get('downloads') or 0) for x in d['response']['docs']}
        for x in out:
            x['downloads'] = dl.get(x['ident'], 0)
    return out


def chinese_books():
    out = []
    for cid, zh, en, author, narrator, styles, apple, feed, summary in CHINESE:
        try:
            n, secs, f1, f2, d1, d2 = feed_info(feed)
        except Exception as e:
            print('!! feed', zh, str(e)[:70]); continue
        cover = ''
        try:
            r = jget('https://itunes.apple.com/lookup?id=%s&country=cn' % apple)['results'][0]
            cover = r.get('artworkUrl600') or ''
        except Exception:
            pass
        order = order_of(d1, d2, n)
        print('   %-8s %4d chapters %7.1f h | %s ... %s | %s -> %s | %s' % (zh, n, secs / 3600, f1, f2, d1, d2, order))
        out.append({'id': 'cn-' + cid, 'title': zh, 'title_en': en, 'author': author, 'narrator': narrator, 'narrator_en': narrator, 'lang': 'zh', 'style': styles, 'rights': 'public-domain', 'net': 'cn', 'source': urllib.parse.urlsplit(feed).hostname,
                    'feed': feed, 'order': order, 'cover': cover, 'summary': summary, 'n': n, 'secs': secs, 'url': ''})
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    lv = librivox_books()
    for x in lv:
        print('   %-34s %4d chapters %6.1f h | downloads %7d | %s' % (x['title_en'][:34], x['n'], x['secs'] / 3600, x.get('downloads', 0), x['feed']))
    lv.sort(key=lambda x: -x.get('downloads', 0))
    cn = chinese_books()
    books = cn + lv
    for i, b in enumerate(books):
        b['hot'] = i + 1
        b.pop('ident', None)
        b.pop('downloads', None)
    doc = {'version': 1, 'updated': '2026-11-06', 'styles': STYLES, 'books': books}
    print('%d books' % len(books))
    if '--write' in sys.argv:
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write('\n')
        print('written', os.path.abspath(OUT))


if __name__ == '__main__':
    main()
