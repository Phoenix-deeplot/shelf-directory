"""What is behind a row of a hot list (hotdetail.py): every platform's answer is fake here (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import hotapi  # noqa: E402
import hotdetail  # noqa: E402

V2_TOPIC = [{'id': 1252074, 'title': '停电了', 'content': 'x', 'replies': 2, 'member': {'username': 'alice'}, 'node': {'title': '生活'},
             'content_rendered': '<p>正文 <script>alert(1)</script><a href="https://a.example/x" onclick="x()">链接</a></p>'}]
V2_REPLIES = [{'member': {'username': 'bob'}, 'content_rendered': '<p>第一条回复</p>'}, {'member': {'username': 'carol'}, 'content_rendered': '<p>第二条</p>'}]
ZH_STORY = {'title': '日报的一篇', 'image': 'http://pic.zhimg.com/x.jpg', 'body': '<div class="question"><h2 class="question-title">问题</h2></div><script>x()</script><div class="answer"><p>答案的正文</p></div>'}
PODCAST = {'resultCount': 1, 'results': [{'collectionName': '声动早咖啡', 'artistName': '声动活泼', 'trackCount': 90, 'primaryGenreName': '商务', 'artworkUrl600': 'https://is1.example/a.jpg', 'feedUrl': 'http://feeds.example/rss'}]}
BING = ('<?xml version="1.0" encoding="utf-8" ?><rss version="2.0" xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss"><channel><title>x</title>'
        '<item><title>A story</title><link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;tid=1&amp;url=https%3a%2f%2fwww.example.com%2fa%3fid%3d1&amp;c=1&amp;mkt=zh-cn</link>'
        '<description>Because &lt;b&gt;this&lt;/b&gt; happened</description><pubDate>Mon, 02 Mar 2026 10:00:00 GMT</pubDate><News:Source>Example</News:Source></item>'
        '<item><title>A story</title><link>http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fwww.example.com%2fb</link></item>'
        '<item><title>On bing itself</title><link>http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fwww.bing.com%2fx</link></item>'
        '<item><title>Not https</title><link>http://www.bing.com/news/apiclick.aspx?url=http%3a%2f%2fplain.example%2fa</link></item>'
        '<item><title>没有地址</title><link>http://www.bing.com/news/apiclick.aspx?c=1</link></item></channel></rss>')


def fake(table):
    """A stand-in for hotdetail._get: the first key the address starts with decides. An Exception is raised, a str / bytes is the body, anything else is JSON."""
    calls = []

    def get(url, accept='*/*', limit=0, headers=None):
        calls.append((url, dict(headers or {})))
        for k, v in table.items():
            if url.startswith(k):
                if isinstance(v, Exception):
                    raise v
                body = v if isinstance(v, (str, bytes)) else json.dumps(v)
                return url, {'content-type': 'text/plain; charset=utf-8'}, body.encode('utf-8') if isinstance(body, str) else body
        raise agg.Refused('没有', 'no', 404)
    get.calls = calls
    return get


class Base(unittest.TestCase):
    def setUp(self):
        hotdetail._COPY.clear()
        hotdetail._REL_USED.clear()

    def use(self, table):
        g = fake(table)
        p = mock.patch.object(hotdetail, '_get', g)
        p.start()
        self.addCleanup(p.stop)
        return g


class TestDetail(Base):
    def test_a_v2ex_topic_comes_with_its_replies_as_safe_html(self):
        self.use({'https://www.v2ex.com/api/topics/show.json?id=1252074': V2_TOPIC, 'https://www.v2ex.com/api/replies/show.json?topic_id=1252074': V2_REPLIES})
        d = hotdetail.detail('https://www.v2ex.com/t/1252074')
        self.assertEqual((d['ok'], d['kind'], d['title']), (True, 'html', '停电了'))
        self.assertIn('正文', d['content'])
        self.assertIn('第一条回复', d['content'])
        self.assertIn('bob', d['content'])
        self.assertNotIn('<script', d['content'])
        self.assertNotIn('onclick', d['content'])
        self.assertEqual(d['meta'], 'V2EX · 生活 · alice · 2 条回复')

    def test_a_topic_whose_replies_cannot_be_read_is_still_a_topic(self):
        self.use({'https://www.v2ex.com/api/topics/show.json?id=1252074': V2_TOPIC, 'https://www.v2ex.com/api/replies/show.json': agg.Refused('x', 'x', 500)})
        d = hotdetail.detail('https://www.v2ex.com/t/1252074?p=1')
        self.assertIn('正文', d['content'])
        self.assertNotIn('回复', d['content'])

    def test_a_zhihu_daily_story_is_its_text_and_its_picture(self):
        self.use({'https://news-at.zhihu.com/api/4/news/9793540': ZH_STORY})
        d = hotdetail.detail('https://daily.zhihu.com/story/9793540')
        self.assertEqual((d['kind'], d['title'], d['image'], d['meta']), ('html', '日报的一篇', 'https://pic.zhimg.com/x.jpg', '知乎日报'))
        self.assertIn('答案的正文', d['content'])
        self.assertNotIn('<script', d['content'])

    def test_a_github_repository_is_its_readme_the_first_name_that_exists(self):
        g = self.use({'https://raw.githubusercontent.com/o/r/HEAD/README.md': agg.Refused('x', 'x', 404), 'https://raw.githubusercontent.com/o/r/HEAD/readme.md': '# R\n\n![x](a.png)\n'})
        d = hotdetail.detail('https://github.com/o/r')
        self.assertEqual((d['kind'], d['title'], d['base'], d['md']), ('md', 'o/r', 'https://raw.githubusercontent.com/o/r/HEAD/', '# R\n\n![x](a.png)\n'))
        self.assertEqual([c[0].rsplit('/', 1)[1] for c in g.calls], ['README.md', 'readme.md'])

    def test_a_repository_with_no_readme_and_pages_that_are_not_a_repository_have_nothing(self):
        self.use({'https://raw.githubusercontent.com/': agg.Refused('x', 'x', 404)})
        with self.assertRaises(hotdetail.Nothing):
            hotdetail.detail('https://github.com/o/r')
        for u in ('https://github.com/topics/python', 'https://github.com/o/r/issues', 'https://github.com/o', 'https://github.com/sponsors/x'):
            with self.assertRaises(hotdetail.Nothing, msg=u):
                hotdetail.detail(u)

    def test_another_error_is_the_platforms_not_a_missing_page(self):
        self.use({'https://raw.githubusercontent.com/': agg.Refused('挂了', 'down', 500)})
        with self.assertRaises(agg.Refused):
            hotdetail.detail('https://github.com/o/r')

    def test_a_podcast_gives_the_feed_to_listen_to_on_https_in_the_country_of_the_address(self):
        g = self.use({'https://itunes.apple.com/lookup': PODCAST})
        d = hotdetail.detail('https://podcasts.apple.com/cn/podcast/%E5%A3%B0%E5%8A%A8/id1573189055')
        self.assertEqual((d['kind'], d['feed'], d['title'], d['image']), ('podcast', 'https://feeds.example/rss', '声动早咖啡', 'https://is1.example/a.jpg'))
        self.assertEqual(d['meta'], '声动活泼 · 商务 · 90 集')
        self.assertIn('id=1573189055', g.calls[0][0])
        self.assertIn('country=cn', g.calls[0][0])

    def test_a_podcast_without_a_feed_has_nothing(self):
        self.use({'https://itunes.apple.com/lookup': {'results': [{'collectionName': 'x'}]}})
        with self.assertRaises(hotdetail.Nothing):
            hotdetail.detail('https://podcasts.apple.com/us/podcast/x/id12345678')

    def test_an_address_that_is_not_one_of_the_known_shapes_is_never_fetched(self):
        g = self.use({'': {}})
        for u in ('https://evil.example/t/12345', 'https://www.v2ex.com.evil.example/t/12345', 'http://www.v2ex.com/t/12345', 'https://www.v2ex.com/member/x', 'file:///etc/passwd', '', 'https://daily.zhihu.com/story/abc',
                  'https://podcasts.apple.com.evil.example/us/podcast/x/id1234567'):
            with self.assertRaises(hotdetail.Nothing, msg=u):
                hotdetail.detail(u)
        self.assertEqual(g.calls, [])

    def test_the_same_page_is_asked_once_in_a_while(self):
        g = self.use({'https://news-at.zhihu.com/api/4/news/': ZH_STORY})
        hotdetail.detail('https://daily.zhihu.com/story/9793540', now=1000)
        hotdetail.detail('https://daily.zhihu.com/story/9793540', now=1500)
        self.assertEqual(len(g.calls), 1)
        hotdetail.detail('https://daily.zhihu.com/story/9793540', now=1000 + hotdetail.KEEP_SECONDS + 1)
        self.assertEqual(len(g.calls), 2)

    def test_has_detail_tells_the_known_shapes(self):
        self.assertTrue(hotdetail.has_detail('https://www.v2ex.com/t/1252074') and hotdetail.has_detail('https://github.com/o/r'))
        self.assertFalse(hotdetail.has_detail('https://www.zhihu.com/question/1') or hotdetail.has_detail(None))


class TestRelated(Base):
    def test_the_news_about_a_phrase_with_the_address_of_each_story(self):
        g = self.use({'https://www.bing.com/news/search': BING})
        items = hotdetail.related('  姆巴佩 #事件#  ', 'zh')
        self.assertEqual([i['url'] for i in items], ['https://www.example.com/a?id=1', 'https://plain.example/a'])     # the address is inside the link (plain http is asked for on https); Bing's own pages and no address are dropped; the same title of the same site once
        i = items[0]
        self.assertEqual((i['title'], i['source'], i['date'], i['snippet']), ('A story', 'Example', '2026-03-02', 'Because this happened'))
        url, headers = g.calls[0]
        self.assertIn('format=rss', url)
        self.assertNotIn('%23', url)                                                         # (no # in the phrase)
        self.assertTrue(headers['Accept-Language'].startswith('zh-CN'))

    def test_the_language_of_the_page_is_the_language_of_the_news(self):
        g = self.use({'https://www.bing.com/news/search': BING})
        hotdetail.related('OpenAI', 'en')
        self.assertTrue(g.calls[0][1]['Accept-Language'].startswith('en-US'))

    def test_a_phrase_is_asked_once_in_a_while_and_too_many_phrases_are_refused(self):
        g = self.use({'https://www.bing.com/news/search': BING})
        hotdetail.related('same words', 'zh', now=1000)
        hotdetail.related('SAME words', 'zh', now=1100)
        self.assertEqual(len(g.calls), 1)
        for n in range(hotdetail.REL_PER_MINUTE):
            try:
                hotdetail.related('words %d' % n, 'zh', now=1200)
            except agg.Refused:
                break
        with self.assertRaises(agg.Refused):
            hotdetail.related('one more phrase', 'zh', now=1201)
        hotdetail.related('one more phrase', 'zh', now=1300)                                  # a minute later it is fine again

    def test_nothing_to_ask_and_a_page_that_is_not_a_feed(self):
        self.use({'https://www.bing.com/news/search': '<!doctype html><html></html>'})
        for w in ('', ' ', 'a', '#  #'):
            with self.assertRaises(hotdetail.Nothing, msg=w):
                hotdetail.related(w)
        with self.assertRaises(agg.Refused):
            hotdetail.related('some words')
        self.use({'https://www.bing.com/news/search': '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel><item><title>&a;</title></item></channel></rss>'})
        with self.assertRaises(agg.Refused):
            hotdetail.related('other words')


class TestHandle(Base):
    def test_detail_and_related_are_answered_as_json_and_can_be_shared(self):
        self.use({'https://www.v2ex.com/api/topics/show.json': V2_TOPIC, 'https://www.v2ex.com/api/replies/show.json': V2_REPLIES, 'https://www.bing.com/news/search': BING})
        st, ctype, body, cache = hotapi.handle({'detail': ['https://www.v2ex.com/t/1252074']}, {})
        self.assertEqual((st, ctype), (200, 'application/json; charset=utf-8'))
        self.assertTrue(json.loads(body)['ok'] and 'public' in cache and 's-maxage' in cache)
        st, ctype, body, cache = hotapi.handle({'related': ['姆巴佩'], 'lang': ['zh']}, {})
        self.assertEqual(st, 200)
        self.assertEqual(len(json.loads(body)['items']), 2)

    def test_an_address_without_a_page_of_its_own_is_a_404_and_a_platform_that_fails_a_502_neither_is_cached(self):
        self.use({'https://news-at.zhihu.com/': agg.Refused('找不到这个网站', 'no host')})
        st, _, body, cache = hotapi.handle({'detail': ['https://evil.example/x']}, {})
        self.assertEqual((st, json.loads(body)['error'], cache), (404, 'none', 'no-store'))
        st, _, body, cache = hotapi.handle({'detail': ['https://daily.zhihu.com/story/9793540']}, {})
        self.assertEqual((st, json.loads(body)['error'], cache), (502, 'upstream', 'no-store'))
        self.assertIn('找不到', json.loads(body)['message'])

    def test_the_owner_can_turn_either_off(self):
        g = self.use({'': {}})
        for q, env in (({'detail': ['https://www.v2ex.com/t/1252074']}, {'HOT_DETAIL': '0'}), ({'related': ['some words']}, {'HOT_RELATED': '0'})):
            st, _, body, _ = hotapi.handle(q, env)
            self.assertEqual((st, json.loads(body)['error']), (404, 'off'))
        self.assertEqual(g.calls, [])

    def test_diag_says_why_a_platform_cannot_be_reached_from_here(self):
        with mock.patch('socket.getaddrinfo', side_effect=OSError('Name or service not known')):
            st, _, body, cache = hotapi.handle({'diag': ['zhihu_daily']}, {'VERCEL_REGION': 'hkg1'})
        d = json.loads(body)
        self.assertEqual((st, d['host'], d['region'], cache), (200, 'news-at.zhihu.com', 'hkg1', 'no-store'))
        self.assertTrue(d['dns'].startswith('failed') and 'connect' not in d)
        with mock.patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('203.0.113.7', 443))]), mock.patch('socket.create_connection', side_effect=OSError('timed out')):
            d = json.loads(hotapi.handle({'diag': ['sspai']}, {})[2])
        self.assertEqual((d['dns'], d['ips']), ('ok', ['203.0.113.7']))
        self.assertTrue(d['connect'].startswith('failed'))
        self.assertEqual(hotapi.handle({'diag': ['no-such-list']}, {})[0], 404)

    def test_the_index_says_which_platforms_have_pages_to_show_and_which_rows_are_a_phrase(self):
        idx = json.loads(hotapi.handle({}, {})[2])
        by = {p['id']: p for p in idx['platforms']}
        self.assertEqual({k for k, p in by.items() if p.get('detail')}, {'github', 'v2ex', 'zhihu_daily', 'apple_pod_cn', 'apple_pod_us'})
        self.assertEqual({k for k, p in by.items() if p.get('kw')}, {'weibo', 'zhihu', 'douyin', 'xiaohongshu', 'toutiao', 'gtrends'})


class TestRows(unittest.TestCase):
    def test_a_row_may_carry_a_picture_and_a_text_and_nothing_else_is_added(self):
        r = hotapi._norm({'title': 'T', 'url': 'https://a.example/x', 'img': 'http://img.example/a.jpg', 'body': ' <b>a</b>\n\n text ', 'evil': 1})
        self.assertEqual((r['img'], r['body']), ('https://img.example/a.jpg', 'a text'))
        self.assertNotIn('evil', r)
        for bad in ('javascript:alert(1)', 'data:image/png;base64,AAAA', 'ftp://x.example/a.png', 'https://a b.example/x.png', '//x.example/a.png '[:-1] + '"'):
            self.assertNotIn('img', hotapi._norm({'title': 'T', 'url': 'https://a.example/x', 'img': bad}) or {}, bad)
        self.assertEqual(hotapi._norm({'title': 'T', 'url': 'https://a.example/x', 'img': '//x.example/a.png'})['img'], 'https://x.example/a.png')

    def test_a_row_without_either_is_what_it_was(self):
        self.assertEqual(set(hotapi._norm({'title': 'T', 'url': 'https://a.example/x'})), {'title', 'url', 'heat', 'note', 'alt', 'date'})

    def test_the_feed_carries_the_picture_and_the_text(self):
        rows = [hotapi._norm({'title': 'T', 'url': 'https://a.example/x', 'img': 'https://i.example/a.jpg?a=1&b=2', 'body': 'about it', 'heat': '热度 1'})]
        xml = hotapi.rss('x', hotapi.PLATFORMS['weibo'], rows, 0)
        import xml.etree.ElementTree as ET
        item = ET.fromstring(xml).find('channel').find('item')
        self.assertEqual(item.find('description').text, '第 1 名 · 热度 1 — about it')
        thumb = item.find('{http://search.yahoo.com/mrss/}thumbnail')
        self.assertEqual(thumb.get('url'), 'https://i.example/a.jpg?a=1&b=2')


if __name__ == '__main__':
    unittest.main()
