"""Tests for aggregate.py. No internet: pages, feeds and the GitHub API are served by a fake server on 127.0.0.1.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402


class Site:
    """A fake web server: routes maps a path (with its query) to (status, headers, body)."""

    def __init__(self):
        self.routes, self.hits, self.calls, self.mroutes = {}, [], [], {}
        site = self

        class H(BaseHTTPRequestHandler):
            def _write(self, status, payload):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _other(self):                                     # POST / PATCH / DELETE: recorded, answered from site.mroutes
                raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                site.calls.append((self.command, self.path, json.loads(raw) if raw else None, dict(self.headers)))
                status, payload = site.mroutes.get((self.command, self.path), (404, {'message': 'Not Found'}))
                self._write(status, payload)

            do_POST = do_PATCH = do_DELETE = _other

            def do_GET(self):
                site.hits.append((self.path, dict(self.headers)))
                r = site.routes.get(self.path)
                if r is None:
                    r = (404, {}, b'not found')
                status, headers, body = r
                if isinstance(body, str):
                    body = body.encode('utf-8')
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        class Srv(ThreadingHTTPServer):
            def server_bind(self):                     # HTTPServer.server_bind asks the resolver for this machine's name: ~1 s on Windows
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = '127.0.0.1', self.server_address[1]

        self.srv = Srv(('127.0.0.1', 0), H)
        self.url = 'http://127.0.0.1:%d' % self.srv.server_address[1]
        threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.02), daemon=True).start()

    def put(self, path, body, ctype='text/html; charset=utf-8', status=200, **headers):
        self.routes[path] = (status, {'Content-Type': ctype, **headers}, body)

    def on(self, method, path, status=200, payload=None):
        self.mroutes[(method, path)] = (status, payload if payload is not None else {})

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = (agg.ROOT, agg.ALLOW_LOCAL, agg.GITHUB_API, agg.GITHUB_RAW)
        agg.ROOT, agg.ALLOW_LOCAL = self.tmp, True
        agg._ROBOTS.clear()
        os.makedirs(os.path.join(self.tmp, 'registry'))
        self.site = Site()
        agg.GITHUB_API, agg.GITHUB_RAW = self.site.url + '/api', self.site.url + '/raw'

    def tearDown(self):
        self.site.close()
        agg.ROOT, agg.ALLOW_LOCAL, agg.GITHUB_API, agg.GITHUB_RAW = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def quiet(self, fn, *a, **k):
        return fn(*a, log=lambda *x: None, **k) if fn is agg.fetch_all else fn(*a, **k)


JSON_FEED = json.dumps({'version': 'https://jsonfeed.org/version/1.1', 'title': 'Notes of A', 'home_page_url': 'https://a.example/', 'language': 'zh-CN',
                        '_shelf': {'spec': 1, 'license': 'CC BY 4.0'}, 'items': [
                            {'id': '1', 'url': 'https://a.example/p/1', 'title': '第一篇', 'content_text': '关于采购的笔记', 'date_published': '2026-10-01T08:00:00Z', 'tags': ['采购', '笔记'], '_shelf': {'type': 'note'}},
                            {'id': '2', 'url': 'https://a.example/p/2', 'title': 'Second', 'content_html': '<p>Hello <b>world</b></p><script>x()</script>', 'date_published': '2026-10-02', 'image': 'https://a.example/i.png'},
                            {'id': '3', 'title': 'no url, dropped'}]})

ATOM = '''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xml:lang="en">
  <title>Some Channel</title><link rel="alternate" href="https://video.example/c/1"/>
  <entry><id>yt:video:abc</id><yt:videoId>abc</yt:videoId><title>How it works</title>
    <link rel="alternate" href="https://video.example/watch?v=abc"/><published>2026-09-30T10:00:00+00:00</published>
    <media:group><media:title>How it works</media:title><media:thumbnail url="https://img.example/abc.jpg" width="480"/><media:description>A walk through the thing.
Second line.</media:description></media:group></entry>
  <entry><title type="html">&lt;b&gt;Bold&lt;/b&gt; title &amp;amp; more</title><link href="https://video.example/p/2"/><updated>2026-09-29T00:00:00Z</updated>
    <summary type="html">&lt;p&gt;Plain &lt;i&gt;text&lt;/i&gt;&lt;/p&gt;</summary><category term="Alpha"/><category term="beta"/></entry>
</feed>'''

RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Blog R</title><link>https://r.example/</link><description>About R</description><language>en-us</language>
<item><title>Post one</title><link>https://r.example/one</link><description>&lt;p&gt;Short &lt;a href="x"&gt;desc&lt;/a&gt;&lt;/p&gt;</description>
  <pubDate>Tue, 06 Oct 2026 10:00:00 GMT</pubDate><category>ops</category><category>Supply Chain</category><media:thumbnail url="https://r.example/t.jpg"/></item>
<item><title>Podcast ep</title><link>https://r.example/ep1</link><enclosure url="https://r.example/ep1.mp3" type="audio/mpeg" length="1"/><content:encoded><![CDATA[<p>Body only</p>]]></content:encoded></item>
<item><title>Guid link</title><guid isPermaLink="true">https://r.example/guid</guid></item>
</channel></rss>'''

PAGE = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Site | 文章标题</title>
<meta property="og:title" content="文章标题"><meta property="og:description" content="这是一篇关于库存的文章。"><meta property="og:image" content="/cover.png">
<meta property="og:site_name" content="我的博客"><meta property="article:published_time" content="2026-10-03T09:00:00+08:00">
<meta property="article:tag" content="库存"><meta property="article:tag" content="供应链"><meta name="keywords" content="库存, 计划">
<link rel="canonical" href="%(base)s/posts/inventory"><link rel="alternate" type="application/atom+xml" href="/feed.xml"></head><body><h1>x</h1></body></html>'''


class TestHelpers(unittest.TestCase):
    def test_clean_and_cut(self):
        self.assertEqual(agg.clean('  a \n\t b  '), 'a b')
        self.assertEqual(agg.clean('x' * 10, 5), 'xxxxx…')
        self.assertEqual(agg.clean(None), '')

    def test_dates(self):
        self.assertEqual(agg.parse_date('2026-10-03T09:00:00+08:00'), '2026-10-03')
        self.assertEqual(agg.parse_date('Tue, 06 Oct 2026 10:00:00 GMT'), '2026-10-06')
        self.assertEqual(agg.parse_date('2099-01-01'), agg.today().isoformat())      # not in the future
        self.assertEqual(agg.parse_date('1970-01-01'), '')
        self.assertEqual(agg.parse_date('soon'), '')
        self.assertEqual(agg.parse_date('2026-13-45'), '')

    def test_strip_html(self):
        self.assertEqual(agg.strip_html('<p>Hello <b>world</b></p><script>alert(1)</script><style>p{}</style>x'), 'Hello world x')
        self.assertEqual(agg.strip_html('Vector&lt;int&gt; &amp; more'), 'Vector<int> & more')

    def test_tags(self):
        self.assertEqual(agg.norm_tags(['#Alpha', 'alpha', ' Beta ', '', 'x' * 31, 'C']), ['Alpha', 'Beta', 'C'])
        self.assertEqual(agg.norm_tags('solo'), ['solo'])
        self.assertEqual(len(agg.norm_tags([str(i) for i in range(30)])), agg.TAGS_PER_ITEM)

    def test_type_guess(self):
        g = agg.guess_type
        self.assertEqual(g('https://www.youtube.com/watch?v=x'), 'video')
        self.assertEqual(g('https://youtu.be/x'), 'video')
        self.assertEqual(g('https://www.bilibili.com/video/BV1xx'), 'video')
        self.assertEqual(g('https://www.youtube.com/@someone'), 'article')
        self.assertEqual(g('https://a.example/x.pdf'), 'pdf')
        self.assertEqual(g('https://a.example/x', 'audio/mpeg'), 'audio')
        self.assertEqual(g('https://a.example/x.md'), 'note')
        self.assertEqual(g('https://a.example/x', hint='tool'), 'tool')
        self.assertEqual(g('https://a.example/x', hint='bogus'), 'article')

    def test_urls(self):
        self.assertEqual(agg.abs_url('/a#frag', 'https://x.example/b/c'), 'https://x.example/a')
        self.assertEqual(agg.abs_url('javascript:alert(1)'), '')
        self.assertEqual(agg.abs_url('mailto:a@b.c'), '')
        self.assertEqual(agg.abs_url('//cdn.example/i.png', 'https://x.example/'), 'https://cdn.example/i.png')

    def test_item_keeps_only_https_images_and_fills_title(self):
        it = agg.make_item('https://a.example/x', '', 'Only a summary', 'http://a.example/i.png')
        self.assertEqual(it['image'], '')
        self.assertEqual(it['title'], 'Only a summary')
        self.assertIsNone(agg.make_item('', 't'))

    def test_humanize(self):
        self.assertEqual(agg.humanize('posts/2024-03-05-hello_world.md'), ('hello world', '2024-03-05'))
        self.assertEqual(agg.humanize('docs/issue-312.md'), ('issue 312', ''))

    def test_front_matter(self):
        m, body = agg.front_matter('---\ntitle: "Hi: there"\ntags: [a, b]\ndate: 2026-01-02\n---\n# Body\n')
        self.assertEqual((m['title'], m['date'], agg._list_value(m['tags'])), ('Hi: there', '2026-01-02', ['a', 'b']))
        self.assertTrue(body.startswith('# Body'))
        m, _ = agg.front_matter('---\ntitle: T\ntags:\n  - x\n  - y\n---\nbody')
        self.assertEqual(agg._list_value(m['tags']), ['x', 'y'])
        m, _ = agg.front_matter('+++\ntitle = "Toml"\ntags = ["p", "q"]\n+++\nbody')
        self.assertEqual((m['title'], agg._list_value(m['tags'])), ('Toml', ['p', 'q']))
        self.assertEqual(agg.front_matter('no front matter')[0], {})

    def test_markdown(self):
        md = agg.read_markdown('# 标题\n\n[![badge](x.svg)](y)\n\n这是第一段，说明这个文档讲什么。\n仍是第一段。\n\n## 二级\n正文', 'a.md')
        self.assertEqual(md['title'], '标题')
        self.assertEqual(md['summary'], '这是第一段，说明这个文档讲什么。 仍是第一段。')
        md = agg.read_markdown('Setext title\n============\n\n```\ncode\n```\n\nReal text [link](u) and `code`.', 'x.md')
        self.assertEqual((md['title'], md['summary']), ('Setext title', 'Real text link and code.'))
        md = agg.read_markdown('just words without heading', '2025-01-02-my-post.md')
        self.assertEqual((md['title'], md['date']), ('my post', '2025-01-02'))

    def test_decode_cut_in_the_middle_of_a_character(self):
        data = '采购'.encode('utf-8')[:-1]
        self.assertEqual(agg.decode(data, {}), '采')
        self.assertEqual(agg.decode('库存'.encode('gb18030'), {'content-type': 'text/html; charset=gbk'}), '库存')


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.old = agg.ALLOW_LOCAL
        agg.ALLOW_LOCAL = False

    def tearDown(self):
        agg.ALLOW_LOCAL = self.old

    def test_refused_addresses(self):
        for u in ('http://example.com/', 'ftp://example.com/', 'https://user:pw@example.com/', 'https://example.com:8443/', 'https://127.0.0.1/',
                  'https://localhost/', 'https://[::1]/', 'https://192.168.1.10/', 'https://10.1.2.3/', 'https://169.254.169.254/latest/meta-data/',
                  'https://[::ffff:127.0.0.1]/', 'https://0.0.0.0/', 'file:///etc/passwd'):
            with self.assertRaises(agg.Refused, msg=u):
                agg.check_url(u)

    def test_redirect_to_a_private_address_is_refused(self):
        req = urllib.request.Request('https://example.com/')
        with self.assertRaises(agg.Refused):
            agg._Redirect().redirect_request(req, None, 302, 'Found', {}, 'https://169.254.169.254/x')

    def test_normalize(self):
        self.assertEqual(agg.normalize_input(' example.com/blog '), 'https://example.com/blog')
        self.assertEqual(agg.normalize_input('http://example.com/a#x'), 'https://example.com/a')
        with self.assertRaises(agg.Refused):
            agg.normalize_input('   ')
        with self.assertRaises(agg.Refused):
            agg.normalize_input('https://exa mple')

    def test_classify(self):
        c = agg.classify
        self.assertEqual(c('https://github.com/o/r')['kind'], 'repo')
        self.assertEqual(c('https://github.com/o/r.git')['repo'], 'r')
        d = c('https://github.com/o/r/tree/main/docs/notes')
        self.assertEqual((d['kind'], d['ref'], d['path']), ('repo', 'main', 'docs/notes'))
        d = c('https://github.com/o/r/blob/dev/a/b.md')
        self.assertEqual((d['kind'], d['ref'], d['path']), ('file', 'dev', 'a/b.md'))
        self.assertEqual(c('https://github.com/o')['kind'], 'unsupported')
        self.assertEqual(c('https://github.com/o/r/issues/3')['kind'], 'unsupported')
        self.assertEqual(c('https://gist.github.com/someone/' + 'a1' * 16)['kind'], 'gist')
        self.assertEqual(c('https://gist.github.com/rxaviers/7360908')['id'], '7360908')
        self.assertEqual(c('https://gist.github.com/rxaviers')['kind'], 'web')
        self.assertEqual(c('https://raw.githubusercontent.com/o/r/main/x.md')['kind'], 'file')
        self.assertEqual(c('https://www.youtube.com/watch?v=abc')['kind'], 'video')
        self.assertEqual(c('https://youtu.be/abc')['kind'], 'video')
        self.assertEqual(c('https://www.youtube.com/channel/UCabcdefghijklmnop')['feed'], 'https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnop')
        self.assertIn('playlist_id=PL1', c('https://www.youtube.com/playlist?list=PL1')['feed'])
        self.assertEqual((c('https://www.youtube.com/@name')['kind'], c('https://www.youtube.com/@name/videos')['home']), ('web', True))      # a channel page counts as a home page
        self.assertEqual(c('https://x.example/notes/a.md')['kind'], 'doc')
        self.assertEqual(c('https://x.example/a.pdf')['kind'], 'pdf')
        self.assertEqual(c('https://x.example/blog')['kind'], 'web')

    def test_is_home(self):
        for u, want in (('https://a.example/', True), ('https://a.example', True), ('https://a.example/blog/', True), ('https://a.example/blog', False),
                        ('https://a.example/a/b/', False), ('https://a.example/p/1', False), ('https://a.example/?p=1', False), ('https://a.example/index.html', True),
                        ('https://a.example/blog/index.html', True), ('https://a.example/a/b/index.html', False)):
            self.assertEqual(agg.is_home(u), want, u)

    def test_a_proxy_resolves_names_so_the_local_dns_is_not_asked(self):
        calls, real_gai, real_vp = [], agg.socket.getaddrinfo, agg.via_proxy
        agg.socket.getaddrinfo = lambda *a, **k: (calls.append(a), [(2, 1, 6, '', ('8.8.8.8', 443))])[1]
        try:
            agg.via_proxy = lambda h: True
            agg.check_url('https://some.example/')
            self.assertEqual(calls, [])
            for bad in ('https://127.0.0.1/', 'https://printer.local/', 'https://localhost/', 'https://x.localhost/', 'https://db.internal/'):
                with self.assertRaises(agg.Refused, msg=bad):                  # literals and names for this machine or a private network stay refused
                    agg.check_url(bad)
            agg.via_proxy = lambda h: False
            agg.check_url('https://some.example/')
            self.assertEqual(len(calls), 1)
        finally:
            agg.socket.getaddrinfo, agg.via_proxy = real_gai, real_vp

    def test_via_proxy(self):
        real_g, real_b = agg.urllib.request.getproxies, agg.urllib.request.proxy_bypass
        try:
            agg.urllib.request.getproxies = lambda: {'https': 'http://p.example:1'}
            agg.urllib.request.proxy_bypass = lambda h: h == 'skip.example'
            self.assertTrue(agg.via_proxy('a.example'))
            self.assertFalse(agg.via_proxy('skip.example'))
            agg.urllib.request.getproxies = lambda: {}
            self.assertFalse(agg.via_proxy('a.example'))
        finally:
            agg.urllib.request.getproxies, agg.urllib.request.proxy_bypass = real_g, real_b


class TestPinnedConnection(unittest.TestCase):
    """DNS rebinding: the name must not be allowed to answer "public" to the check and "private" to the connection."""

    def setUp(self):
        self.old = (agg.ALLOW_LOCAL, agg.socket.getaddrinfo, agg.socket.create_connection, agg.urllib.request.getproxies, agg.via_proxy)
        agg.ALLOW_LOCAL = False
        agg.urllib.request.getproxies = lambda: {}
        agg.via_proxy = lambda h: False
        self.seen = {'resolve': 0, 'connect': []}

    def tearDown(self):
        agg.ALLOW_LOCAL, agg.socket.getaddrinfo, agg.socket.create_connection, agg.urllib.request.getproxies, agg.via_proxy = self.old

    def answers(self, *per_call):
        def gai(host, port, *a, **k):
            i = min(self.seen['resolve'], len(per_call) - 1)
            self.seen['resolve'] += 1
            return [(2, 1, 6, '', (ip, 443)) for ip in per_call[i]]

        def cc(addr, timeout=None, *a, **k):
            self.seen['connect'].append(addr)
            raise OSError('stop here: the test needs no real server')
        agg.socket.getaddrinfo, agg.socket.create_connection = gai, cc

    def test_a_name_that_turns_private_after_the_check_is_not_connected_to(self):
        self.answers(['93.184.216.34'], ['127.0.0.1'])                         # the check sees a public address, the connection would see loopback
        with self.assertRaises(agg.Refused) as cm:
            agg.fetch('https://rebind.example/x')
        self.assertIn('内网', str(cm.exception))
        self.assertEqual(self.seen['connect'], [])

    def test_a_name_with_one_private_address_among_public_ones_is_refused(self):
        self.answers(['93.184.216.34', '10.0.0.5'])
        with self.assertRaises(agg.Refused):
            agg.fetch('https://mixed.example/x')
        self.assertEqual(self.seen['connect'], [])

    def test_the_connection_goes_to_the_checked_address_not_to_the_name(self):
        self.answers(['93.184.216.34'])
        with self.assertRaises(agg.Refused):                                    # the fake connect stops everything with an OSError -> "could not connect"
            agg.fetch('https://good.example/x')
        self.assertEqual(self.seen['connect'], [('93.184.216.34', 443)])        # an address, resolved once more only inside the connection

    def test_a_proxy_means_no_pinning(self):
        agg.urllib.request.getproxies = lambda: {'https': 'http://127.0.0.1:1'}
        handlers = []
        real = agg.urllib.request.build_opener
        agg.urllib.request.build_opener = lambda *h: (handlers.extend(h), real(*h))[1]
        try:
            self.answers(['93.184.216.34'])
            with self.assertRaises(agg.Refused):
                agg.fetch('https://good.example/x')
        finally:
            agg.urllib.request.build_opener = real
        self.assertFalse(any(isinstance(h, agg._PinnedHTTPSHandler) for h in handlers))


class TestFetch(Base):
    def test_size_limit_and_truncate(self):
        self.site.put('/big', b'x' * (agg.MAX_BYTES + 10), 'text/plain')
        with self.assertRaises(agg.Refused):
            agg.fetch(self.site.url + '/big')
        _, _, body = agg.fetch(self.site.url + '/big', max_bytes=100, truncate=True)
        self.assertEqual(len(body), 100)

    def test_redirects(self):
        self.site.put('/a', '', status=302, Location='/b')
        self.site.put('/b', 'ok', 'text/plain')
        final, _, body = agg.fetch(self.site.url + '/a')
        self.assertEqual((final, body), (self.site.url + '/b', b'ok'))
        self.site.put('/loop1', '', status=302, Location='/loop2')
        self.site.put('/loop2', '', status=302, Location='/loop1')
        with self.assertRaises(agg.Refused):
            agg.fetch(self.site.url + '/loop1')

    def test_errors_become_refusals(self):
        with self.assertRaises(agg.Refused) as cm:
            agg.fetch(self.site.url + '/nothing')
        self.assertEqual(cm.exception.code, 404)

    def test_conditional_get(self):
        self.site.put('/f', '', status=304)
        with self.assertRaises(agg.Unchanged):
            agg.fetch(self.site.url + '/f', headers={'If-None-Match': 'x'})

    def test_authorization_does_not_follow_a_redirect_to_another_host(self):
        req = urllib.request.Request(self.site.url + '/a', headers={'Authorization': 'Bearer secret'})
        other = self.site.url.replace('127.0.0.1', 'localhost') + '/b'
        new = agg._Redirect().redirect_request(req, None, 302, 'Found', {}, other)
        self.assertFalse(new.has_header('Authorization'))
        same = agg._Redirect().redirect_request(req, None, 302, 'Found', {}, self.site.url + '/c')
        self.assertTrue(same.has_header('Authorization'))


class TestFeeds(Base):
    def test_json_feed(self):
        shelf, items = agg.parse_feed(JSON_FEED.encode(), {}, 'https://a.example/feed.json')
        self.assertEqual((shelf['name'], shelf['license'], shelf['home']), ('Notes of A', 'CC BY 4.0', 'https://a.example/'))
        self.assertEqual(len(items), 2)                                   # the item without a url is dropped
        one, two = items
        self.assertEqual((one['title'], one['type'], one['date'], one['tags'], one['lang']), ('第一篇', 'note', '2026-10-01', ['采购', '笔记'], 'zh'))
        self.assertEqual(two['summary'], 'Hello world')                  # content_html is reduced to text, scripts gone
        self.assertEqual(two['image'], 'https://a.example/i.png')

    def test_the_category_a_shelf_lists_among_its_tags_is_not_a_tag(self):
        d = {'version': 'https://jsonfeed.org/version/1.1', 'title': 'S', 'items': [
            {'id': '1', 'url': 'https://s.example/1', 'title': 'A', 'content_text': 'a', 'tags': ['供应链', 'Books'], '_shelf': {'type': 'note', 'category': 'books'}},
            {'id': '2', 'url': 'https://s.example/2', 'title': 'B', 'content_text': 'b', 'tags': ['books']}]}
        _, items = agg.parse_feed(json.dumps(d).encode(), {}, 'https://s.example/feed.json')
        self.assertEqual([i['tags'] for i in items], [['供应链'], ['books']])        # without _shelf.category nothing is guessed

    def test_atom_with_media_group(self):
        shelf, items = agg.parse_feed(ATOM.encode(), {}, 'https://video.example/feed')
        self.assertEqual((shelf['name'], shelf['lang']), ('Some Channel', 'en'))
        v, b = items
        self.assertEqual((v['type'], v['image'], v['date']), ('video', 'https://img.example/abc.jpg', '2026-09-30'))
        self.assertEqual(v['summary'], 'A walk through the thing. Second line.')
        self.assertEqual(b['title'], 'Bold title & more')                  # a type="html" title is reduced to text
        self.assertEqual((b['summary'], b['tags']), ('Plain text', ['Alpha', 'beta']))

    def test_rss(self):
        shelf, items = agg.parse_feed(RSS.encode(), {}, 'https://r.example/rss')
        self.assertEqual((shelf['name'], shelf['description'], shelf['lang']), ('Blog R', 'About R', 'en-us'))
        one, pod, guid = items
        self.assertEqual((one['summary'], one['date'], one['tags'], one['image']), ('Short desc', '2026-10-06', ['ops', 'Supply Chain'], 'https://r.example/t.jpg'))
        self.assertEqual((pod['type'], pod['summary']), ('audio', 'Body only'))
        self.assertEqual(guid['url'], 'https://r.example/guid')

    def test_language_follows_the_script_not_the_declaration(self):
        rss = ('<rss version="2.0"><channel><title>博客</title><language>en-us</language>'
               '<item><title>科技爱好者周刊（第 414 期）</title><link>https://z.example/1</link></item>'
               '<item><title>An English title</title><link>https://z.example/2</link></item></channel></rss>')
        shelf, items = agg.parse_feed(rss.encode(), {}, 'https://z.example/rss')
        self.assertEqual([i['lang'] for i in items], ['zh', 'en'])
        self.assertEqual(agg.norm_lang('fr', 'Bonjour tout le monde'), 'fr')
        self.assertEqual(agg.norm_lang('zh-CN', 'Only latin words here'), 'zh')
        self.assertEqual(agg.norm_lang('', '日本語のタイトル'), 'ja')
        self.assertEqual(agg.norm_lang('', ''), 'en')

    def test_rss_1_0(self):
        rdf = '''<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <channel><title>Old</title><link>https://o.example/</link></channel>
        <item><title>Entry</title><link>https://o.example/1</link><dc:date>2026-01-05</dc:date><description>d</description></item></rdf:RDF>'''
        shelf, items = agg.parse_feed(rdf.encode(), {}, 'https://o.example/rdf')
        self.assertEqual((shelf['name'], items[0]['title'], items[0]['date']), ('Old', 'Entry', '2026-01-05'))

    def test_xml_with_entities_is_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><rss version="2.0"><channel><title>&b;</title></channel></rss>'
        with self.assertRaises(agg.Refused):
            agg.parse_feed(bomb, {}, 'https://x.example/')

    def test_not_a_feed_and_broken_xml(self):
        for body in (b'<html><body>hi</body></html>', b'<rss version="2.0"><channel><title>x</title>', b'just text', b'{"a": 1}'):
            with self.assertRaises(agg.Refused):
                agg.parse_feed(body, {}, 'https://x.example/')

    def test_feed_keeps_newest_fifty(self):
        rows = ''.join('<item><title>t%d</title><link>https://r.example/%d</link><pubDate>Tue, 06 Oct 2026 10:00:00 GMT</pubDate></item>' % (i, i) for i in range(80))
        self.site.put('/rss', '<rss version="2.0"><channel><title>Big</title>%s</channel></rss>' % rows, 'application/rss+xml')
        entry = {'kind': 'feed', 'source': self.site.url + '/rss'}
        self.assertEqual(len(agg.read_source(entry)['items']), agg.FEED_KEEP)


class TestDiscover(Base):
    def test_direct_feed(self):
        self.site.put('/feed.json', JSON_FEED, 'application/feed+json')
        r = agg.probe(self.site.url + '/feed.json')
        self.assertEqual((r['entry']['kind'], r['shelf']['name'], len(r['items'])), ('feed', 'Notes of A', 2))

    def test_home_page_leads_to_its_feed(self):
        self.site.put('/', PAGE % {'base': self.site.url})
        self.site.put('/feed.xml', RSS, 'application/rss+xml')
        r = agg.probe(self.site.url + '/')
        self.assertEqual((r['entry']['kind'], r['entry']['source']), ('feed', self.site.url + '/feed.xml'))

    def test_home_redirected_to_index_html_is_still_the_home(self):
        self.site.put('/blog/', '', status=302, Location='/blog/index.html')
        self.site.put('/blog/index.html', (PAGE % {'base': self.site.url}).replace('href="/feed.xml"', 'href="/blog/feed.xml"'))
        self.site.put('/blog/feed.xml', RSS, 'application/rss+xml')
        r = agg.probe(self.site.url + '/blog/')
        self.assertEqual((r['entry']['kind'], r['entry']['source']), ('feed', self.site.url + '/blog/feed.xml'))

    def test_home_without_link_tags_tries_common_paths(self):
        self.site.put('/', '<html><head><title>Plain</title></head><body>hi</body></html>')
        self.site.put('/index.xml', RSS, 'application/xml')
        r = agg.probe(self.site.url)
        self.assertEqual(r['entry']['source'], self.site.url + '/index.xml')

    def test_home_without_any_feed_is_one_page(self):
        self.site.put('/', '<html><head><title>Only page</title></head><body>hi</body></html>')
        r = agg.probe(self.site.url + '/')
        self.assertEqual((r['entry']['kind'], len(r['items'])), ('link', 1))
        self.assertTrue(r['warnings'])

    def test_article_page_is_one_link_with_metadata(self):
        self.site.put('/posts/inventory', PAGE % {'base': self.site.url})
        r = agg.probe(self.site.url + '/posts/inventory')
        e, it = r['entry'], r['items'][0]
        self.assertEqual((e['kind'], e['sub']), ('link', 'page'))
        self.assertEqual((it['title'], it['summary'], it['date'], it['lang']), ('文章标题', '这是一篇关于库存的文章。', '2026-10-03', 'zh'))
        self.assertEqual(it['tags'], ['库存', '供应链', '计划'])
        self.assertEqual(it['url'], self.site.url + '/posts/inventory')       # canonical, same host
        self.assertEqual(r['shelf']['name'], '我的博客')
        self.assertTrue(any('订阅源' in zh for zh, en in r['warnings']))      # tells the submitter the site has a feed

    def test_robots_txt_is_respected_for_pages_not_for_feeds(self):
        self.site.put('/robots.txt', 'User-agent: *\nDisallow: /private/\n', 'text/plain')
        self.site.put('/private/post', PAGE % {'base': self.site.url})
        r = agg.probe(self.site.url + '/private/post')                          # a page that is not read is still a link: listed unread, for the maintainer to look at
        self.assertTrue(r['unread'])
        self.assertIn('robots', r['warnings'][0][0])
        self.assertFalse(any('/private/post' in p for p, h in self.site.hits))   # and it really was not read
        self.site.put('/private/feed.xml', RSS, 'application/rss+xml')
        self.assertEqual(agg.probe(self.site.url + '/private/feed.xml')['entry']['kind'], 'feed')

    def test_page_without_title(self):
        self.site.put('/x/y', '<html><body>login please</body></html>')
        r = agg.probe(self.site.url + '/x/y')                                   # nothing to read in it (a script, a login): the link can still be listed, as it is
        self.assertTrue(r['unread'])
        self.assertEqual(r['items'][0]['title'], 'y')

    def test_a_dead_page_or_an_unsafe_address_is_not_listed_unread(self):
        with self.assertRaises(agg.Refused):                                    # 404: the link is bad
            agg.probe(self.site.url + '/nothing-here')
        agg.ALLOW_LOCAL = False
        self.addCleanup(setattr, agg, 'ALLOW_LOCAL', True)
        for url in ('https://127.0.0.1/x', 'https://localhost/x', 'https://user:pw@blog.example.org/x'):       # nothing here is asked of the network: these are refused by what they are
            with self.assertRaises(agg.Refused):
                agg.probe(url)

    def test_soft_failures_are_told_from_hard_ones(self):
        soft = [agg.Refused('网站返回了错误（HTTP %d）' % c, '', c) for c in (401, 403, 429, 500, 503)] + [agg.Refused('连不上这个网站：timed out'), agg.Refused('这个网站的 robots.txt 不允许读取这个页面'),
                agg.Refused('读不到这个页面的标题（可能需要登录）')]
        hard = [agg.Refused('网站返回了错误（HTTP %d）' % c, '', c) for c in (404, 410)] + [agg.Refused('不允许访问内网或本机地址'), agg.Refused('找不到这个网站（域名无法解析）'), agg.Refused('这个网站不在收录范围内')]
        self.assertTrue(all(agg.soft_failure(e) for e in soft))
        self.assertFalse(any(agg.soft_failure(e) for e in hard))

    def test_title_and_feed_link_after_the_body_starts(self):
        pad = '<script>var x = "%s";</script>' % ('a' * 500000)           # a big site: the tags come after a lot of script, and after <body>
        self.site.put('/', '<html><head>%s</head><body><title>Late title</title><link rel="alternate" type="application/rss+xml" href="/late.xml"></body></html>' % pad)
        self.site.put('/late.xml', RSS, 'application/rss+xml')
        r = agg.probe(self.site.url + '/')
        self.assertEqual((r['entry']['kind'], r['entry']['source']), ('feed', self.site.url + '/late.xml'))

    def test_site_name_suffix_and_site_wide_keywords(self):
        self.assertEqual(agg.strip_site_suffix('供应链系统架构的设计与实践供应链系统是现代企业管理中不可或缺的一部分 - 掘金'), '供应链系统架构的设计与实践供应链系统是现代企业管理中不可或缺的一部分')
        self.assertEqual(agg.strip_site_suffix('How inventory turnover really works | Acme Blog', 'Acme Blog'), 'How inventory turnover really works')
        self.assertEqual(agg.strip_site_suffix('Python - 入门'), 'Python - 入门')                     # short: nothing to lose
        self.assertEqual(agg.strip_site_suffix('一个很长很长很长很长的标题而且没有任何后缀所以不应该被截断的'), '一个很长很长很长很长的标题而且没有任何后缀所以不应该被截断的')
        many = ','.join('关键词%d' % i for i in range(10))
        pg = agg.parse_page('<html><head><title>T</title><meta name="keywords" content="%s"></head></html>' % many, 'https://x.example/')
        self.assertEqual([t for t in pg['tags'] if t.strip()], [])                                    # ten keywords: boilerplate, dropped
        few = agg.parse_page('<html><head><title>T</title><meta name="keywords" content="库存, 计划"></head></html>', 'https://x.example/')
        self.assertEqual([t.strip() for t in few['tags']], ['库存', '计划'])

    def test_gbk_page(self):
        body = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312"><title>库存管理</title></head></html>'.encode('gb18030')
        self.site.put('/gbk/a', body, 'text/html')
        self.assertEqual(agg.probe(self.site.url + '/gbk/a')['items'][0]['title'], '库存管理')

    def test_markdown_document(self):
        self.site.put('/notes/a.md', '---\ntitle: 笔记A\ntags: [x, y]\n---\n正文第一段，足够长的一句话。', 'text/markdown')
        r = agg.probe(self.site.url + '/notes/a.md')
        it = r['items'][0]
        self.assertEqual((r['entry']['sub'], it['title'], it['type'], it['tags']), ('doc', '笔记A', 'note', ['x', 'y']))

    def test_pdf(self):
        self.site.put('/r/2026-report.pdf', b'%PDF-1.7\n...', 'application/pdf')
        r = agg.probe(self.site.url + '/r/2026-report.pdf')
        self.assertEqual((r['items'][0]['type'], r['items'][0]['title']), ('pdf', '2026 report'))
        self.site.put('/r/fake.pdf', b'<html>not a pdf</html>', 'application/pdf')
        with self.assertRaises(agg.Refused):
            agg.probe(self.site.url + '/r/fake.pdf')

    def test_unsupported_github_urls(self):
        for u in ('https://github.com/someone', 'https://github.com/o/r/issues/1'):
            with self.assertRaises(agg.Refused):
                agg.probe(u)

    def test_blocklist(self):
        with open(os.path.join(self.tmp, 'registry', 'blocklist.txt'), 'w') as f:
            f.write('# comment\n127.0.0.1\n')
        self.site.put('/feed.json', JSON_FEED)
        with self.assertRaises(agg.Refused):
            agg.probe(self.site.url + '/feed.json')


# a repository on the fake GitHub: /api/repos/o/r, /api/repos/o/r/git/trees/main, /raw/o/r/main/<path>
def repo(site, files, name='r', owner='o', topics=('supply-chain',), truncated=False):
    site.put('/api/repos/%s/%s' % (owner, name), json.dumps({'name': name, 'default_branch': 'main', 'description': 'A repo of notes', 'topics': list(topics), 'private': False,
                                                              'html_url': 'https://github.com/%s/%s' % (owner, name), 'license': {'spdx_id': 'MIT'}}), 'application/json')
    site.put('/api/repos/%s/%s/git/trees/main?recursive=1' % (owner, name),
             json.dumps({'tree': [{'path': p, 'type': 'blob', 'size': 10} for p in files] + [{'path': 'docs', 'type': 'tree'}], 'truncated': truncated}), 'application/json')
    for p, text in files.items() if isinstance(files, dict) else []:
        site.put('/raw/%s/%s/main/%s' % (owner, name, p), text, 'text/plain')


class TestGitHub(Base):
    FILES = {'README.md': '# 我的笔记库\n\n这个仓库放着我的读书笔记。', 'docs/issue-9.md': '---\ntitle: 第九期\ntags: [周刊]\n---\n九', 'docs/issue-10.md': '# 第十期\n\n十期内容概要。',
             'docs/sub/README.md': '# 子目录说明\n\nx', 'LICENSE': 'MIT', 'CHANGELOG.md': '# changes', 'node_modules/x/readme.md': '# no', 'themes/t/a.md': '# no',
             '.github/PULL_REQUEST_TEMPLATE.md': '# no', 'img/a.png': 'png', 'papers/Deep-Learning.pdf': '%PDF', 'site/page.html': '<title>Page</title>'}

    def test_repo_shelf(self):
        repo(self.site, self.FILES)
        r = agg.probe('https://github.com/o/r')
        paths = [i['url'].split('/blob/main/')[-1] for i in r['items']]
        self.assertEqual(r['entry']['kind'], 'repo')
        self.assertEqual(sorted(paths), sorted(['README.md', 'docs/issue-9.md', 'docs/issue-10.md', 'papers/Deep-Learning.pdf', 'site/page.html']))
        titles = {i['url'].split('/blob/main/')[-1]: i for i in r['items']}
        self.assertEqual(titles['docs/issue-10.md']['title'], '第十期')
        self.assertEqual((titles['docs/issue-9.md']['title'], titles['docs/issue-9.md']['tags']), ('第九期', ['周刊']))
        self.assertEqual(titles['README.md']['title'], '我的笔记库')
        self.assertEqual(titles['papers/Deep-Learning.pdf']['type'], 'pdf')
        self.assertEqual(titles['papers/Deep-Learning.pdf']['title'], 'Deep Learning')
        self.assertEqual((r['shelf']['name'], r['shelf']['license'], r['shelf']['tags'], r['shelf']['description']), ('o/r', 'MIT', ['supply-chain'], 'A repo of notes'))
        self.assertTrue(all(i['url'].startswith('https://github.com/o/r/blob/main/') for i in r['items']))

    def test_natural_order_newest_number_first(self):
        repo(self.site, {'docs/issue-9.md': '# nine', 'docs/issue-10.md': '# ten', 'docs/issue-100.md': '# hundred'})
        r = agg.probe('https://github.com/o/r/tree/main/docs')
        self.assertEqual([i['title'] for i in r['items']], ['hundred', 'ten', 'nine'])
        self.assertEqual(r['shelf']['name'], 'o/r · docs')

    def test_folder_scope_keeps_its_own_readme_only(self):
        repo(self.site, self.FILES)
        r = agg.probe('https://github.com/o/r/tree/main/docs')
        paths = sorted(i['url'].split('/blob/main/')[-1] for i in r['items'])
        self.assertEqual(paths, ['docs/issue-10.md', 'docs/issue-9.md'])

    def test_file_cap_and_titles_read_only_for_the_first_ones(self):
        files = {'docs/p%03d.md' % n: '# Title %d' % n for n in range(agg.REPO_FILES + 20)}
        repo(self.site, files)
        r = agg.probe('https://github.com/o/r')
        self.assertEqual(len(r['items']), agg.REPO_FILES)
        self.assertTrue(any('只收录前' in zh for zh, en in r['warnings']))
        raw_hits = [p for p, h in self.site.hits if p.startswith('/raw/')]
        self.assertEqual(len(raw_hits), agg.REPO_READ)

    def test_files_that_were_not_read_take_the_language_of_those_that_were(self):
        files = {'docs/issue-%03d.md' % n: '# 第%d期\n\n这是一段中文的内容概要。' % n for n in range(agg.REPO_READ + 10)}
        repo(self.site, files)
        r = agg.probe('https://github.com/o/r')
        self.assertEqual({i['lang'] for i in r['items']}, {'zh'})                 # also the 10 whose title is only 'issue 004'
        self.assertEqual(r['shelf']['lang'], 'zh')
        self.assertIn('issue 004', [i['title'] for i in r['items']])

    def test_pages_site_repo_links_html_to_the_site(self):
        repo(self.site, {'index.html': '<title>Home</title>', 'about/index.html': '<title>About</title>', '2026/post.md': '# Post'}, name='o.github.io')
        r = agg.probe('https://github.com/o/o.github.io')
        urls = sorted(i['url'] for i in r['items'])
        self.assertIn('https://o.github.io/index.html', urls)
        self.assertIn('https://github.com/o/o.github.io/blob/main/2026/post.md', urls)

    def test_missing_or_private_repo(self):
        with self.assertRaises(agg.Refused) as cm:
            agg.probe('https://github.com/o/none')
        self.assertIn('找不到', str(cm.exception))
        repo(self.site, {'a.md': '# a'}, name='priv')
        self.site.put('/api/repos/o/priv', json.dumps({'name': 'priv', 'private': True, 'default_branch': 'main'}), 'application/json')
        with self.assertRaises(agg.Refused):
            agg.probe('https://github.com/o/priv')

    def test_repo_without_documents(self):
        repo(self.site, {'src/main.py': 'print(1)'})
        with self.assertRaises(agg.Refused):
            agg.probe('https://github.com/o/r')

    def test_blob_file(self):
        self.site.put('/raw/o/r/main/notes/a.md', '# 单个文件\n\n摘要在这里，足够长。', 'text/plain')
        r = agg.probe('https://github.com/o/r/blob/main/notes/a.md')
        it = r['items'][0]
        self.assertEqual((r['entry']['sub'], it['title'], it['url'], it['type']), ('file', '单个文件', 'https://github.com/o/r/blob/main/notes/a.md', 'note'))
        self.assertEqual(agg.probe('https://raw.githubusercontent.com/o/r/main/notes/a.md')['items'][0]['title'], '单个文件')

    def test_gist(self):
        gid = 'ab' * 16
        self.site.put('/api/gists/' + gid, json.dumps({'html_url': 'https://gist.github.com/me/' + gid, 'description': 'desc', 'created_at': '2026-10-05T10:00:00Z', 'owner': {'login': 'me'},
                                                       'files': {'a.txt': {'content': 'x'}, 'post.md': {'content': '# Gist 标题\n\n来自 Gist 的第一段。'}}}), 'application/json')
        r = agg.probe('https://gist.github.com/me/' + gid)
        it = r['items'][0]
        self.assertEqual((it['title'], it['summary'], it['date'], it['url']), ('Gist 标题', '来自 Gist 的第一段。', '2026-10-05', 'https://gist.github.com/me/' + gid))
        self.assertEqual(r['shelf']['name'], 'me · Gist')
        self.assertTrue(r['warnings'])                                           # several files: only one is listed
        self.site.put('/api/gists/' + 'cd' * 16, json.dumps({'files': {'a.png': {'content': ''}}}), 'application/json')
        with self.assertRaises(agg.Refused):
            agg.probe('https://gist.github.com/' + 'cd' * 16)

    def test_token_is_sent_to_github_only(self):
        os.environ['GITHUB_TOKEN'] = 'tok'
        try:
            repo(self.site, {'a.md': '# a'})
            agg.GITHUB_API = self.site.url + '/api'                               # not api.github.com: no token
            agg.probe('https://github.com/o/r')
            self.assertFalse(any('Authorization' in h for p, h in self.site.hits))
        finally:
            del os.environ['GITHUB_TOKEN']


def feed_site(site, title='Blog', posts=(('One', '2026-10-01'),), path='/feed.xml'):
    rows = ''.join('<item><title>%s</title><link>https://x.example/%s</link><pubDate>%s</pubDate><category>cat</category></item>' % (t, t.lower().replace(' ', '-'), d) for t, d in posts)
    site.put(path, '<?xml version="1.0"?><rss version="2.0"><channel><title>%s</title><link>https://x.example/</link><language>en</language>%s</channel></rss>' % (title, rows), 'application/rss+xml')


class TestRegistryAndBuild(Base):
    def test_approve_fetch_build(self):
        feed_site(self.site, posts=(('One', '2026-10-01'), ('Two', '2026-10-02')))
        e, r = agg.approve(self.site.url + '/feed.xml', ['Supply Chain', '采购'])
        self.assertTrue(e['id'].startswith('127-0-0-1'))
        reg = agg.load_registry()
        self.assertEqual((reg['shelves'][0]['kind'], reg['shelves'][0]['tags']), ('feed', ['Supply Chain', '采购']))
        with self.assertRaises(agg.Refused):                                      # the same address twice
            agg.approve(self.site.url + '/feed.xml')
        snap = agg.load_snapshot(e['id'])
        self.assertEqual((snap['status'], len(snap['items'])), ('ok', 2))
        out = agg.build()
        d = json.loads(out['directory.json'])
        self.assertEqual((d['spec'], d['count'], len(d['shelves'])), (1, 2, 1))
        self.assertEqual([i['title'] for i in d['items']], ['Two', 'One'])
        self.assertEqual(d['items'][0]['tags'], ['cat', 'Supply Chain', '采购'])      # item tags first, then the shelf's own
        names = {t['name']: t for t in d['tags']}
        self.assertEqual(names['cat']['n'], 2)
        self.assertTrue(names['采购']['slug'].startswith('t-'))                      # ASCII file names only
        self.assertEqual(names['Supply Chain']['slug'], 'supply-chain')
        for rel in ('feed/all.xml', 'feed/all.json', 'feed/type/article.xml', 'feed/tag/supply-chain.xml', 'feed/tag/' + names['采购']['slug'] + '.json', 'feed/shelf/' + e['id'] + '.xml'):
            self.assertIn(rel, out, rel)
        for rel, text in out.items():
            if rel.endswith('.xml') or rel.endswith('.opml'):
                ET.fromstring(text.encode('utf-8'))                               # well-formed
            elif rel.endswith('.json'):
                json.loads(text)
        jf = json.loads(out['feed/tag/supply-chain.json'])
        self.assertEqual(jf['version'], 'https://jsonfeed.org/version/1.1')
        self.assertTrue(all(i.get('content_text') for i in jf['items']))           # JSON Feed needs content_text or content_html
        self.assertEqual(jf['items'][0]['_shelf']['shelf'], e['id'])
        opml = ET.fromstring(out['shelves.opml'].encode())
        self.assertEqual(opml.find('.//outline[@type="rss"]').get('xmlUrl'), self.site.url + '/feed.xml')
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'docs', 'directory.json')))

    def test_a_starter_feed_is_marked_in_the_directory_and_nothing_else_is(self):
        feed_site(self.site, title='Good', posts=(('One', '2026-10-01'),), path='/a.xml')
        feed_site(self.site, title='Other', posts=(('Two', '2026-10-02'),), path='/b.xml')
        e, _ = agg.approve(self.site.url + '/a.xml', starter=True)
        agg.approve(self.site.url + '/b.xml')
        self.assertTrue(agg.load_registry()['shelves'][0]['starter'])
        self.assertNotIn('starter', agg.load_registry()['shelves'][1])
        d = json.loads(agg.build()['directory.json'])
        marked = {s['id']: s.get('starter') for s in d['shelves']}
        self.assertEqual(marked[e['id']], True)
        self.assertEqual(sum(1 for v in marked.values() if v), 1)                  # only the one that was asked for
        reg = agg.load_registry()
        reg['shelves'][1]['starter'] = 'yes'                                       # anything but a real true is ignored
        agg.write_json(agg.reg_path(), reg)
        self.assertEqual(sum(1 for s in json.loads(agg.build()['directory.json'])['shelves'] if s.get('starter')), 1)

    def test_first_seen_date_is_kept_and_feed_updates(self):
        site = self.site
        site.put('/f.xml', '<rss version="2.0"><channel><title>No dates</title><item><title>A</title><link>https://x.example/a</link></item></channel></rss>', 'application/rss+xml')
        e, _ = agg.approve(site.url + '/f.xml')
        first = agg.load_snapshot(e['id'])['items'][0]['date']
        self.assertEqual(first, agg.today().isoformat())
        snap = agg.load_snapshot(e['id'])
        snap['items'][0]['date'] = '2026-01-01'                                     # pretend it was first seen long ago
        agg.write_json(os.path.join(agg.snap_dir(), e['id'] + '.json'), snap)
        site.put('/f.xml', '<rss version="2.0"><channel><title>No dates</title><item><title>B</title><link>https://x.example/b</link></item>'
                           '<item><title>A</title><link>https://x.example/a</link></item></channel></rss>', 'application/rss+xml')
        self.quiet(agg.fetch_all)
        dates = {i['title']: i['date'] for i in agg.load_snapshot(e['id'])['items']}
        self.assertEqual(dates, {'A': '2026-01-01', 'B': agg.today().isoformat()})

    def test_a_failing_source_keeps_its_items_and_is_hidden_after_a_week(self):
        feed_site(self.site)
        e, _ = agg.approve(self.site.url + '/feed.xml')
        self.site.put('/feed.xml', 'gone', status=500)
        for n in range(1, agg.DOWN_AFTER):
            self.quiet(agg.fetch_all)
            snap = agg.load_snapshot(e['id'])
            self.assertEqual((snap['status'], snap['fails'], len(snap['items'])), ('error', n, 1))
            self.assertEqual(json.loads(agg.build()['directory.json'])['count'], 1)      # still shown, from the last good copy
        self.quiet(agg.fetch_all)
        self.assertEqual(json.loads(agg.build()['directory.json'])['count'], 0)           # a week of failures: hidden
        feed_site(self.site)                                                           # it comes back by itself
        self.quiet(agg.fetch_all)
        self.assertEqual(json.loads(agg.build()['directory.json'])['count'], 1)

    def test_not_modified_keeps_the_snapshot(self):
        feed_site(self.site)
        self.site.put('/feed.xml', '<rss version="2.0"><channel><title>T</title><item><title>One</title><link>https://x.example/one</link></item></channel></rss>',
                      'application/rss+xml', ETag='"v1"')
        e, _ = agg.approve(self.site.url + '/feed.xml')
        self.assertEqual(agg.load_snapshot(e['id'])['etag'], '"v1"')
        self.site.put('/feed.xml', '', status=304)
        self.quiet(agg.fetch_all)
        snap = agg.load_snapshot(e['id'])
        self.assertEqual((snap['status'], len(snap['items']), snap['fails']), ('ok', 1, 0))

    def test_a_snapshot_made_by_older_rules_is_not_trusted_on_304(self):
        self.site.put('/feed.xml', '<rss version="2.0"><channel><title>T</title><item><title>One</title><link>https://x.example/one</link></item></channel></rss>',
                      'application/rss+xml', ETag='"v1"')
        e, _ = agg.approve(self.site.url + '/feed.xml')
        path = os.path.join(agg.snap_dir(), e['id'] + '.json')
        snap = agg.load_snapshot(e['id'])
        snap['parser'] = agg.PARSER - 1
        agg.write_json(path, snap)
        before = len(self.site.hits)
        self.quiet(agg.fetch_all)
        sent = [h for p, h in self.site.hits[before:] if p == '/feed.xml']
        self.assertTrue(sent and 'If-None-Match' not in sent[0])                      # asked again without the validator
        self.assertEqual(agg.load_snapshot(e['id'])['parser'], agg.PARSER)

    def test_duplicates_across_shelves_and_tag_aliases(self):
        feed_site(self.site, path='/a.xml')
        feed_site(self.site, title='Second', path='/b.xml')
        agg.approve(self.site.url + '/a.xml')
        agg.approve(self.site.url + '/b.xml')
        agg.write_json(os.path.join(self.tmp, 'registry', 'tags.json'), {'CAT': 'Category'})
        d = json.loads(agg.build()['directory.json'])
        self.assertEqual(d['count'], 1)                                              # the same link in two feeds is listed once
        self.assertEqual(d['items'][0]['tags'], ['Category'])

    def test_stale_feed_files_go_but_other_files_stay(self):
        feed_site(self.site)
        e, _ = agg.approve(self.site.url + '/feed.xml', ['x'])
        agg.build()
        docs = os.path.join(self.tmp, 'docs')
        with open(os.path.join(docs, 'CNAME'), 'w') as f:
            f.write('dir.example.com')
        self.assertTrue(os.path.exists(os.path.join(docs, 'feed', 'tag', 'x.xml')))
        agg.remove(e['id'])
        agg.build()
        self.assertFalse(os.path.exists(os.path.join(docs, 'feed', 'tag', 'x.xml')))
        self.assertFalse(os.path.exists(os.path.join(docs, 'feed', 'shelf')))
        self.assertTrue(os.path.exists(os.path.join(docs, 'CNAME')))
        self.assertFalse(os.path.exists(os.path.join(agg.snap_dir(), e['id'] + '.json')))

    def test_build_writes_nothing_when_nothing_changed(self):
        feed_site(self.site)
        agg.approve(self.site.url + '/feed.xml')
        agg.build()
        path = os.path.join(self.tmp, 'docs', 'directory.json')
        m = os.stat(path).st_mtime_ns
        agg.build()
        self.assertEqual(os.stat(path).st_mtime_ns, m)

    def test_remove_validates_the_id(self):
        for bad in ('../x', 'a/b', '', 'UPPER'):
            with self.assertRaises(agg.Refused):
                agg.remove(bad)
        with self.assertRaises(agg.Refused):
            agg.remove('nope')

    def test_repo_shelf_is_registered_with_its_scope(self):
        repo(self.site, {'docs/a.md': '# A'})
        e, _ = agg.approve('https://github.com/o/r/tree/main/docs')
        self.assertEqual((e['id'], e['repo']), ('gh-o-r-docs', {'owner': 'o', 'repo': 'r', 'ref': 'main', 'path': 'docs'}))
        d = json.loads(agg.build()['directory.json'])
        self.assertEqual((d['items'][0]['type'], d['items'][0]['date'], d['items'][0]['tags']), ('note', '', ['supply-chain']))   # no date of its own: stays empty


BODY = '''### 网址

%s

### 标签（可选）

%s

### 权利确认

%s
'''


def event(url='https://example.com/', tags='_No response_', rights='我有权发布这些内容 / I have the right to publish this', number=7):
    return {'issue': {'number': number, 'body': BODY % (url, tags, rights)}}


class TestIssues(Base):
    def test_parse_form(self):
        url, tags, rights, n = agg.read_submission(event('https://a.example/blog.', 'ops, 采购，供应链'))
        self.assertEqual((url, tags, rights, n), ('https://a.example/blog', ['ops', '采购', '供应链'], True, 7))
        self.assertFalse(agg.read_submission(event(rights='_No response_'))[2])
        self.assertFalse(agg.read_submission(event(rights='- [ ] 我有权发布'))[2])
        self.assertTrue(agg.read_submission(event(rights='- [X] 我有权发布'))[2])
        self.assertEqual(agg.read_submission(event('example.com/blog'))[0], 'example.com/blog')
        self.assertEqual(agg.read_submission({'issue': {'body': 'please add https://z.example/x thanks', 'number': 1}})[0], 'https://z.example/x')
        self.assertEqual(agg.read_submission({})[0], '')

    def test_check_passes_and_comments(self):
        self.site.put('/feed.xml', '<rss version="2.0"><channel><title>Blog @owner</title><item><title>Hello @everyone [click](javascript:alert(1))</title>'
                                   '<link>https://x.example/p/1</link><pubDate>Thu, 01 Oct 2026 00:00:00 GMT</pubDate></item></channel></rss>', 'application/rss+xml')
        res = agg.handle_issue(event(self.site.url + '/feed.xml'), 'check')
        self.assertTrue(res['ok'])
        self.assertEqual((res['add'], res['remove']), (['checked'], ['invalid', 'duplicate', 'similar']))
        self.assertIn('自动检查通过', res['comment'])
        self.assertNotIn('@everyone', res['comment'])                              # no pinging strangers from a bot comment
        self.assertNotIn('@owner', res['comment'])
        self.assertIsNone(re.search(r'(?<!\\)\]\((?!https?://)', res['comment']))   # every real link target is http(s); the rest is escaped text
        self.assertEqual(agg.load_registry()['shelves'], [])                      # nothing is listed before the label

    def test_check_fails_with_a_reason(self):
        res = agg.handle_issue(event(self.site.url + '/nothing'), 'check')
        self.assertFalse(res['ok'])
        self.assertEqual((res['add'], res['remove']), (['invalid'], ['checked', 'duplicate', 'similar']))
        self.assertIn('暂时无法收录', res['comment'])
        res = agg.handle_issue(event(self.site.url + '/feed.xml', rights='_No response_'), 'check')
        self.assertIn('我有权发布', res['comment'])
        res = agg.handle_issue({'issue': {'body': '', 'number': 3}}, 'check')
        self.assertIn('表单', res['comment'])

    def test_approve_lists_it_and_closes(self):
        feed_site(self.site)
        res = agg.handle_issue(event(self.site.url + '/feed.xml', tags='ops'), 'approve')
        self.assertTrue(res['ok'] and res['close'])
        self.assertEqual(res['add'], ['listed'])
        reg = agg.load_registry()['shelves']
        self.assertEqual((len(reg), reg[0]['issue'], reg[0]['tags']), (1, 7, ['ops']))
        self.assertTrue(os.path.exists(os.path.join(agg.snap_dir(), reg[0]['id'] + '.json')))
        again = agg.handle_issue(event(self.site.url + '/feed.xml'), 'approve')
        self.assertFalse(again['ok'])

    def test_probe_says_when_it_is_already_listed(self):
        feed_site(self.site)
        agg.approve(self.site.url + '/feed.xml')
        res = agg.handle_issue(event(self.site.url + '/feed.xml'), 'check')
        self.assertIn('已经在目录里', res['comment'])

    RES = {'number': 7, 'ok': True, 'comment': 'checked **ok**', 'add': ['checked'], 'remove': ['invalid'], 'close': False}

    def api(self, **statuses):
        """The fake GitHub for issue 7 of o/r: every call succeeds unless a status is given for its name."""
        s, base = self.site, '/api/repos/o/r'
        os.environ['GITHUB_TOKEN'] = 'tok'
        self.addCleanup(os.environ.pop, 'GITHUB_TOKEN', None)
        s.put(base + '/issues/7/comments?per_page=100', json.dumps(statuses.get('comments', [])), 'application/json')
        s.on('POST', base + '/issues/7/comments', statuses.get('post', 201), {'id': 1})
        s.on('PATCH', base + '/issues/comments/99', statuses.get('patch', 200), {'id': 99})
        s.on('POST', base + '/labels', statuses.get('label', 201), {})
        s.on('POST', base + '/issues/7/labels', statuses.get('labels', 200), [])
        s.on('DELETE', base + '/issues/7/labels/invalid', statuses.get('unlabel', 200), [])
        s.on('PATCH', base + '/issues/7', statuses.get('close', 200), {})

    def test_apply_creates_the_comment_and_sets_the_labels(self):
        self.api()
        self.assertEqual(agg.apply_result(self.RES, 'o/r'), [])
        sent = {(m, p): b for m, p, b, h in self.site.calls}
        comment = sent[('POST', '/api/repos/o/r/issues/7/comments')]['body']
        self.assertTrue(comment.startswith(agg.BOT_MARKER) and 'checked **ok**' in comment)
        self.assertEqual(sent[('POST', '/api/repos/o/r/issues/7/labels')], {'labels': ['checked']})
        self.assertIn(('DELETE', '/api/repos/o/r/issues/7/labels/invalid'), sent)
        self.assertNotIn(('PATCH', '/api/repos/o/r/issues/7'), sent)                   # not closed
        self.assertTrue(all(h.get('Authorization') == 'Bearer tok' for _, _, _, h in self.site.calls))

    def test_apply_edits_its_earlier_comment_instead_of_adding_another(self):
        self.api(comments=[{'id': 5, 'body': 'someone else'}, {'id': 99, 'body': agg.BOT_MARKER + '\nold'}])
        self.assertEqual(agg.apply_result({**self.RES, 'close': True}, 'o/r'), [])
        methods = [(m, p) for m, p, b, h in self.site.calls]
        self.assertIn(('PATCH', '/api/repos/o/r/issues/comments/99'), methods)
        self.assertNotIn(('POST', '/api/repos/o/r/issues/7/comments'), methods)
        self.assertIn(('PATCH', '/api/repos/o/r/issues/7'), methods)                   # closed
        self.assertEqual([b for m, p, b, h in self.site.calls if (m, p) == ('PATCH', '/api/repos/o/r/issues/7')], [{'state': 'closed', 'state_reason': 'completed'}])

    def test_apply_reports_what_failed_with_the_reason(self):
        self.api(post=403, labels=403)
        self.site.on('POST', '/api/repos/o/r/issues/7/comments', 403, {'message': 'Resource not accessible by integration'})
        problems = agg.apply_result(self.RES, 'o/r')
        self.assertTrue(any('create comment: HTTP 403 Resource not accessible by integration' in p for p in problems), problems)
        self.assertTrue(any('add labels' in p for p in problems), problems)

    def test_apply_tolerates_an_existing_label_and_a_label_that_is_not_on_the_issue(self):
        self.api(label=422, unlabel=404)
        self.assertEqual(agg.apply_result(self.RES, 'o/r'), [])

    def test_apply_needs_a_token_and_a_repository(self):
        with self.assertRaises(agg.Refused):
            agg.apply_result(self.RES, 'o/r')                                             # no GITHUB_TOKEN
        os.environ['GITHUB_TOKEN'] = 'tok'
        try:
            self.assertTrue(agg.apply_result(self.RES, ''))
            self.assertTrue(agg.apply_result({**self.RES, 'number': None}, 'o/r'))
        finally:
            del os.environ['GITHUB_TOKEN']

    def test_apply_command(self):
        self.api()
        path = os.path.join(self.tmp, 'result.json')
        agg.write_json(path, self.RES)
        os.environ['GITHUB_REPOSITORY'] = 'o/r'
        self.addCleanup(os.environ.pop, 'GITHUB_REPOSITORY', None)
        self.assertEqual(agg.main(['apply', '--result', path]), 0)
        self.site.on('POST', '/api/repos/o/r/issues/7/comments', 500, {'message': 'boom'})
        self.assertEqual(agg.main(['apply', '--result', path]), 1)                        # a failure is a failed step, not a silent one
        self.assertEqual(agg.main(['apply', '--result', os.path.join(self.tmp, 'never-written.json')]), 1)      # and so is a missing result file

    def test_the_workflows_pass_a_bare_file_name(self):
        """`--out result.json` is how the Action calls it: no folder in the name. Every real run failed on this once."""
        feed_site(self.site)
        ev = os.path.join(self.tmp, 'event.json')
        agg.write_json(ev, event(self.site.url + '/feed.xml'))
        old = os.getcwd()
        os.chdir(self.tmp)
        try:
            for cmd in ('issue', 'approve', 'reject'):
                agg.write_json('event.json', event(self.site.url + '/feed.xml' if cmd != 'approve' else self.site.url + '/feed.xml'))
                code = agg.main([cmd, '--event', 'event.json', '--out', 'result.json'])
                self.assertTrue(os.path.isfile('result.json'), cmd)
                self.assertIn('number', json.load(open('result.json', encoding='utf-8')), cmd)
                os.remove('result.json')
        finally:
            os.chdir(old)

    def test_cli(self):
        feed_site(self.site)
        ev = os.path.join(self.tmp, 'event.json')
        out = os.path.join(self.tmp, 'out.json')
        with open(ev, 'w', encoding='utf-8') as f:
            json.dump(event(self.site.url + '/feed.xml'), f)
        self.assertEqual(agg.main(['issue', '--event', ev, '--out', out]), 0)
        self.assertTrue(json.load(open(out, encoding='utf-8'))['ok'])
        self.assertEqual(agg.main(['approve', '--event', ev, '--out', out]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'docs', 'directory.json')))
        self.assertEqual(agg.main(['probe', self.site.url + '/nothing']), 1)


if __name__ == '__main__':
    unittest.main()
