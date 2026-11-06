"""Story podcasts (stories.py) and audiobooks (audiobooks.py): every answer is a fixed sample (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest tests.test_stories
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import audiobooks  # noqa: E402
import listen  # noqa: E402
import stories  # noqa: E402

NOW = 1793000000
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def chart(ids):
    return json.dumps({'feed': {'entry': [{'id': {'attributes': {'im:id': str(i)}}} for i in ids]}})


def show(cid, name, feed, ep=1):
    """What iTunes lookup says of one show: the show itself and its newest episode."""
    return [{'wrapperType': 'track', 'kind': 'podcast', 'collectionId': cid, 'collectionName': name, 'feedUrl': feed},
            {'wrapperType': 'podcastEpisode', 'kind': 'podcast-episode', 'collectionId': cid, 'trackId': cid * 10 + ep, 'trackName': '%s 第%d期' % (name, ep), 'collectionName': name, 'feedUrl': feed,
             'episodeUrl': 'https://jt.example.com/%d.m4a' % cid, 'trackTimeMillis': 1500000, 'releaseDate': '2026-11-0%dT09:00:00Z' % ep, 'artworkUrl600': 'https://is1-ssl.mzstatic.com/p%d/600x600bb.jpg' % cid,
             'trackViewUrl': 'https://podcasts.apple.com/cn/podcast/x/id%d?i=%d' % (cid, cid * 10 + ep)}]


XM, XY, LZ = 'https://www.ximalaya.com/album/%d.xml', 'https://feed.xyzfm.space/%s', 'http://rss.lizhi.fm/rss/%d.xml'
SHOWS = {                                                   # collection id -> (name, feed): the ones picked by hand (1001-1004) and the ones that are only on a chart
    1001: ('鲶鱼夜话', XM % 66572833), 1002: ('入夜电波', XY % 'abc123'), 1003: ('黑鲨诡话', LZ % 3528895), 1004: ('野史下酒', 'https://s1.proxy.wavpub.com/yeshixiajiu.xml'),
    2001: ('松鼠大侦探', XY % 'sq456'), 2002: ('凌晨1点鬼录 | 悬疑灵异', XM % 71974167), 2003: ('穿越之重生爽文有声书', XM % 5), 2004: ('搞钱女孩', XM % 6), 2005: ('夜话怪谈小说多播', XM % 7), 2006: ('晚安故事', XM % 8)}
PICKED = {'version': 1, 'styles': [{'id': 'mystery', 'name': '悬疑怪谈', 'name_en': 'Mystery'}, {'id': 'crime', 'name': '真实罪案', 'name_en': 'True crime'}, {'id': 'history', 'name': '历史野史', 'name_en': 'History'},
                                   {'id': 'sleep', 'name': '睡前故事', 'name_en': 'Bedtime'}],
          'shows': [{'id': 'nianyu', 'name': '鲶鱼夜话', 'platform': 'ximalaya', 'style': 'mystery', 'apple_id': 1001, 'feed': XM % 66572833, 'why': 'w'},
                    {'id': 'ruye', 'name': '入夜电波', 'platform': 'xiaoyuzhou', 'style': 'mystery', 'apple_id': 1002, 'feed': XY % 'abc123', 'why': 'w'},
                    {'id': 'heisha', 'name': '黑鲨诡话', 'platform': 'lizhi', 'style': 'mystery', 'apple_id': 1003, 'feed': LZ % 3528895, 'why': 'w'},
                    {'id': 'yeshi', 'name': '野史下酒', 'platform': 'wavpub', 'style': 'history', 'apple_id': 1004, 'feed': 'https://s1.proxy.wavpub.com/yeshixiajiu.xml', 'why': 'w'},
                    {'id': 'bad1', 'name': 'bad id', 'style': 'nope', 'apple_id': 1005, 'feed': XM % 1},
                    {'id': 'bad2', 'name': 'no apple id', 'style': 'mystery', 'apple_id': 'x', 'feed': XM % 2},
                    {'id': 'bad3', 'name': 'no feed', 'style': 'mystery', 'apple_id': 1006, 'feed': 'javascript:alert(1)'},
                    {'id': 'Bad Id!', 'name': 'x', 'style': 'mystery', 'apple_id': 1007, 'feed': XM % 3}, 'junk', None]}
CHARTS = {'1488': [2001, 2002, 1001], '1483': [2003, 2005, 2006], '1324': [2004], '1462': []}


class Base(unittest.TestCase):
    def setUp(self):
        listen._CACHE.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'stories.json')
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(PICKED, f, ensure_ascii=False)
        self.old = (stories.PATH, listen._get)
        stories.PATH = self.path
        stories._LOADED.clear()
        listen._get = self.fake
        self.asked, self.down = [], set()

    def tearDown(self):
        stories.PATH, listen._get = self.old
        stories._LOADED.clear()
        self.dir.cleanup()

    def fake(self, url, headers=None):
        self.asked.append(url)
        for k in self.down:
            if k in url:
                raise agg.Refused('网站返回了错误（HTTP 500）', 'HTTP 500')
        if '/rss/toppodcasts/' in url:
            g = url.split('genre=')[1].split('/')[0]
            return chart(CHARTS[g])
        if 'itunes.apple.com/lookup' in url:
            ids = [int(i) for i in url.split('id=')[1].split('&')[0].split(',')]
            res = []
            for i in ids:
                if i in SHOWS:
                    res += show(i, *SHOWS[i])
            return json.dumps({'resultCount': len(res), 'results': res})
        raise AssertionError(url)

    def call(self, **q):
        st, ct, body, cache = listen.handle({k: [str(v)] for k, v in q.items()}, {}, NOW)
        return st, json.loads(body), cache


class Parts(Base):
    def test_the_platform_is_told_by_the_host_of_the_feed(self):
        P = stories.platform_of
        self.assertEqual([P(XM % 1), P('http://www.ximalaya.com/album/1.xml'), P(XY % 'x'), P(LZ % 1), P('https://cdn.lizhi.fm/rss/1.xml'), P('https://feeds.storyfm.cn/storyfm.xml'), P('https://s1.proxy.wavpub.com/a.xml'), P('https://feeds.heymeowfm.com/h.xml')],
                         ['ximalaya', 'ximalaya', 'xiaoyuzhou', 'lizhi', 'lizhi', 'wavpub', 'wavpub', 'heymeow'])
        self.assertEqual([P('https://evilximalaya.com/x'), P('https://ximalaya.com.evil.example/x'), P(''), P(None), P('not a url')], ['', '', '', '', ''])

    def test_a_name_that_says_a_story_and_not_a_novel(self):
        for good in ('鲶鱼夜话', '哈喽怪谈', '凌晨1点鬼录 | 悬疑灵异', '成人睡前故事', '野史下酒｜有趣的历史故事', '松鼠大侦探', '黑猫侦探社'):
            self.assertTrue(stories.story_name(good), good)
        for bad in ('穿越之重生爽文有声书', '【免费】有声书｜精品多播', '夜话怪谈小说多播', '搞钱女孩', '西游记后传之孙悟空探案', '儿童睡前故事', '《三体》有声书', '斗罗大陆'):
            self.assertFalse(stories.story_name(bad), bad)

    def test_the_style_of_a_name(self):
        S = stories.style_of
        self.assertEqual([S('成人睡前故事'), S('松鼠大侦探'), S('鲶鱼夜话'), S('野史下酒'), S('故事FM'), S('搞钱女孩')], ['sleep', 'crime', 'mystery', 'history', 'true', ''])

    def test_the_picked_list_leaves_out_what_is_not_valid(self):
        cfg = stories.load()
        self.assertEqual([x['id'] for x in cfg['shows']], ['nianyu', 'ruye', 'heisha', 'yeshi'])
        self.assertEqual([s['id'] for s in cfg['styles']], ['mystery', 'crime', 'history', 'sleep'])
        self.assertEqual([x['platform'] for x in cfg['shows']], ['ximalaya', 'xiaoyuzhou', 'lizhi', 'wavpub'])        # (the platform is the host's, a Lizhi feed that is http is fine: the feed is not read here)

    def test_a_missing_or_broken_file_gives_nothing(self):
        stories.PATH = os.path.join(self.dir.name, 'none.json')
        self.assertEqual(stories.load(), {'styles': [], 'shows': []})
        self.assertFalse(stories.available())
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{not json')
        stories.PATH = self.path
        stories._LOADED.clear()
        self.assertEqual(stories.load()['shows'], [])

    def test_the_lists_there_are(self):
        gs = {g['id']: g for g in stories.groups()}
        self.assertEqual(list(gs), ['st-mystery', 'st-history', 'st-hot-ximalaya', 'st-hot-xiaoyuzhou', 'st-hot-lizhi'])          # (a style with no show picked has no list: crime, sleep; wavpub has no hot list)
        for g in gs.values():
            self.assertEqual((g['type'], g['kind'], g['lang'], g['net'], g['policy']), ('podcast', 'list', 'zh', 'cn', 'mix'))
        self.assertEqual(gs['st-hot-lizhi']['name'], '荔枝FM · 故事热门')
        self.assertEqual(gs['st-mystery']['sources'], [('stories', 'style', 'mystery')])


class Lists(Base):
    def test_the_shows_of_a_list_picked_ones_and_the_ones_of_the_charts(self):
        its = stories.items('style', 'mystery', NOW)
        self.assertEqual([x['artist'] for x in its], ['凌晨1点鬼录 | 悬疑灵异', '鲶鱼夜话', '入夜电波', '黑鲨诡话'])           # (on a chart first, by their place (2002 is second of the true crime chart, 1001 third); the picked ones that are on none after)
        self.assertEqual([x['plat'] for x in its], ['ximalaya', 'ximalaya', 'xiaoyuzhou', 'lizhi'])
        x = its[1]
        self.assertEqual((x['mode'], x['audio'], x['from']), ('stream', 'https://jt.example.com/1001.m4a', 'Apple Podcasts'))
        self.assertTrue(x['id'].startswith('pc:') and x['title'].startswith('鲶鱼夜话') and x['cover'].startswith('https://') and x['dur'] == 1500 and x['date'] == '2026-11-01' and x['show'] == '1001')
        crime = stories.items('style', 'crime', NOW)
        self.assertEqual([x['artist'] for x in crime], ['松鼠大侦探'])                                                          # (found on the chart by its name; nobody picked it)

    def test_a_show_that_reads_a_novel_is_not_in_any_list(self):
        names = {x['artist'] for style in ('mystery', 'crime', 'true', 'history', 'sleep') for x in stories.items('style', style, NOW)}
        self.assertNotIn('穿越之重生爽文有声书', names)
        self.assertNotIn('夜话怪谈小说多播', names)
        self.assertNotIn('搞钱女孩', names)                                                                                  # (a talk show, not a story)
        self.assertIn('晚安故事', names)                                                                                     # (a bedtime story that is on the fiction chart)

    def test_the_hot_list_of_a_platform(self):
        xm = stories.items('plat', 'ximalaya', NOW)
        self.assertEqual([x['artist'] for x in xm], ['凌晨1点鬼录 | 悬疑灵异', '鲶鱼夜话', '晚安故事'])                                  # (by the place on the charts; a tie: the one picked by hand first)
        self.assertTrue(all(x['plat'] == 'ximalaya' for x in xm))
        self.assertEqual([x['artist'] for x in stories.items('plat', 'xiaoyuzhou', NOW)], ['松鼠大侦探', '入夜电波'])
        self.assertEqual([x['artist'] for x in stories.items('plat', 'lizhi', NOW)], ['黑鲨诡话'])

    def test_a_chart_that_does_not_answer_is_left_out_the_picked_shows_remain(self):
        self.down = {'genre=1488'}
        its = stories.items('style', 'mystery', NOW)
        self.assertEqual([x['artist'] for x in its], ['鲶鱼夜话', '入夜电波', '黑鲨诡话'])                                      # (the chart of true crime does not answer: 2002 and the place of the others are gone)
        self.assertEqual(stories.items('style', 'crime', NOW), [])

    def test_the_lookup_that_does_not_answer_is_an_error_and_an_older_copy_is_served(self):
        stories.items('style', 'mystery', NOW)
        later = NOW + listen.POOL_SECONDS + 3600
        self.down = {'itunes.apple.com'}
        its = stories.items('style', 'mystery', later)                                                                   # (served from the copy of before: 36 hours are allowed)
        self.assertEqual(len(its), 4)
        listen._CACHE.clear()
        with self.assertRaises(agg.Refused):
            stories.items('style', 'mystery', later)

    def test_one_question_for_all_the_lists(self):
        for g in ('style', 'mystery'), ('style', 'sleep'), ('plat', 'ximalaya'):
            stories.items(g[0], g[1], NOW)
        lookups = [u for u in self.asked if 'itunes.apple.com/lookup' in u]
        self.assertEqual(len(lookups), 1)                                                                                # (ten shows: one lookup, kept for all the lists)
        self.assertEqual(len([u for u in self.asked if 'toppodcasts' in u]), 4)


class Catalogue(Base):
    def test_a_page_that_asks_for_caps_resolve_sees_the_lists_a_page_that_does_not_does_not(self):
        st, d, _ = self.call(caps='resolve', lang='zh')
        by = {g['id']: g for g in d['groups']}
        for gid in ('st-mystery', 'st-history', 'st-hot-ximalaya', 'st-hot-xiaoyuzhou', 'st-hot-lizhi'):
            self.assertEqual((by[gid]['type'], by[gid]['kind'], by[gid]['region'], by[gid]['lang']), ('podcast', 'list', 'cn', 'zh'), gid)
        st, d, _ = self.call(lang='zh')
        self.assertFalse(any(g['id'].startswith('st-') for g in d['groups']))
        st, d, _ = self.call(g='st-mystery')
        self.assertEqual(st, 404)

    def test_a_reader_of_english_does_not_get_them(self):
        st, d, _ = self.call(caps='resolve', lang='en')
        self.assertFalse(any(g['id'].startswith('st-') for g in d['groups']))
        self.assertEqual(self.call(g='st-mystery', caps='resolve', lang='en')[0], 404)

    def test_a_list_has_its_episodes(self):
        st, d, _ = self.call(g='st-hot-xiaoyuzhou', caps='resolve', lang='zh')
        self.assertEqual(st, 200, d)
        g = d['group']
        self.assertEqual((g['type'], g['kind']), ('podcast', 'list'))
        self.assertEqual([x['artist'] for x in g['items']], ['松鼠大侦探', '入夜电波'])
        self.assertEqual(g['sub'], '来源：Apple Podcasts')
        self.assertTrue(all(x['mode'] == 'stream' and x['audio'].startswith('https://') for x in g['items']))

    def test_the_lists_are_off_without_a_picked_list(self):
        stories.PATH = os.path.join(self.dir.name, 'none.json')
        stories._LOADED.clear()
        st, d, _ = self.call(caps='resolve', lang='zh')
        self.assertFalse(any(g['id'].startswith('st-') for g in d['groups']))


# ---------------------------------------------------------------- audiobooks
def rss(items, head=''):
    return '<?xml version="1.0"?><rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0"><channel><title>Book</title>%s%s</channel></rss>' % (head, ''.join(items))


def item(title, url='https://archive.org/download/x/%s.mp3', dur='0:10:00', cdata=False, extra=''):
    t = '<![CDATA[%s]]>' % title if cdata else title
    u = (url % title.replace(' ', '_')) if '%s' in url else url
    return '<item><title>%s</title>%s<enclosure url="%s" length="123" type="audio/mpeg"/><itunes:duration>%s</itunes:duration></item>' % (t, extra, u, dur)


BOOKS = {'version': 1, 'styles': [{'id': 'classic', 'name': '经典名著', 'name_en': 'Classics'}, {'id': 'story', 'name': '故事悬疑', 'name_en': 'Stories'}],
         'books': [{'id': 'lv-1', 'title': '爱丽丝', 'title_en': 'Alice', 'author': 'Carroll', 'narrator': 'LibriVox 志愿者', 'narrator_en': 'LibriVox volunteers', 'lang': 'en', 'style': ['classic', 'nope'], 'rights': 'public-domain', 'net': 'global', 'source': 'librivox',
                   'feed': 'https://librivox.org/rss/1', 'order': 'asc', 'cover': 'https://archive.org/services/img/a', 'summary': 's', 'n': 3, 'secs': 100, 'hot': 2, 'url': 'https://librivox.org/a'},
                  {'id': 'cn-2', 'title': '红楼梦', 'title_en': 'Dream', 'author': '曹雪芹', 'narrator': '朗读', 'lang': 'zh', 'style': ['classic'], 'rights': 'public-domain', 'source': 'ximalaya.com', 'feed': 'https://www.ximalaya.com/album/2.xml',
                   'order': 'desc', 'cover': 'http://insecure.example/c.jpg', 'summary': 's', 'n': 0, 'secs': 0, 'hot': 1},
                  {'id': 'bad-rights', 'title': 'x', 'feed': 'https://x.example/f', 'rights': 'pirated', 'order': 'asc'},
                  {'id': 'bad-order', 'title': 'x', 'feed': 'https://x.example/f', 'rights': 'public-domain', 'order': 'sideways'},
                  {'id': 'bad-feed', 'title': 'x', 'feed': 'javascript:1', 'rights': 'public-domain', 'order': 'asc'}, {'id': 'cn-2', 'title': 'dup', 'feed': 'https://x.example/f', 'rights': 'public-domain', 'order': 'asc'}, 'junk']}


class Audio(unittest.TestCase):
    def setUp(self):
        listen._CACHE.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'audiobooks.json')
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(BOOKS, f, ensure_ascii=False)
        self.old = (audiobooks.PATH, listen._get)
        audiobooks.PATH = self.path
        audiobooks._LOADED.clear()
        listen._get = self.fake
        self.feeds = {'https://librivox.org/rss/1': rss([item('Chapter 1', dur='0:10:30'), item('Chapter 2', dur='600'), item('Chapter 3', dur='')]),
                      'https://www.ximalaya.com/album/2.xml': rss([item('回 3'), item('回 2'), item('回 1')])}
        self.down = set()

    def tearDown(self):
        audiobooks.PATH, listen._get = self.old
        audiobooks._LOADED.clear()
        self.dir.cleanup()

    def fake(self, url, headers=None):
        if url in self.down:
            raise agg.Refused('网站返回了错误（HTTP 500）', 'HTTP 500')
        return self.feeds[url]

    def call(self, now=NOW, **q):
        st, ct, body, cache = listen.handle({k: [str(v)] for k, v in q.items()}, {}, now)
        return st, json.loads(body), cache

    def test_the_chapters_of_a_feed(self):
        ch = audiobooks.parse_chapters(self.feeds['https://librivox.org/rss/1'])
        self.assertEqual([(c['n'], c['title'], c['dur']) for c in ch], [(1, 'Chapter 1', 630), (2, 'Chapter 2', 600), (3, 'Chapter 3', 0)])
        self.assertTrue(all(c['audio'].startswith('https://archive.org/download/x/') for c in ch))

    def test_a_feed_with_the_newest_first_is_turned_round(self):
        ch = audiobooks.parse_chapters(self.feeds['https://www.ximalaya.com/album/2.xml'], 'desc')
        self.assertEqual([(c['n'], c['title']) for c in ch], [(1, '回 1'), (2, '回 2'), (3, '回 3')])

    def test_what_is_not_a_chapter_is_left_out(self):
        cut = rss([item('Whole one'), '<item><title>No sound</title></item>', item('<b>Tagged</b> &amp; escaped', url='https://x.example/tagged.mp3'), item('With CDATA', cdata=True), item('Http one', url='http://cdn.example.com/a.mp3?x=1&amp;y=2')])
        cut += '<item><title>Cut off</title><enclosure url="https://x.example/cut.mp3"'                                        # (the feed was read up to its limit: the last item is cut in two)
        ch = audiobooks.parse_chapters(cut)
        self.assertEqual([c['title'] for c in ch], ['Whole one', 'Tagged & escaped', 'With CDATA', 'Http one'])
        self.assertEqual(ch[3]['audio'], 'https://cdn.example.com/a.mp3?x=1&y=2')                                              # (http becomes https; the entities of a feed are read)
        self.assertEqual(audiobooks.parse_chapters(''), [])
        self.assertEqual(audiobooks.parse_chapters(None), [])

    def test_at_most_six_hundred_chapters(self):
        ch = audiobooks.parse_chapters(rss([item('C %d' % i) for i in range(700)]))
        self.assertEqual((len(ch), ch[-1]['n']), (600, 600))

    def test_the_list_of_books_leaves_out_what_is_not_valid(self):
        cfg = audiobooks.load()
        self.assertEqual([b['id'] for b in cfg['books']], ['lv-1', 'cn-2'])
        self.assertEqual(cfg['books'][0]['style'], ['classic'])                                                                # (a style that is not in the list is dropped)
        self.assertEqual(cfg['books'][1]['cover'], '')                                                                         # (a picture that is not https is dropped)
        self.assertEqual([b['net'] for b in cfg['books']], ['global', 'cn'])                                                   # (where the sound is: abroad for LibriVox; not said: here)

    def test_the_catalogue(self):
        st, d, cache = self.call(books=1, lang='zh')
        self.assertEqual(st, 200)
        self.assertTrue(d['ok'])
        self.assertEqual([b['id'] for b in d['books']], ['cn-2', 'lv-1'])                                                      # (the hottest first)
        self.assertEqual([s['id'] for s in d['styles']], ['classic', 'story'])
        self.assertEqual(d['books'][1]['narrator'], 'LibriVox 志愿者')
        self.assertNotIn('feed', d['books'][0])                                                                                # (the address of the feed is the server's business)
        self.assertIn('s-maxage=1800', cache)
        self.assertEqual(self.call(books=1, lang='en')[1]['books'][1]['narrator'], 'LibriVox volunteers')

    def test_the_catalogue_of_nothing(self):
        audiobooks.PATH = os.path.join(self.dir.name, 'none.json')
        self.assertEqual(self.call(books=1)[0], 404)

    def test_one_book_with_all_its_chapters_the_first_first(self):
        st, d, cache = self.call(book='lv-1')
        self.assertEqual(st, 200, d)
        b = d['book']
        self.assertEqual([(c['n'], c['title']) for c in b['chapters']], [(1, 'Chapter 1'), (2, 'Chapter 2'), (3, 'Chapter 3')])
        self.assertEqual((b['n'], b['secs'], b['id']), (3, 1230, 'lv-1'))
        self.assertNotIn('feed', b)
        self.assertNotIn('stale', d)
        self.assertIn('s-maxage=3600', cache)
        st, d, _ = self.call(book='cn-2')
        self.assertEqual([c['title'] for c in d['book']['chapters']], ['回 1', '回 2', '回 3'])                                  # (the feed had the newest first)

    def test_a_book_there_is_not_and_a_feed_that_does_not_answer(self):
        self.assertEqual(self.call(book='nope')[0], 404)
        self.assertEqual(self.call(book='../etc/passwd')[0], 404)
        self.down = {'https://librivox.org/rss/1'}
        st, d, cache = self.call(book='lv-1')
        self.assertEqual((st, d['ok'], cache), (502, False, 'no-store'))

    def test_an_older_copy_is_served_when_the_feed_stops_answering(self):
        self.assertEqual(self.call(book='lv-1')[0], 200)
        self.down = {'https://librivox.org/rss/1'}
        st, d, _ = self.call(now=NOW + listen.POOL_SECONDS + 3600, book='lv-1')
        self.assertEqual(st, 200)
        self.assertTrue(d['stale'])
        self.assertEqual(len(d['book']['chapters']), 3)
        self.assertEqual(self.call(now=NOW + listen.STALE_SECONDS + 7200, book='lv-1')[0], 502)

    def test_a_feed_with_no_chapter_is_an_error(self):
        self.feeds['https://librivox.org/rss/1'] = rss([])
        self.assertEqual(self.call(book='lv-1')[0], 502)

    def test_listen_off_turns_it_off(self):
        st, ct, body, cache = listen.handle({'books': ['1']}, {'LISTEN': '0'}, NOW)
        self.assertEqual(st, 404)


class RegistryFiles(unittest.TestCase):
    """The lists that ship with the service: every entry is valid (a bad entry would be left out without a word)."""

    def test_the_audiobooks_that_ship(self):
        with open(os.path.join(ROOT, 'registry', 'audiobooks.json'), encoding='utf-8') as f:
            raw = json.load(f)
        audiobooks._LOADED.clear()
        cfg = audiobooks.load(os.path.join(ROOT, 'registry', 'audiobooks.json'))
        self.assertEqual(len(cfg['books']), len(raw['books']))
        self.assertGreaterEqual(len(cfg['books']), 12)
        self.assertEqual(len({b['id'] for b in cfg['books']}), len(cfg['books']))
        for b in cfg['books']:
            self.assertIn(b['rights'], ('public-domain', 'broadcaster'))
            self.assertTrue(b['style'], b['id'])
            self.assertTrue(b['hot'] > 0, b['id'])
        self.assertTrue({'zh', 'en'} <= {b['lang'] for b in cfg['books']})
        self.assertTrue(all((b['net'] == 'global') == b['id'].startswith('lv-') for b in cfg['books']))                       # (LibriVox's sounds are on archive.org)
        self.assertEqual(len({b['hot'] for b in cfg['books']}), len(cfg['books']))

    def test_the_stories_that_ship(self):
        with open(os.path.join(ROOT, 'registry', 'stories.json'), encoding='utf-8') as f:
            raw = json.load(f)
        stories._LOADED.clear()
        cfg = stories.load(os.path.join(ROOT, 'registry', 'stories.json'))
        self.assertEqual(len(cfg['shows']), len(raw['shows']))
        self.assertGreaterEqual(len(cfg['shows']), 15)
        self.assertEqual(len({s['id'] for s in cfg['shows']}), len(cfg['shows']))
        self.assertEqual(len({s['apple_id'] for s in cfg['shows']}), len(cfg['shows']))
        for s in cfg['shows']:
            self.assertTrue(s['platform'] in stories.PLATFORM_NAMES, s['id'])
            self.assertEqual(s['platform'], stories.platform_of(s['feed']))
        for p in stories.HOT_PLATFORMS:
            self.assertTrue(any(s['platform'] == p for s in cfg['shows']), p)                                                   # (every platform with a hot list has a show picked)


if __name__ == '__main__':
    unittest.main()
