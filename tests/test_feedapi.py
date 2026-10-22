"""The update service for readers' own subscriptions: what an address is, what is new in a feed, the limits, and the HTTP side. Fake sites; no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import http.client
import json
import os
import socketserver
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import feedapi as fa  # noqa: E402
from test_aggregate import ATOM, JSON_FEED, RSS, Base  # noqa: E402

PAGE = '<!doctype html><html lang="en"><head><title>A single article</title><meta name="description" content="What it is about, in a sentence."></head><body><h1>A single article</h1></body></html>'
HOME = '<!doctype html><html><head><title>Home</title><link rel="alternate" type="application/rss+xml" href="/blog.xml"></head><body>hi</body></html>'


class FeedBase(Base):
    def setUp(self):
        super().setUp()
        fa._USED.clear()
        self.env = {'IP_SALT': 's', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}
        self.u = self.site.url

    def call(self, payload, ip='1.2.3.4', now=1_000_000):
        return fa.handle(payload, ip, self.env, now=now)

    def discover(self, url, **kw):
        return self.call({'action': 'discover', 'url': url}, **kw)

    def update(self, feeds, **kw):
        return self.call({'action': 'update', 'feeds': feeds}, **kw)

    def big_feed(self, n):
        items = [{'id': str(i), 'url': 'https://a.example/p/%d' % i, 'title': 'Post %d' % i, 'content_text': 'text %d' % i, 'date_published': '2026-09-%02dT08:00:00Z' % (i % 28 + 1)} for i in range(n)]
        return json.dumps({'version': 'https://jsonfeed.org/version/1.1', 'title': 'Big', 'home_page_url': 'https://a.example/', 'items': items})


class TestDiscover(FeedBase):
    def test_a_feed_address_says_what_it_is_and_shows_the_latest(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json', ETag='"v1"', **{'Last-Modified': 'Mon, 19 Oct 2026 08:00:00 GMT'})
        st, body = self.discover(self.u + '/feed.json')
        self.assertEqual((st, body['ok'], body['kind']), (200, True, 'feed'))
        self.assertEqual((body['title'], body['feed'], body['found']), ('Notes of A', self.u + '/feed.json', 2))
        self.assertEqual([i['title'] for i in body['items']], ['Second', '第一篇'])                    # newest first
        self.assertEqual((body['etag'], body['modified']), ('"v1"', 'Mon, 19 Oct 2026 08:00:00 GMT'))
        self.assertEqual(set(body['items'][0]), {'url', 'title', 'summary', 'date', 'image', 'lang', 'type', 'tags'})

    def test_atom_and_rss_too_and_what_comes_back_is_text(self):
        self.site.put('/atom.xml', ATOM, 'application/atom+xml')
        self.site.put('/rss.xml', RSS, 'application/rss+xml')
        for path in ('/atom.xml', '/rss.xml'):
            st, body = self.discover(self.u + path)
            self.assertEqual((st, body['kind']), (200, 'feed'), path)
            for i in body['items']:
                self.assertNotIn('<', i['title'] + i['summary'])                                           # no markup of someone else's page reaches the reader
                self.assertTrue(i['url'].startswith('https://'))

    def test_a_home_page_leads_to_its_feed(self):
        self.site.put('/', HOME, 'text/html')
        self.site.put('/blog.xml', RSS, 'application/rss+xml')
        st, body = self.discover(self.u + '/')
        self.assertEqual((st, body['kind'], body['feed']), (200, 'feed', self.u + '/blog.xml'))

    def test_a_single_page_is_one_link_with_nothing_to_follow(self):
        self.site.put('/robots.txt', '', 'text/plain', status=404)
        self.site.put('/post/1', PAGE, 'text/html')
        st, body = self.discover(self.u + '/post/1')
        self.assertEqual((st, body['kind'], body['title'], body['summary']), (200, 'link', 'A single article', 'What it is about, in a sentence.'))
        self.assertEqual(body['url'], self.u + '/post/1')                                                  # the kind says it: a page, nothing to follow

    def test_a_github_repository_cannot_be_followed_yet(self):
        st, body = self.discover('https://github.com/o/r')
        self.assertEqual((st, body['error']), (422, 'unreachable'))
        self.assertIn('GitHub', body['message'])
        st, body = self.discover('https://github.com/someone')
        self.assertEqual(st, 422)
        self.assertEqual(self.site.hits, [])                                                               # decided from the shape of the address, nothing was requested

    def test_bad_input(self):
        self.assertEqual(self.discover('')[0], 422)
        self.assertEqual(self.discover('not a url at all')[0], 422)
        for payload in ({'action': 'discover'}, {'action': 'discover', 'url': 5}, {'action': 'discover', 'url': ['https://a.example/']}, {'action': 'other'}, {}, [], None, 'x'):
            self.assertEqual(self.call(payload)[0], 400, payload)

    def test_a_dead_page_says_why(self):
        st, body = self.discover(self.u + '/nothing-here')
        self.assertEqual((st, body['error'], body['code']), (422, 'unreachable', 404))

    def test_not_a_feed_at_a_feed_address(self):
        self.site.put('/feed.xml', '<html>nope</html>', 'text/html')
        st, body = self.discover(self.u + '/feed.xml')
        self.assertIn(st, (200, 422))
        self.assertNotEqual(body.get('kind'), 'feed')


class TestUpdate(FeedBase):
    def test_what_is_new_in_several_feeds_in_the_order_asked(self):
        self.site.put('/a.json', JSON_FEED, 'application/json', ETag='"a1"')
        self.site.put('/b.xml', RSS, 'application/rss+xml')
        st, body = self.update([{'url': self.u + '/b.xml'}, {'url': self.u + '/a.json'}])
        self.assertEqual(st, 200)
        self.assertEqual([r['url'] for r in body['results']], [self.u + '/b.xml', self.u + '/a.json'])
        a = body['results'][1]
        self.assertEqual((a['status'], a['title'], a['etag']), ('ok', 'Notes of A', '"a1"'))
        self.assertEqual([i['title'] for i in a['items']], ['Second', '第一篇'])

    def test_one_bad_feed_does_not_spoil_the_others(self):
        self.site.put('/good.json', JSON_FEED, 'application/json')
        self.site.put('/html.xml', '<html>this is not a feed</html>', 'text/html')
        st, body = self.update([{'url': self.u + '/gone.xml'}, {'url': self.u + '/good.json'}, {'url': self.u + '/html.xml'}])
        gone, good, html = body['results']
        self.assertEqual((st, gone['status'], gone['code'], good['status'], html['status']), (200, 'error', 404, 'ok', 'error'))
        self.assertTrue(gone['message'] and gone['message_en'])

    def test_not_modified_is_asked_with_the_validators_and_costs_nothing(self):
        self.site.put('/feed.json', '', 'application/json', status=304)
        st, body = self.update([{'url': self.u + '/feed.json', 'etag': '"v1"', 'modified': 'Mon, 19 Oct 2026 08:00:00 GMT'}])
        self.assertEqual((st, body['results'][0]['status']), (200, 'unchanged'))
        sent = self.site.hits[-1][1]
        self.assertEqual((sent.get('If-None-Match'), sent.get('If-Modified-Since')), ('"v1"', 'Mon, 19 Oct 2026 08:00:00 GMT'))

    def test_without_validators_the_feed_is_read_in_full(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        self.update([{'url': self.u + '/feed.json'}])
        sent = self.site.hits[-1][1]
        self.assertNotIn('If-None-Match', sent)
        self.assertNotIn('If-Modified-Since', sent)

    def test_only_the_newest_items_come_back(self):
        self.site.put('/big.json', self.big_feed(80), 'application/json')
        _, body = self.update([{'url': self.u + '/big.json'}])
        items = body['results'][0]['items']
        self.assertEqual(len(items), agg.FEED_KEEP)
        self.assertEqual([i['date'] for i in items], sorted([i['date'] for i in items], reverse=True))

    def test_the_request_is_checked_before_anything_is_read(self):
        too_many = [{'url': self.u + '/f%d.xml' % i} for i in range(fa.MAX_FEEDS + 1)]
        for feeds in (too_many, [], 'x', None, {'url': 'https://a.example/'}, [5], [{'url': ''}], [{'nourl': 1}], [{'url': 'x' * 1600}], [{'url': 'https://a.example/'}, 'https://b.example/']):
            self.assertEqual(self.update(feeds)[0], 400, str(feeds)[:60])
        self.assertEqual(self.site.hits, [])

    def test_the_same_feed_twice_is_read_once(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        _, body = self.update([{'url': self.u + '/feed.json'}, {'url': self.u + '/feed.json', 'etag': 'x'}])
        self.assertEqual(len(body['results']), 1)
        self.assertEqual(len([h for h in self.site.hits if h[0] == '/feed.json']), 1)

    def test_what_is_kept_of_the_validators_is_bounded(self):
        self.site.put('/feed.json', '', 'application/json', status=304)
        self.update([{'url': self.u + '/feed.json', 'etag': 'e' * 5000, 'modified': 'm' * 5000}])
        sent = self.site.hits[-1][1]
        self.assertLessEqual(len(sent.get('If-None-Match', '')), 300)
        self.assertLessEqual(len(sent.get('If-Modified-Since', '')), 100)

    def test_the_time_limit_is_put_back_afterwards(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        before = (agg.TIMEOUT, agg.DEADLINE)
        self.update([{'url': self.u + '/feed.json'}])
        self.discover(self.u + '/feed.json')
        self.discover(self.u + '/missing')
        self.assertEqual((agg.TIMEOUT, agg.DEADLINE), before)


LONG = '<p>' + 'This sentence is about supply chains and how lead time moves with the order. ' * 12 + '</p>'          # > 500 characters of text
ZH_LONG = '<p>' + '这是一段关于库存周转率的正文，讲的是周转天数怎么算，以及为什么它比库存金额更能说明问题。' * 12 + '</p>'
HOSTILE = ('<p onclick="x()">Intro ' + 'word ' * 120 + '</p><script>alert(1)</script><img src="https://cdn.example/a.png" onerror="y()" alt="A"><a href="javascript:alert(2)">bad</a>'
           '<iframe src="https://evil.example/"></iframe><a href="/rel">rel</a>')


def json_feed(items):
    return json.dumps({'version': 'https://jsonfeed.org/version/1.1', 'title': 'Texts', 'home_page_url': 'https://a.example/', 'items': items})


def jitem(i, html=None, text=None, day=None, summary=None):
    d = {'id': str(i), 'url': 'https://a.example/p/%d' % i, 'title': 'Post %d' % i, 'date_published': '2026-09-%02dT08:00:00Z' % (day or i)}
    if html is not None:
        d['content_html'] = html
    if text is not None:
        d['content_text'] = text
    if summary is not None:
        d['summary'] = summary
    return d


class TestArticleText(FeedBase):
    """With "content": true the text of the newest articles comes along when the feed itself carries it: rebuilt as safe html, bounded, and never for an excerpt."""

    def read(self, path, **spec):
        st, body = self.update([{'url': self.u + path, **spec}])
        self.assertEqual(st, 200)
        return body['results'][0]

    def test_it_is_off_unless_asked_for(self):
        self.site.put('/t.json', json_feed([jitem(1, LONG)]), 'application/json')
        r = self.read('/t.json')
        self.assertNotIn('content', r['items'][0])
        self.assertNotIn('content_mode', r)
        r = self.read('/t.json', content='true')                                            # only a real true counts
        self.assertNotIn('content', r['items'][0])
        self.assertNotIn('content_mode', r)

    def test_json_feed_text_comes_along(self):
        self.site.put('/t.json', json_feed([jitem(1, LONG), jitem(2, '<p>Just an excerpt.</p>', day=2)]), 'application/json')
        r = self.read('/t.json', content=True)
        self.assertTrue(r['content_mode'])
        by = {i['title']: i for i in r['items']}
        self.assertIn('lead time', by['Post 1']['content'])
        self.assertNotIn('content', by['Post 2'])                                           # an excerpt is not the article
        self.assertNotIn('<', by['Post 1']['summary'])                                      # the summary is still plain text

    def test_plain_text_content_becomes_paragraphs(self):
        text = '\n\n'.join(['Paragraph %d of the article. ' % n + 'More words here. ' * 20 for n in range(4)])
        self.site.put('/t.json', json_feed([jitem(1, text=text)]), 'application/json')
        c = self.read('/t.json', content=True)['items'][0]['content']
        self.assertEqual(c.count('<p>'), 4)

    def test_atom_html_xhtml_and_text(self):
        esc_long = LONG.replace('<', '&lt;').replace('>', '&gt;')
        atom = ('<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><link href="https://a.example/"/>'
                '<entry><title>H</title><link href="https://a.example/h"/><updated>2026-09-03T08:00:00Z</updated><content type="html">%s</content></entry>'
                '<entry><title>X</title><link href="https://a.example/x"/><updated>2026-09-02T08:00:00Z</updated><content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">%s</div></content></entry>'
                '<entry><title>T</title><link href="https://a.example/t"/><updated>2026-09-01T08:00:00Z</updated><content type="text">%s</content></entry>'
                '<entry><title>S</title><link href="https://a.example/s"/><updated>2026-08-30T08:00:00Z</updated><summary>only a summary</summary></entry></feed>'
                % (esc_long, LONG, 'Plain text words go here. ' * 30))
        self.site.put('/a.xml', atom, 'application/atom+xml')
        r = self.read('/a.xml', content=True)
        by = {i['title']: i for i in r['items']}
        for name in ('H', 'X', 'T'):
            self.assertIn('content', by[name], name)
            self.assertGreater(len(by[name]['content']), 500, name)
        self.assertIn('<p>', by['H']['content'])
        self.assertIn('lead time', by['X']['content'])
        self.assertNotIn('content', by['S'])

    def test_rss_content_encoded_but_not_a_short_description(self):
        rss = ('<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>R</title><link>https://a.example/</link>'
               '<item><title>Full</title><link>https://a.example/full</link><pubDate>Thu, 03 Sep 2026 08:00:00 GMT</pubDate><description>An excerpt of it.</description>'
               '<content:encoded><![CDATA[%s]]></content:encoded></item>'
               '<item><title>Excerpt</title><link>https://a.example/ex</link><pubDate>Wed, 02 Sep 2026 08:00:00 GMT</pubDate><description>%s</description></item>'
               '<item><title>Whole in description</title><link>https://a.example/wd</link><pubDate>Tue, 01 Sep 2026 08:00:00 GMT</pubDate><description><![CDATA[%s]]></description></item>'
               '</channel></rss>' % (LONG, 'A two-line excerpt of the post. ' * 3, ZH_LONG))
        self.site.put('/r.xml', rss, 'application/rss+xml')
        by = {i['title']: i for i in self.read('/r.xml', content=True)['items']}
        self.assertIn('lead time', by['Full']['content'])
        self.assertNotIn('content', by['Excerpt'])
        self.assertIn('库存周转率', by['Whole in description']['content'])

    def test_what_arrives_is_the_safe_subset_with_absolute_addresses(self):
        self.site.put('/t.json', json_feed([jitem(1, HOSTILE)]), 'application/json')
        c = self.read('/t.json', content=True)['items'][0]['content']
        for bad in ('<script', 'alert', 'onclick', 'onerror', 'javascript:', '<iframe', 'evil.example'):
            self.assertNotIn(bad, c)
        self.assertIn('<img src="https://cdn.example/a.png" alt="A"', c)
        self.assertIn('href="https://a.example/rel"', c)                                    # resolved against the article's own address

    def test_a_long_article_is_cut_and_says_so(self):
        big = ''.join('<p>%s</p>' % ('long text of the article goes on ' * 20) for _ in range(300))
        self.site.put('/t.json', json_feed([jitem(1, big)]), 'application/json')
        it = self.read('/t.json', content=True)['items'][0]
        self.assertTrue(it['content_cut'])
        self.assertLessEqual(len(it['content']), fa.CONTENT_ITEM_CHARS + 200)
        self.assertEqual(it['content'].count('<p>'), it['content'].count('</p>'))

    def test_only_the_newest_items_carry_text(self):
        self.site.put('/t.json', json_feed([jitem(i, LONG, day=i) for i in range(1, 21)]), 'application/json')
        items = self.read('/t.json', content=True)['items']
        with_text = [i['title'] for i in items if 'content' in i]
        self.assertEqual(len(with_text), fa.CONTENT_ITEMS)
        self.assertEqual(set(with_text), {'Post %d' % n for n in range(20, 20 - fa.CONTENT_ITEMS, -1)})
        self.assertEqual(len(items), 20)

    def test_one_answer_carries_only_so_much_text(self):
        old = fa.CONTENT_TOTAL
        fa.CONTENT_TOTAL = 6000
        try:
            for n in range(3):
                self.site.put('/f%d.json' % n, json_feed([jitem(i, LONG, day=i) for i in range(1, 5)]), 'application/json')
            st, body = self.update([{'url': self.u + '/f%d.json' % n, 'content': True} for n in range(3)])
            total = sum(len(i['content']) for r in body['results'] for i in r['items'] if 'content' in i)
            self.assertLessEqual(total, 6000)
            self.assertGreater(total, 0)
            self.assertTrue(any('content' not in i for r in body['results'] for i in r['items']))     # the later ones lost theirs
        finally:
            fa.CONTENT_TOTAL = old

    def test_asking_for_text_does_not_leak_into_the_next_request(self):
        self.site.put('/t.json', json_feed([jitem(1, LONG)]), 'application/json')
        self.read('/t.json', content=True)
        self.assertFalse(getattr(agg._OPT, 'content', False))
        self.assertNotIn('content', self.read('/t.json')['items'][0])
        st, body = self.discover(self.u + '/t.json')
        self.assertNotIn('content', body['items'][0])
        res = agg.read_feed({'source': self.u + '/t.json'})                                 # the directory's own reading keeps no text of anybody
        self.assertTrue(all('_content' not in i for i in res['items']))

    def test_unchanged_feeds_stay_cheap(self):
        self.site.put('/t.json', '', 'application/json', status=304)
        r = self.read('/t.json', content=True, etag='"v1"')
        self.assertEqual(r['status'], 'unchanged')

    def test_chinese_text_counts_as_text(self):
        self.site.put('/t.json', json_feed([jitem(1, ZH_LONG)]), 'application/json')
        it = self.read('/t.json', content=True)['items'][0]
        self.assertIn('库存周转率', it['content'])

class TestSafety(FeedBase):
    def setUp(self):
        super().setUp()
        agg.ALLOW_LOCAL = False                                          # the real rules: https, public addresses only (Base turns them off to read the fake sites)

    def test_addresses_of_this_machine_and_private_networks_are_never_requested(self):
        bad = ['https://127.0.0.1/feed.xml', 'https://localhost/feed.xml', 'https://10.0.0.5/feed.xml', 'https://192.168.1.1/rss', 'https://[::1]/feed',
               'https://169.254.169.254/latest/meta-data/', 'https://user:pass@example.org/feed.xml', 'https://example.org:8443/feed.xml', 'file:///etc/passwd', 'ftp://example.org/feed']
        st, body = self.update([{'url': u} for u in bad])
        self.assertEqual(st, 200)
        self.assertTrue(all(r['status'] == 'error' for r in body['results']), body)
        for u in bad:
            self.assertEqual(self.discover(u)[0], 422, u)
        self.assertEqual(self.site.hits, [])

    def test_the_secure_address_is_what_is_read_for_a_plain_http_one(self):
        self.assertEqual(agg.normalize_input('http://example.org/feed.xml'), 'https://example.org/feed.xml')


class TestLimits(FeedBase):
    def test_a_sender_can_ask_only_so_often(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        for _ in range(fa.LIMITS['discover'][0]):
            self.assertEqual(self.discover(self.u + '/feed.json')[0], 200)
        st, body = self.discover(self.u + '/feed.json')
        self.assertEqual((st, body['error']), (429, 'rate'))
        self.assertEqual(self.discover(self.u + '/feed.json', ip='9.9.9.9')[0], 200)                       # somebody else is not held back
        self.assertEqual(self.update([{'url': self.u + '/feed.json'}])[0], 200)                            # updating has a count of its own
        self.assertEqual(self.discover(self.u + '/feed.json', now=1_000_000 + 601)[0], 200)               # and the window passes

    def test_a_rejected_request_does_not_use_up_the_count(self):
        for _ in range(fa.LIMITS['update'][0] + 5):
            self.assertEqual(self.update([])[0], 400)
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        self.assertEqual(self.update([{'url': self.u + '/feed.json'}])[0], 200)


class TestConfig(FeedBase):
    """The page finds the service from directory.json: `submit_api` with /submit -> /feed, or `feed_api` when the config says it."""
    def built(self, **cfg):
        with open(os.path.join(self.tmp, 'registry', 'config.json'), 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
        return json.loads(agg.build()['directory.json'])

    def test_feed_api_is_in_directory_json_only_when_configured(self):
        self.assertNotIn('feed_api', self.built(submit_api='https://dir.example/api/submit'))
        d = self.built(submit_api='https://dir.example/api/submit', feed_api=' https://other.example/api/feed ')
        self.assertEqual((d['submit_api'], d['feed_api']), ('https://dir.example/api/submit', 'https://other.example/api/feed'))
        self.assertNotIn('feed_api', self.built(feed_api='   '))


class Srv(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = '127.0.0.1', self.server_address[1]


class TestHttp(FeedBase):
    def serve(self):
        env = self.env

        class H(BaseHTTPRequestHandler):
            def do_OPTIONS(self): fa.respond(self, env, 'OPTIONS')
            def do_GET(self): fa.respond(self, env, 'GET')
            def do_POST(self): fa.respond(self, env, 'POST')
            def log_message(self, *a): pass
        srv = Srv(('127.0.0.1', 0), H)
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv.server_address[1]

    def request(self, port, method, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
        c.request(method, '/api/feed', body=json.dumps(body) if isinstance(body, (dict, list)) else body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        out = (r.status, dict(r.getheaders()), json.loads(data) if data else None)
        c.close()
        return out

    def test_cors_and_the_origin_rule(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        port = self.serve()
        ask = {'action': 'discover', 'url': self.u + '/feed.json'}
        ok = {'Origin': 'https://books.zhitree.cn', 'Content-Type': 'application/json'}
        st, h, _ = self.request(port, 'OPTIONS', headers=ok)
        self.assertEqual((st, h.get('Access-Control-Allow-Origin'), 'POST' in h.get('Access-Control-Allow-Methods', '')), (204, 'https://books.zhitree.cn', True))
        st, h, body = self.request(port, 'POST', ask, ok)
        self.assertEqual((st, h.get('Access-Control-Allow-Origin'), body['kind']), (200, 'https://books.zhitree.cn', 'feed'))
        st, h, body = self.request(port, 'POST', ask, {'Origin': 'https://evil.example', 'Content-Type': 'application/json'})
        self.assertEqual((st, body['error'], 'Access-Control-Allow-Origin' in h), (403, 'origin', False))
        self.assertEqual(self.request(port, 'OPTIONS', headers={'Origin': 'https://evil.example'})[1].get('Access-Control-Allow-Origin'), None)
        self.assertEqual(len([x for x in self.site.hits if x[0] == '/feed.json']), 1)                      # only the allowed page made the service read anything

    def test_nothing_is_stored_and_nothing_secret_is_asked_for(self):
        port = self.serve()
        self.env.pop('SHELF_REPO', None)
        self.assertEqual(self.request(port, 'GET'), (200, self.request(port, 'GET')[1], {'ok': True, 'service': 'shelf-feed'}))
        self.assertEqual(self.request(port, 'GET')[1].get('Cache-Control'), 'no-store')

    def test_size_and_json(self):
        port = self.serve()
        try:
            st, _, body = self.request(port, 'POST', '{"x": "' + 'a' * (fa.BODY_LIMIT + 10) + '"}', {'Content-Type': 'application/json'})
            self.assertEqual((st, body['error']), (413, 'size'))
        except (ConnectionError, OSError):
            pass
        self.assertEqual(self.request(port, 'POST', 'not json', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request(port, 'POST', '', {'Content-Type': 'application/json'})[0], 400)

    def test_sender_address_comes_from_the_proxy_header(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        port = self.serve()
        ask = {'action': 'discover', 'url': self.u + '/feed.json'}
        for _ in range(fa.LIMITS['discover'][0]):
            self.request(port, 'POST', ask, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.9, 10.0.0.1'})
        self.assertEqual(self.request(port, 'POST', ask, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.9'})[0], 429)
        self.assertEqual(self.request(port, 'POST', ask, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.10'})[0], 200)

    def test_local_origins_only_when_asked_for(self):
        self.site.put('/feed.json', JSON_FEED, 'application/json')
        port = self.serve()
        ask = {'action': 'discover', 'url': self.u + '/feed.json'}
        local = {'Origin': 'http://127.0.0.1:8801', 'Content-Type': 'application/json'}
        self.assertEqual(self.request(port, 'POST', ask, local)[0], 403)
        self.env['ALLOW_LOCAL_ORIGIN'] = '1'
        self.assertEqual(self.request(port, 'POST', ask, local)[0], 200)


if __name__ == '__main__':
    unittest.main()
