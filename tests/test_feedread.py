"""The "read" action of the update service: the article at an address, for reading in the page; and the pages that are not articles. Fake sites; no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import feedapi as fa  # noqa: E402
from test_feedapi import FeedBase  # noqa: E402

EN = 'Lead time is the gap between the moment an order is placed and the moment the goods are on the shelf, and it moves with the supplier calendar. '
ZH = '库存周转率等于销货成本除以平均库存，周转天数则是它的倒数乘以三百六十五，数字越小说明货卖得越快，占用的资金也越少。'


def article(title='Lead time in one picture', body=None, extra=''):
    body = body if body is not None else ''.join('<p>%s %d</p>' % (EN, i) for i in range(6))
    return ('<!doctype html><html lang="en"><head><title>%s | My Blog</title><meta property="og:site_name" content="My Blog"><meta name="author" content="Ann">'
            '<meta property="article:published_time" content="2026-10-14T08:00:00Z"></head><body><nav><a href="/">Home</a><a href="/about">About</a></nav>'
            '<article><h1>%s</h1><div class="entry-content">%s%s</div></article><aside class="sidebar"><ul><li><a href="/a">A</a></li></ul></aside><footer>© 2026</footer></body></html>'
            % (title, title, body, extra))


class ReadBase(FeedBase):
    def setUp(self):
        super().setUp()
        self.site.put('/robots.txt', '', 'text/plain', status=404)

    def read(self, path, **kw):
        return self.call({'action': 'read', 'url': self.u + path}, **kw)


class TestRead(ReadBase):
    def test_an_article_comes_back_as_safe_text_with_what_the_page_says_about_itself(self):
        self.site.put('/p/1', article(), 'text/html; charset=utf-8')
        st, body = self.read('/p/1')
        self.assertEqual((st, body['ok'], body['kind']), (200, True, 'article'))
        self.assertEqual((body['title'], body['site'], body['author'], body['date'], body['url']), ('Lead time in one picture', 'My Blog', 'Ann', '2026-10-14', self.u + '/p/1'))
        self.assertIn('Lead time is the gap', body['content'])
        for out in ('Home', 'About', '© 2026'):
            self.assertNotIn(out, body['content'])
        self.assertFalse(body['cut'])

    def test_the_page_is_asked_for_honestly_and_without_a_cookie(self):
        self.site.put('/p/1', article(), 'text/html')
        self.read('/p/1')
        sent = [h for p, h in self.site.hits if p == '/p/1'][0]
        self.assertTrue(sent['User-Agent'].startswith('ZhiTreeBooks-Reader/1'))
        self.assertNotIn('Cookie', sent)
        self.assertNotIn('Authorization', sent)
        self.assertEqual(len([1 for p, _ in self.site.hits if p == '/p/1']), 1)                    # once

    def test_chinese_pages_in_gb18030_are_read(self):
        html = article('库存周转率怎么看', ''.join('<p>%s</p>' % ZH for _ in range(6))).replace('<head>', '<head><meta charset="gb18030">')
        self.site.put('/zh', html.encode('gb18030'), 'text/html')
        st, body = self.read('/zh')
        self.assertEqual(st, 200)
        self.assertIn('周转天数则是它的倒数', body['content'])
        self.assertEqual(body['title'], '库存周转率怎么看')

    def test_a_hostile_page_is_made_safe(self):
        self.site.put('/p/2', article(extra='<script>alert(1)</script><p onclick="x()">x <a href="javascript:alert(2)">y</a></p><iframe src="https://evil.example/"></iframe><img src=x onerror="alert(3)">'), 'text/html')
        st, body = self.read('/p/2')
        for bad in ('<script', 'alert', 'onclick', 'javascript:', '<iframe', 'onerror', 'evil.example'):
            self.assertNotIn(bad, body['content'])

    def test_a_very_long_article_is_cut_and_says_so(self):
        self.site.put('/long', article(body=''.join('<p>%s</p>' % (EN * 3) for _ in range(900))), 'text/html')
        st, body = self.read('/long')
        self.assertTrue(body['cut'])
        self.assertLessEqual(len(body['content']), fa.READ_CHARS + 300)

    def test_a_redirect_is_followed_and_the_final_address_is_given(self):
        self.site.put('/short', '', 'text/html', status=302, Location='/p/final')
        self.site.put('/p/final', article(), 'text/html')
        st, body = self.read('/short')
        self.assertEqual((st, body['url']), (200, self.u + '/p/final'))


class TestNotReadable(ReadBase):
    def reason(self, path, **kw):
        st, body = self.read(path, **kw)
        self.assertEqual((st, body['ok'], body['error']), (422, False, 'unreadable'), body)
        self.assertTrue(body['message'] and body['message_en'])
        return body['reason']

    def test_a_login_wall_says_login(self):
        self.site.put('/wall', '<html><head><title>登录</title></head><body><div class="login-box"><h2>请先登录</h2><p>登录后查看全文。</p></div></body></html>', 'text/html')
        self.assertEqual(self.reason('/wall'), 'login')

    def test_a_redirect_to_a_login_page(self):
        self.site.put('/secret', '', 'text/html', status=302, Location='/login?next=/secret')
        self.site.put('/login?next=/secret', '<html><body><form><input name="user"></form><p>Welcome back.</p></body></html>', 'text/html')
        self.assertEqual(self.reason('/secret'), 'login')

    def test_the_answers_of_the_site_become_reasons(self):
        for status, reason in ((401, 'login'), (402, 'login'), (403, 'blocked'), (429, 'blocked'), (451, 'blocked'), (404, 'gone'), (410, 'gone'), (500, 'unreachable'), (502, 'unreachable')):
            self.site.put('/s%d' % status, 'x', 'text/html', status=status)
            self.assertEqual(self.reason('/s%d' % status), reason, status)

    def test_a_bot_check_a_paywall_and_a_page_of_scripts(self):
        self.site.put('/cf', '<html><head><title>Just a moment...</title></head><body><h1>Just a moment...</h1><p>Checking your browser before accessing the site.</p></body></html>', 'text/html')
        self.assertEqual(self.reason('/cf'), 'blocked')
        self.site.put('/pay', article(body='<p>%s</p><p>Subscribe to continue reading this article.</p>' % (EN * 3)), 'text/html')
        self.assertEqual(self.reason('/pay'), 'paywall')
        self.site.put('/spa', '<html><body><div id="root"></div><script src="/a.js"></script><script src="/b.js"></script><script src="/c.js"></script></body></html>', 'text/html')
        self.assertEqual(self.reason('/spa'), 'needs_js')

    def test_a_pdf_and_other_files(self):
        self.site.put('/doc.pdf', b'%PDF-1.4 x', 'application/pdf')
        self.assertEqual(self.reason('/doc.pdf'), 'pdf')
        self.site.put('/get', b'%PDF-1.4 x', 'application/pdf')                                      # a PDF behind an address that does not end in .pdf
        self.assertEqual(self.reason('/get'), 'pdf')
        self.site.put('/data', '{"a": 1}', 'application/json')
        self.assertEqual(self.reason('/data'), 'unsupported')
        self.site.put('/pic', b'\x89PNG', 'image/png')
        self.assertEqual(self.reason('/pic'), 'unsupported')

    def test_addresses_that_are_not_pages_to_read(self):
        for url in ('https://github.com/o/r', 'https://www.youtube.com/watch?v=abcdefghijk', 'https://gist.github.com/someone/0123456789abcdef0123456789abcdef'):
            st, body = self.call({'action': 'read', 'url': url})
            self.assertEqual((st, body['reason']), (422, 'unsupported'), url)
        self.assertEqual(self.site.hits, [])

    def test_a_page_with_nothing_to_read(self):
        self.site.put('/empty', '<html><body><nav><a href="/a">A</a></nav><p>Hello.</p></body></html>', 'text/html')
        self.assertEqual(self.reason('/empty'), 'empty')

    def test_robots_txt_is_respected_unless_the_owner_says_otherwise(self):
        self.site.put('/robots.txt', 'User-agent: *\nDisallow: /private/\n', 'text/plain')
        self.site.put('/private/a', article(), 'text/html')
        self.site.put('/public/a', article(), 'text/html')
        self.assertEqual(self.reason('/private/a'), 'robots')
        self.assertEqual(self.read('/public/a')[0], 200)
        self.assertEqual([p for p, _ in self.site.hits if p == '/private/a'], [])                      # not even requested
        self.env['READ_IGNORE_ROBOTS'] = '1'
        self.assertEqual(self.read('/private/a')[0], 200)


class TestReadSafetyAndLimits(ReadBase):
    def test_bad_requests(self):
        for payload in ({'action': 'read'}, {'action': 'read', 'url': 5}, {'action': 'read', 'url': ['https://a.example/']}, {'action': 'read', 'url': None}):
            self.assertEqual(self.call(payload)[0], 400, payload)
        self.assertEqual(self.read_url('')[0], 422)
        self.assertEqual(self.read_url('not a url at all')[0], 422)

    def read_url(self, url):
        return self.call({'action': 'read', 'url': url})

    def test_a_sender_can_ask_only_so_often(self):
        self.site.put('/p/1', article(), 'text/html')
        for _ in range(fa.LIMITS['read'][0]):
            self.assertEqual(self.read('/p/1')[0], 200)
        st, body = self.read('/p/1')
        self.assertEqual((st, body['error']), (429, 'rate'))
        self.assertEqual(self.read('/p/1', ip='9.9.9.9')[0], 200)
        self.assertEqual(self.read('/p/1', now=1_000_000 + 601)[0], 200)

    def test_the_real_rules_keep_this_machine_and_private_networks_out(self):
        agg.ALLOW_LOCAL = False
        for url in ('https://127.0.0.1/a', 'https://localhost/a', 'https://10.0.0.5/a', 'https://192.168.1.1/a', 'https://[::1]/a', 'https://169.254.169.254/latest/meta-data/', 'file:///etc/passwd', 'https://user:pw@example.org/a'):
            st, body = self.read_url(url)
            self.assertEqual(st, 422, url)
        self.assertEqual(self.site.hits, [])

    def test_the_time_limit_is_put_back(self):
        self.site.put('/p/1', article(), 'text/html')
        before = (agg.TIMEOUT, agg.DEADLINE)
        self.read('/p/1')
        self.read('/missing')
        self.assertEqual((agg.TIMEOUT, agg.DEADLINE), before)


class TestDiscoverComesWithTheText(ReadBase):
    def test_a_single_article_is_found_with_its_text_so_it_can_be_saved_as_it_is(self):
        self.site.put('/post/1', article(), 'text/html')
        st, body = self.discover(self.u + '/post/1')
        self.assertEqual((st, body['kind'], body['readable']), (200, 'link', True))
        self.assertIn('Lead time is the gap', body['content'])
        self.assertEqual(body['title'], 'Lead time in one picture')
        self.assertFalse(body['content_cut'])

    def test_a_single_page_that_cannot_be_read_says_why_and_can_still_be_saved_as_a_link(self):
        self.site.put('/post/2', '<html><head><title>Members</title></head><body><div class="login"><p>请先登录后查看。</p></div></body></html>', 'text/html')
        st, body = self.discover(self.u + '/post/2')
        self.assertEqual((st, body['kind'], body['readable'], body['reason']), (200, 'link', False, 'login'))
        self.assertTrue(body['reason_message'] and body['reason_message_en'])
        self.assertNotIn('content', body)

    def test_a_feed_is_a_feed_as_before(self):
        self.site.put('/feed.json', json.dumps({'version': 'https://jsonfeed.org/version/1.1', 'title': 'T', 'home_page_url': 'https://a.example/', 'items': [{'id': '1', 'url': 'https://a.example/1', 'title': 'One', 'content_text': 'x'}]}), 'application/json')
        st, body = self.discover(self.u + '/feed.json')
        self.assertEqual((st, body['kind']), (200, 'feed'))
        self.assertNotIn('readable', body)


if __name__ == '__main__':
    unittest.main()
