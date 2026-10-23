"""Hot lists as feeds (hotapi.py): every platform's answer is fake here (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import time
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import feedapi  # noqa: E402
import hotapi  # noqa: E402
from test_aggregate import Base  # noqa: E402

WEIBO = {'ok': 1, 'data': {'hotgov': {'word': '#官方置顶#', 'is_gov': 1}, 'realtime': [
    {'word': '第一个话题', 'num': 1359619, 'flag_desc': '综艺', 'label_name': '热', 'realpos': 1}, {'word': '广告位', 'num': 1, 'is_ad': 1}, {'word': '第二个话题 & <b>', 'num': 868104, 'label_name': '新'}]}}
ZHIHU = {'data': [{'type': 'hot_list_feed', 'detail_text': '1094 万热度', 'target': {'id': 2096358861804511510, 'title': '为什么……？', 'type': 'question', 'answer_count': 327}},
                  {'type': 'hot_list_feed', 'target': {'id': 5, 'title': '一篇文章', 'type': 'article'}}]}
BILI = {'code': 0, 'data': {'list': [{'title': '一个视频', 'bvid': 'BV1C8nH69Ekq', 'owner': {'name': 'UP主'}, 'stat': {'view': 579434}}, {'title': '坏的', 'bvid': 'javascript:1', 'stat': {}}]}}
BAIDU = '<html><!--s-data:' + json.dumps({'data': {'cards': [{'content': [{'query': '百度话题', 'hotScore': '7904862', 'rawUrl': 'https://www.baidu.com/s?wd=a', 'desc': '<em>说明</em> 文字'}]}]}}, ensure_ascii=False) + '--></html>'
TOUTIAO = {'data': [{'ClusterId': 7699707790348045850, 'Title': '头条话题', 'HotValue': '12345678', 'Url': 'https://www.toutiao.com/trending/7699707790348045850/?tracking=1'}, {'ClusterId': 'x', 'Title': '坏'}]}
JUEJIN = {'data': [{'content': {'content_id': '7698382188488048649', 'title': '掘金文章'}, 'content_counter': {'hot_rank': 2250}, 'author': {'name': '作者'}}]}
V2EX = [{'title': '一个主题', 'url': 'https://www.v2ex.com/t/1251126', 'replies': 135, 'node': {'title': '问与答'}}, {'title': '外链', 'url': 'https://evil.example/t/1', 'replies': 1}]
NETEASE = {'result': {'tracks': [{'name': '歌', 'id': 2078101758, 'artists': [{'name': '甲'}, {'name': '乙'}], 'album': {'name': '专辑'}}]}}
GITHUB = ('<html><body>' + ''.join('<article class="Box-row"><div class="float-right"><a href="/login?return_to=%%2F%s">star</a></div><h2 class="h3 lh-condensed"><a href="/%s" data-hydro-click="x">\n  %s\n</a></h2>'
          '<p class="col-9 color-fg-muted my-1 pr-4">  A <b>great</b> tool  </p><div><span itemprop="programmingLanguage">Rust</span><a href="/%s/stargazers">1,234</a>'
          '<span class="d-inline-block float-sm-right">%s stars today</span></div></article>' % (r, r, r, r, s) for r, s in (('owner/one', '1,500'), ('owner/two', '20'))) + '</body></html>')
FIXTURES = {'weibo': json.dumps(WEIBO), 'zhihu': json.dumps(ZHIHU), 'bilibili': json.dumps(BILI), 'baidu': BAIDU, 'toutiao': json.dumps(TOUTIAO), 'juejin': json.dumps(JUEJIN),
            'v2ex': json.dumps(V2EX), 'netease': json.dumps(NETEASE), 'github': GITHUB}
ALL = {'HOT_ENABLE': 'all'}


class HotBase(Base):
    def setUp(self):
        super().setUp()
        self.saved = {k: dict(v) for k, v in hotapi.PLATFORMS.items()}
        hotapi._CACHE.clear()
        for pid, body in FIXTURES.items():
            self.site.put('/' + pid, body, 'application/json; charset=utf-8' if pid not in ('baidu', 'github') else 'text/html; charset=utf-8')
            hotapi.PLATFORMS[pid]['url'] = self.site.url + '/' + pid

    def tearDown(self):
        for k, v in self.saved.items():
            hotapi.PLATFORMS[k].update(v)
        hotapi._CACHE.clear()
        super().tearDown()

    def get(self, pid, env=ALL, **q):
        query = {'p': [pid], **{k: [str(v)] for k, v in q.items()}}
        return hotapi.handle(query, env)


class TestParsers(HotBase):
    def items(self, pid):
        st, ctype, body, cache = self.get(pid)
        self.assertEqual((st, ctype), (200, 'application/rss+xml; charset=utf-8'), body[:200])
        ch = ET.fromstring(body).find('channel')
        return [{x.tag: (x.text or '') for x in i} for i in ch.findall('item')], ch

    def test_every_platform_becomes_a_well_formed_feed_with_rank_title_heat_and_a_link(self):
        want = {'weibo': 2, 'zhihu': 1, 'bilibili': 1, 'baidu': 1, 'toutiao': 1, 'juejin': 1, 'v2ex': 1, 'netease': 1, 'github': 2}
        for pid, n in want.items():
            items, ch = self.items(pid)
            self.assertEqual(len(items), n, pid)
            self.assertEqual(ch.find('title').text, hotapi.PLATFORMS[pid]['name'])
            for i in items:
                self.assertTrue(i['title'] and i['link'].startswith('https://') and i['guid'].startswith('hot:%s:' % pid), (pid, i))
                self.assertTrue(i['description'].startswith('第 1 名') or i['description'].startswith('第 2 名'), (pid, i['description']))

    def test_what_each_platform_says(self):
        i, _ = self.items('weibo')
        self.assertEqual(i[0]['title'], '第一个话题')
        self.assertIn('热度 136.0 万', i[0]['description'])
        self.assertIn('综艺', i[0]['description'])
        self.assertEqual(i[0]['link'], 'https://s.weibo.com/weibo?q=%23%E7%AC%AC%E4%B8%80%E4%B8%AA%E8%AF%9D%E9%A2%98%23')
        self.assertEqual(i[1]['title'], '第二个话题 & <b>')                                       # (escaped in the XML, text again once parsed)
        self.assertNotIn('官方置顶', json.dumps(i, ensure_ascii=False))                          # what the government pins is not a hot search; ads are not either
        self.assertNotIn('广告位', json.dumps(i, ensure_ascii=False))
        z, _ = self.items('zhihu')
        self.assertEqual((z[0]['link'], z[0]['description']), ('https://www.zhihu.com/question/2096358861804511510', '第 1 名 · 1094 万热度'))     # only questions
        b, _ = self.items('bilibili')
        self.assertEqual((b[0]['link'], b[0]['description']), ('https://www.bilibili.com/video/BV1C8nH69Ekq', '第 1 名 · 播放 57.9 万 · UP主'))
        bd, _ = self.items('baidu')
        self.assertEqual((bd[0]['title'], bd[0]['description']), ('百度话题', '第 1 名 · 热度 790.5 万 · 说明 文字'))
        t, _ = self.items('toutiao')
        self.assertEqual(t[0]['link'], 'https://www.toutiao.com/trending/7699707790348045850/')   # no tracking parameters
        j, _ = self.items('juejin')
        self.assertEqual(j[0]['link'], 'https://juejin.cn/post/7698382188488048649')
        v, _ = self.items('v2ex')
        self.assertEqual((v[0]['link'], v[0]['description']), ('https://www.v2ex.com/t/1251126', '第 1 名 · 回复 135 · 问与答'))     # an address that is not V2EX's is dropped
        n, _ = self.items('netease')
        self.assertEqual((n[0]['title'], n[0]['link']), ('歌 — 甲 / 乙', 'https://music.163.com/#/song?id=2078101758'))
        g, _ = self.items('github')
        self.assertEqual((g[0]['title'], g[0]['link'], g[0]['description']), ('owner/one', 'https://github.com/owner/one', '第 1 名 · 今日 +1,500 ★ · Rust · A great tool'))

    def test_the_feed_is_one_the_shelf_can_subscribe_to(self):
        self.site.put('/feed.xml', self.get('v2ex')[2].decode('utf-8'), 'application/rss+xml')
        d = feedapi.discover(self.site.url + '/feed.xml')
        self.assertEqual((d['kind'], d['title'], d['found']), ('feed', 'V2EX 今日热议', 1))
        self.assertEqual(d['items'][0]['url'], 'https://www.v2ex.com/t/1251126')

    def test_a_count_and_the_json_form(self):
        self.assertEqual(len(ET.fromstring(self.get('weibo', n=1)[2]).find('channel').findall('item')), 1)
        self.assertEqual(len(ET.fromstring(self.get('weibo', n=0)[2]).find('channel').findall('item')), 1)       # at least one
        self.assertEqual(len(ET.fromstring(self.get('weibo', n='x')[2]).find('channel').findall('item')), 2)
        st, ctype, body, _ = self.get('weibo', format='json')
        d = json.loads(body)
        self.assertEqual((st, d['platform'], d['items'][0]['title']), (200, 'weibo', '第一个话题'))


class TestWhatIsOn(HotBase):
    def test_the_news_platforms_are_off_until_the_owner_turns_them_on(self):
        self.assertEqual(hotapi.enabled({}), ['github', 'juejin', 'v2ex', 'bilibili', 'netease'])
        for pid in hotapi.NEWS:
            st, _, body, cache = self.get(pid, env={})
            self.assertEqual((st, cache), (404, 'no-store'), pid)                                  # (and nothing was asked of the platform)
        self.assertEqual(self.site.hits, [])
        self.assertEqual(hotapi.enabled({'HOT_ENABLE': 'weibo, zhihu'}), ['github', 'juejin', 'v2ex', 'bilibili', 'netease', 'weibo', 'zhihu'])
        self.assertEqual(self.get('weibo', env={'HOT_ENABLE': 'weibo'})[0], 200)
        self.assertEqual(len(hotapi.enabled({'HOT_ENABLE': 'ALL'})), len(hotapi.PLATFORMS))

    def test_any_platform_can_be_turned_off_and_nonsense_is_ignored(self):
        self.assertEqual(hotapi.enabled({'HOT_DISABLE': 'juejin, v2ex'}), ['github', 'bilibili', 'netease'])
        self.assertEqual(hotapi.enabled({'HOT_ENABLE': 'all', 'HOT_DISABLE': 'weibo'})[-3:], ['zhihu', 'baidu', 'toutiao'])
        self.assertEqual(hotapi.enabled({'HOT_ENABLE': 'nonsense,;rm', 'HOT_DISABLE': '??'}), ['github', 'juejin', 'v2ex', 'bilibili', 'netease'])
        self.assertEqual(self.get('github', env={'HOT_DISABLE': 'github'})[0], 404)

    def test_the_index_says_what_is_on_and_which_are_news(self):
        st, ctype, body, _ = hotapi.handle({}, {})
        d = json.loads(body)
        self.assertEqual((st, d['service']), (200, 'shelf-hot'))
        self.assertEqual([p['id'] for p in d['platforms']], ['github', 'juejin', 'v2ex', 'bilibili', 'netease'])
        d = json.loads(hotapi.handle({}, ALL)[2])
        self.assertEqual([p['id'] for p in d['platforms'] if p['news']], ['weibo', 'zhihu', 'baidu', 'toutiao'])

    def test_a_request_can_only_choose_among_the_fixed_platforms(self):
        for pid in ('../x', 'https://evil.example/', 'weibo/../../x', '', 'GITHUB', 'a' * 500):
            if pid == '':
                continue
            self.assertEqual(self.get(pid)[0], 404, pid)
        self.assertEqual(self.site.hits, [])


class TestKeepingAndFailing(HotBase):
    def test_the_platform_is_asked_once_in_a_while_not_for_every_reader(self):
        for _ in range(5):
            self.assertEqual(self.get('v2ex')[0], 200)
        self.assertEqual(len([p for p, h in self.site.hits if p == '/v2ex']), 1)
        hotapi._CACHE['v2ex'] = (time.time() - hotapi.CACHE_SECONDS - 1, hotapi._CACHE['v2ex'][1])
        self.get('v2ex')
        self.assertEqual(len([p for p, h in self.site.hits if p == '/v2ex']), 2)

    def test_the_platform_is_asked_like_a_browser_and_with_its_referer(self):
        self.get('weibo')
        h = [h for p, h in self.site.hits if p == '/weibo'][0]
        self.assertTrue(h['User-Agent'].startswith('Mozilla/5.0'))
        self.assertEqual(h.get('Referer'), 'https://weibo.com/')
        self.assertNotIn('Cookie', h)

    def test_when_the_platform_does_not_answer_the_last_good_copy_is_served_for_an_hour(self):
        self.get('v2ex')
        self.site.put('/v2ex', 'nope', status=500)
        hotapi._CACHE['v2ex'] = (time.time() - 600, hotapi._CACHE['v2ex'][1])                     # expired for the cache, young enough to serve
        self.assertEqual(self.get('v2ex')[0], 200)
        hotapi._CACHE['v2ex'] = (time.time() - hotapi.STALE_SECONDS - 5, hotapi._CACHE['v2ex'][1])
        st, ctype, body, cache = self.get('v2ex')
        self.assertEqual((st, cache), (502, 'no-store'))
        d = json.loads(body)
        self.assertEqual(d['error'], 'upstream')
        self.assertIn('读不到', d['message'])

    def test_a_changed_format_is_an_error_not_an_empty_feed(self):
        self.site.put('/juejin', '{"data": []}', 'application/json')
        self.assertEqual(self.get('juejin')[0], 502)
        self.site.put('/baidu', '<html>no data here</html>', 'text/html')
        self.assertEqual(self.get('baidu')[0], 502)
        self.site.put('/weibo', 'not json', 'text/plain')
        self.assertEqual(self.get('weibo')[0], 502)
        self.site.put('/github', '<html>login</html>', 'text/html')
        self.assertEqual(self.get('github')[0], 502)
        self.assertEqual(self.get('netease')[0], 200)                                             # one platform failing does not touch another

    def test_the_feed_is_cacheable_and_the_error_is_not(self):
        self.assertIn('s-maxage=600', self.get('v2ex')[3])
        self.assertEqual(self.get('nothing')[3], 'no-store')


class TestHttp(HotBase):
    def serve(self, path, method='GET', env=None):
        class H:
            pass
        out = {'status': None, 'headers': {}, 'body': b''}
        h = H()
        h.path = path

        class W:
            def write(self, b):
                out['body'] += b
        h.wfile = W()
        h.send_response = lambda s: out.update(status=s)
        h.send_header = lambda k, v: out['headers'].__setitem__(k, v)
        h.end_headers = lambda: None
        old = os.environ.copy()
        os.environ.update(env or ALL)
        try:
            hotapi.respond(h, os.environ, method)
        finally:
            os.environ.clear()
            os.environ.update(old)
        return out

    def test_get_gives_the_feed_with_its_headers_and_nothing_else_is_allowed(self):
        o = self.serve('/api/hot?p=v2ex')
        self.assertEqual((o['status'], o['headers']['Content-Type']), (200, 'application/rss+xml; charset=utf-8'))
        self.assertEqual(o['headers']['X-Robots-Tag'], 'noindex')
        self.assertEqual(o['headers']['Access-Control-Allow-Origin'], '*')
        self.assertEqual(int(o['headers']['Content-Length']), len(o['body']))
        idx = json.loads(self.serve('/api/hot')['body'])                        # what a page reads to know which lists exist
        self.assertTrue(idx['ok'] and {'github', 'juejin'} <= {x['id'] for x in idx['platforms']})
        ET.fromstring(o['body'])
        for m in ('POST', 'PUT', 'DELETE'):
            self.assertEqual(self.serve('/api/hot?p=v2ex', m)['status'], 405)


if __name__ == '__main__':
    unittest.main()
