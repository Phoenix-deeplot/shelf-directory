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
ZHIHU = {'data': [{'type': 'hot_list_feed', 'detail_text': '1094 万热度', 'children': [{'type': 'answer', 'thumbnail': 'http://picx.zhimg.com/80/a.jpg'}],
                   'target': {'id': 2096358861804511510, 'title': '为什么……？', 'type': 'question', 'answer_count': 327, 'follower_count': 12, 'excerpt': '摘要 <b>文字</b>'}},
                  {'type': 'hot_list_feed', 'target': {'id': 5, 'title': '一篇文章', 'type': 'article'}}]}
ZHIHU_DAILY = {'stories': [{'title': '日报的一篇', 'url': 'https://daily.zhihu.com/story/9793540', 'hint': 'Tom · 11 分钟阅读'}, {'title': '外链', 'url': 'https://evil.example/x'}]}
BILI = {'code': 0, 'data': {'list': [{'title': '一个视频', 'bvid': 'BV1C8nH69Ekq', 'owner': {'name': 'UP主'}, 'stat': {'view': 579434}}, {'title': '坏的', 'bvid': 'javascript:1', 'stat': {}}]}}
TOUTIAO = {'data': [{'ClusterId': 7699707790348045850, 'Title': '头条话题', 'HotValue': '12345678', 'Url': 'https://www.toutiao.com/trending/7699707790348045850/?tracking=1'}, {'ClusterId': 'x', 'Title': '坏'}]}
DOUYIN = {'data': {'word_list': [{'word': '抖音话题', 'hot_value': 12273749, 'sentence_id': '2714056'}, {'word': '坏的', 'sentence_id': 'x'}]}}
JUEJIN = {'data': [{'content': {'content_id': '7698382188488048649', 'title': '掘金文章'}, 'content_counter': {'hot_rank': 2250}, 'author': {'name': '作者'}}]}
V2EX = [{'title': '一个主题', 'url': 'https://www.v2ex.com/t/1251126', 'replies': 135, 'node': {'title': '问与答'}}, {'title': '外链', 'url': 'https://evil.example/t/1', 'replies': 1}]
NETEASE = {'result': {'tracks': [{'name': '歌', 'id': 2078101758, 'artists': [{'name': '甲'}, {'name': '乙'}], 'album': {'name': '专辑'}}]}}
GITHUB = ('<html><body>' + ''.join('<article class="Box-row"><div class="float-right"><a href="/login?return_to=%%2F%s">star</a></div><h2 class="h3 lh-condensed"><a href="/%s" data-hydro-click="x">\n  %s\n</a></h2>'
          '<p class="col-9 color-fg-muted my-1 pr-4">  A <b>great</b> tool  </p><div><span itemprop="programmingLanguage">Rust</span><a href="/%s/stargazers">1,234</a>'
          '<span class="d-inline-block float-sm-right">%s stars today</span></div></article>' % (r, r, r, r, s) for r, s in (('owner/one', '1,500'), ('owner/two', '20'))) + '</body></html>')
HN = {'hits': [{'title': 'Anthropic S-1', 'url': 'https://www.sec.gov/x.htm', 'objectID': '50242939', 'points': 1510, 'num_comments': 1480}, {'title': 'Ask HN: something', 'url': None, 'objectID': '5', 'points': 3, 'num_comments': 0}]}
LOBSTERS = [{'title': 'Printable', 'url': 'https://brennenputh.me/x/', 'comments_url': 'https://lobste.rs/s/olnqh2/x', 'short_id_url': 'https://lobste.rs/s/olnqh2', 'score': 66, 'comment_count': 17, 'tags': ['web', 'design']},
            {'title': 'A text post', 'url': '', 'comments_url': 'https://lobste.rs/s/zzz/t', 'short_id_url': 'https://lobste.rs/s/zzz', 'score': 2, 'comment_count': 0, 'tags': []}]
DEVTO = [{'title': 'Doors', 'url': 'https://dev.to/a/b', 'public_reactions_count': 20, 'comments_count': 7, 'user': {'name': 'Ann'}}]
THS = {'status_code': 0, 'data': {'stock_list': [{'code': '000678', 'name': '襄阳轴承', 'rate': '856222.0', 'rise_and_fall': 10.0344, 'tag': {'concept_tag': ['减速器', '农机', '汽车', '多余的']}}, {'code': 'abc', 'name': '坏的'}]}}
COINGECKO = {'coins': [{'item': {'id': 'bitcoin', 'name': 'Bitcoin', 'symbol': 'BTC', 'market_cap_rank': 1}}, {'item': {'id': '../x', 'name': 'bad'}}]}
WIKIPEDIA = {'items': [{'articles': [{'article': 'Main_Page', 'views': 99, 'rank': 1}, {'article': 'Special:Search', 'views': 98, 'rank': 2}, {'article': 'Lula_da_Silva', 'views': 272000, 'rank': 3},
                                    {'article': 'Wikipedia:Top_25_Report', 'views': 5, 'rank': 4}, {'article': '-', 'views': 4, 'rank': 5}]}]}
MASTODON = [{'title': 'Audi', 'url': 'https://www.der-postillon.com/a.html', 'provider_name': 'Blogger', 'history': [{'day': '1', 'accounts': '5'}, {'day': '2', 'accounts': '360'}, {'day': '3', 'accounts': '90'}]},
            {'title': 'insecure', 'url': 'http://insecure.example/x', 'history': []}]
HF_MODELS = [{'id': 'deepseek-ai/DeepSeek-V4', 'likes': 3209, 'downloads': 95889, 'pipeline_tag': 'text-generation'}, {'id': 'private/one', 'private': True}, {'id': 'bad id'}]
HF_PAPERS = [{'title': 'DynaFlow', 'paper': {'id': '2610.34467', 'title': 'DynaFlow', 'upvotes': 12}, 'organization': {'fullname': 'NUS'}}, {'paper': {'id': 'x', 'title': 'bad'}}]
WEREAD = {'books': [{'readingCount': 13837, 'bookInfo': {'title': '剑来', 'author': '烽火戏诸侯', 'deepLink': 'https://weread.qq.com/book-detail?type=1&v=8e53', 'newRating': 861}},
                    {'bookInfo': {'title': '没有链接的书', 'author': '甲'}}, {'bookInfo': {'title': '外链', 'deepLink': 'https://evil.example/x'}}]}
OPENLIBRARY = {'works': [{'title': 'Atomic Habits', 'key': '/works/OL17930368W', 'author_name': ['James Clear'], 'first_publish_year': 2016}, {'title': 'bad', 'key': '/x'}]}
GUTENDEX = {'results': [{'id': 1342, 'title': 'Pride and Prejudice', 'authors': [{'name': 'Austen, Jane'}], 'download_count': 190246}, {'id': 'x', 'title': 'bad'}]}
QQMUSIC = {'detail': {'data': {'songInfoList': [{'name': '歌', 'mid': '004VBMk71TdUuR', 'singer': [{'name': '薛之谦'}], 'album': {'name': '初学者'}}, {'name': '坏的', 'mid': '../x'}]}}}
APPLE = {'feed': {'results': [{'name': '声动早咖啡', 'artistName': '声动活泼', 'url': 'https://podcasts.apple.com/cn/podcast/x/id1', 'genres': [{'name': '商务'}]}, {'name': '坏的', 'url': 'https://evil.example/'}]}}
STEAM = {'top_sellers': {'items': [{'id': 4115450, 'name': '影之刃零', 'currency': 'CNY', 'final_price': 26800, 'discount_percent': 0}, {'id': 7, 'name': 'Free One', 'currency': 'CNY', 'final_price': 0, 'discount_percent': 0},
                                   {'id': 8, 'name': 'On Sale', 'currency': 'USD', 'final_price': 1999, 'discount_percent': 20}]}}
YOUTUBE = {'items': [{'id': 'dQw4w9WgXcQ', 'snippet': {'title': 'A video', 'channelTitle': 'A channel'}, 'statistics': {'viewCount': '1234567'}}, {'id': 'short', 'snippet': {'title': 'bad'}}]}
GTRENDS = ('<?xml version="1.0" encoding="UTF-8"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>x</title>'
           '<item><title>darryn peterson</title><ht:approx_traffic>500+</ht:approx_traffic><ht:news_item><ht:news_item_title>Jazz&apos;s Darryn Peterson</ht:news_item_title><ht:news_item_url>https://www.cbssports.com/a</ht:news_item_url></ht:news_item></item>'
           '<item><title>no news</title><ht:approx_traffic>100+</ht:approx_traffic></item></channel></rss>')
TOPHUB_WECHAT = ('<html><head><title>tophub</title></head><body><table>'
                 '<tr><td align="center">1.</td><td><a href="https://mp.weixin.qq.com/s?__biz=A&amp;mid=1&amp;idx=1#rd" target="_blank" rel="nofollow" itemid="1">公众号的一篇</a></td><td class="ws">10.0万</td>'
                 '<td align="right"><a href="https://mp.weixin.qq.com/s?__biz=A&amp;mid=1&amp;idx=1#rd" title="查看详细"><i class="m-n">&#xe652;</i></a></td></tr>'
                 '<tr><td align="center">2.</td><td><a href="https://evil.example/s?x=1" rel="nofollow">别的网站</a></td><td class="ws">9.0万</td></tr>'
                 '<tr><td align="center">3.</td><td><a href="https://mp.weixin.qq.com/s?__biz=B&amp;mid=2&amp;idx=1#rd" rel="nofollow">第三篇</a></td><td class="ws"></td></tr></table></body></html>')
TOPHUB_XUEQIU = ('<html><head><title>tophub</title></head><body><table><tr><td align="center">1.</td><td class="al"><img src="https://xqimg.imedao.com/a.png" width="100" /></td>'
                 '<td class="al"><div><a href="http://xueqiu.com/6180156458/413837502" target="_blank" rel="nofollow">雪球的一帖</a></div><div class="item-desc">杨饭</div></td>'
                 '<td align="right"><a href="http://xueqiu.com/6180156458/413837502" title="查看详细"><i class="m-n">&#xe652;</i></a></td></tr></table></body></html>')
XIAOHONGSHU = ('<html><title>rebang</title><a aria-label="打开图片：面部刮毛护理来啦~超级爽" class="block" href="https://www.xiaohongshu.com/explore/6ad9ea86000000002c018328?xsec_source=pc_pgy&amp;xsec_token=QB%3D" rel="noreferrer"><span>1 00:18</span></a>'
               '<a href="https://www.xiaohongshu.com/explore/6ad9ea86000000002c018328?xsec_source=pc_pgy&amp;xsec_token=QB%3D">面部刮毛护理来啦~超级爽</a>'
               '<a class="x" href="https://www.xiaohongshu.com/explore/6ad73db9000000002f03a334" aria-label="打开图片：第二篇笔记">y</a><a aria-label="打开图片：别处" href="https://evil.example/explore/1">z</a></html>')
RSS = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed</title><link>https://ex.example/</link>'
       '<item><title>Newest one</title><link>https://ex.example/a?utm_source=x&amp;id=1</link><pubDate>Mon, 02 Mar 2026 10:00:00 GMT</pubDate></item>'
       '<item><title>Older one</title><link>https://ex.example/b</link><pubDate>Sun, 01 Mar 2026 10:00:00 GMT</pubDate></item></channel></rss>')

JSONS = {'weibo': WEIBO, 'zhihu': ZHIHU, 'zhihu_daily': ZHIHU_DAILY, 'bilibili': BILI, 'toutiao': TOUTIAO, 'douyin': DOUYIN, 'juejin': JUEJIN, 'v2ex': V2EX, 'netease': NETEASE, 'hackernews': HN, 'lobsters': LOBSTERS, 'devto': DEVTO,
         'ths': THS, 'coingecko': COINGECKO, 'wikipedia': WIKIPEDIA, 'mastodon': MASTODON, 'hf_models': HF_MODELS, 'hf_papers': HF_PAPERS, 'weread_rising': WEREAD, 'openlibrary': OPENLIBRARY, 'gutenberg': GUTENDEX,
         'qqmusic': QQMUSIC, 'apple_pod_cn': APPLE, 'steam': STEAM}
TEXTS = {'github': (GITHUB, 'text/html; charset=utf-8'), 'gtrends': (GTRENDS, 'application/xml'), 'wechat': (TOPHUB_WECHAT, 'text/html; charset=utf-8'),
         'xueqiu': (TOPHUB_XUEQIU, 'text/html; charset=utf-8'), 'xiaohongshu': (XIAOHONGSHU, 'text/html; charset=utf-8'), 'producthunt': (RSS, 'application/rss+xml'), 'bbc': (RSS, 'application/rss+xml')}
FIXTURES = {**{k: (json.dumps(v), 'application/json; charset=utf-8') for k, v in JSONS.items()}, **TEXTS}
ALL = {'HOT_ENABLE': 'all'}
EVERY = [k for k in hotapi.PLATFORMS if k != 'youtube']                                       # (YouTube needs a key)


class HotBase(Base):
    def setUp(self):
        super().setUp()
        self.saved = {k: dict(v) for k, v in hotapi.PLATFORMS.items()}
        self.addCleanup(self.restore)                                                                   # (also when this setUp fails half way)
        hotapi._CACHE.clear()
        for pid, (body, ctype) in FIXTURES.items():
            self.site.put('/' + pid, body, ctype)
            hotapi.PLATFORMS[pid]['url'] = self.site.url + '/' + pid

    def restore(self):
        for k, v in self.saved.items():
            hotapi.PLATFORMS[k].update(v)
        hotapi._CACHE.clear()

    def get(self, pid, env=ALL, **q):
        query = {'p': [pid], **{k: [str(v)] for k, v in q.items()}}
        return hotapi.handle(query, env)

    def items(self, pid, env=ALL):
        st, ctype, body, cache = self.get(pid, env)
        self.assertEqual((st, ctype), (200, 'application/rss+xml; charset=utf-8'), body[:200])
        ch = ET.fromstring(body).find('channel')
        return [{x.tag: (x.text or '') for x in i} for i in ch.findall('item')], ch

    def rows(self, pid, env=ALL):
        st, ctype, body, _ = self.get(pid, env, format='json')
        self.assertEqual(st, 200, body[:200])
        return json.loads(body)


class TestParsers(HotBase):
    def test_every_platform_becomes_a_well_formed_feed_with_a_title_a_link_and_a_guid(self):
        want = {'weibo': 2, 'zhihu': 1, 'zhihu_daily': 1, 'bilibili': 1, 'toutiao': 1, 'douyin': 1, 'juejin': 1, 'v2ex': 1, 'netease': 1, 'github': 2, 'hackernews': 2, 'lobsters': 2, 'devto': 1, 'ths': 1, 'coingecko': 1,
                'wikipedia': 1, 'mastodon': 1, 'hf_models': 1, 'hf_papers': 1, 'weread_rising': 3, 'openlibrary': 1, 'gutenberg': 1, 'qqmusic': 1, 'apple_pod_cn': 1, 'steam': 3, 'gtrends': 2, 'wechat': 2, 'xueqiu': 1,
                'xiaohongshu': 2, 'producthunt': 2, 'bbc': 2}
        self.assertEqual(sorted(want), sorted(FIXTURES))                                              # (a fixture for each one that is tried)
        for pid, n in want.items():
            items, ch = self.items(pid)
            self.assertEqual(len(items), n, pid)
            self.assertEqual(ch.find('title').text, hotapi.PLATFORMS[pid]['name'] + (' · US' if hotapi.PLATFORMS[pid].get('regions') else ''))        # (a list of a country says which)
            for i in items:
                self.assertTrue(i['title'] and i['link'].startswith('https://') and i['guid'].startswith('hot:%s:' % pid), (pid, i))

    def test_what_each_platform_says(self):
        i, _ = self.items('weibo')
        self.assertEqual(i[0]['title'], '第一个话题')
        self.assertTrue(i[0]['description'].startswith('第 1 名'))
        self.assertIn('热度 136.0 万', i[0]['description'])
        self.assertIn('综艺', i[0]['description'])
        self.assertEqual(i[0]['link'], 'https://s.weibo.com/weibo?q=%23%E7%AC%AC%E4%B8%80%E4%B8%AA%E8%AF%9D%E9%A2%98%23')
        self.assertEqual(i[1]['title'], '第二个话题 & <b>')                                       # (escaped in the XML, text again once parsed)
        self.assertNotIn('官方置顶', json.dumps(i, ensure_ascii=False))                          # what the government pins is not a hot search; ads are not either
        self.assertNotIn('广告位', json.dumps(i, ensure_ascii=False))
        z, _ = self.items('zhihu')
        self.assertEqual((z[0]['link'], z[0]['description']), ('https://www.zhihu.com/question/2096358861804511510', '第 1 名 · 1094 万热度 · 327 回答 · 12 关注 — 摘要 文字'))     # only questions; the excerpt comes along
        zr = self.rows('zhihu')['items'][0]
        self.assertEqual((zr['img'], zr['body']), ('https://picx.zhimg.com/80/a.jpg', '摘要 文字'))           # a picture on http is asked for on https; no markup in the text
        b, _ = self.items('bilibili')
        self.assertEqual((b[0]['link'], b[0]['description']), ('https://www.bilibili.com/video/BV1C8nH69Ekq', '第 1 名 · 播放 57.9 万 · UP主'))
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

    def test_what_the_news_and_community_platforms_say(self):
        d, _ = self.items('douyin')
        self.assertEqual((d[0]['title'], d[0]['link'], d[0]['description']), ('抖音话题', 'https://www.douyin.com/hot/2714056', '第 1 名 · 热度 1227.4 万'))
        zd = self.rows('zhihu_daily')
        self.assertEqual((zd['ranked'], zd['items'][0]['note']), (False, 'Tom · 11 分钟阅读'))       # the daily is not a ranking, and an address that is not Zhihu Daily's is dropped
        self.assertEqual(len(zd['items']), 1)
        hn = self.rows('hackernews')['items']
        self.assertEqual((hn[0]['url'], hn[0]['alt'], hn[0]['heat'], hn[0]['note']), ('https://www.sec.gov/x.htm', 'https://news.ycombinator.com/item?id=50242939', '▲ 1,510', '1,480 条评论'))
        self.assertEqual(hn[1]['url'], 'https://news.ycombinator.com/item?id=5')                      # "Ask HN" has no address of its own: the discussion is the story
        lo = self.rows('lobsters')['items']
        self.assertEqual((lo[0]['note'], lo[0]['alt']), ('web design · 17 条评论', 'https://lobste.rs/s/olnqh2/x'))
        self.assertEqual(lo[1]['url'], 'https://lobste.rs/s/zzz')
        dv, _ = self.items('devto')
        self.assertEqual(dv[0]['description'], '第 1 名 · ♥ 20 · Ann · 7 条评论')
        mp = self.rows('mastodon')['items']
        self.assertEqual((len(mp), mp[0]['heat'], mp[0]['note']), (1, '365 人分享', 'Blogger'))      # (two days of people sharing it; an address that is not https is dropped)

    def test_what_the_third_party_lists_say(self):
        w = self.rows('wechat')
        self.assertEqual((w['via'], len(w['items'])), ('tophub.today', 2))
        self.assertEqual(w['items'][0]['url'], 'https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1#rd')    # (the entities of the page are text again)
        self.assertEqual(w['items'][0]['heat'], '热度 10.0万')
        self.assertEqual((w['items'][1]['title'], w['items'][1]['heat']), ('第三篇', ''))             # a link that is not WeChat's is dropped
        x = self.rows('xueqiu')['items']
        self.assertEqual((x[0]['url'], x[0]['note']), ('https://xueqiu.com/6180156458/413837502', '杨饭'))      # the page says http, the site answers on https
        h = self.rows('xiaohongshu')
        self.assertEqual((h['via'], [r['title'] for r in h['items']]), ('rebang.today', ['面部刮毛护理来啦~超级爽', '第二篇笔记']))        # (the same note twice is one)
        self.assertEqual(h['items'][0]['url'], 'https://www.xiaohongshu.com/explore/6ad9ea86000000002c018328?xsec_source=pc_pgy&xsec_token=QB%3D')

    def test_what_the_money_and_world_platforms_say(self):
        t, _ = self.items('ths')
        self.assertEqual((t[0]['title'], t[0]['link'], t[0]['description']), ('襄阳轴承（000678）', 'https://stockpage.10jqka.com.cn/000678/', '第 1 名 · 热度 85.6 万 · +10.03% · 减速器、农机、汽车'))
        c, _ = self.items('coingecko')
        self.assertEqual((c[0]['title'], c[0]['link'], c[0]['description']), ('Bitcoin（BTC）', 'https://www.coingecko.com/en/coins/bitcoin', '第 1 名 · 市值排名 #1'))
        w, _ = self.items('wikipedia')
        self.assertEqual((w[0]['title'], w[0]['link'], w[0]['description']), ('Lula da Silva', 'https://en.wikipedia.org/wiki/Lula_da_Silva', '第 1 名 · 浏览 27.2 万'))       # not the main page, not a search, not a project page
        g, _ = self.items('gtrends')
        self.assertEqual((g[0]['title'], g[0]['link'], g[0]['description']), ('darryn peterson', 'https://www.cbssports.com/a', "第 1 名 · 搜索 500+ · Jazz's Darryn Peterson"))
        self.assertEqual(g[1]['link'], 'https://www.google.com/search?q=no%20news')                   # no story with it: the search itself

    def test_what_the_ai_and_book_and_media_platforms_say(self):
        m = self.rows('hf_models')['items']
        self.assertEqual((len(m), m[0]['url'], m[0]['heat'], m[0]['note']), (1, 'https://huggingface.co/deepseek-ai/DeepSeek-V4', '♥ 3,209', 'text-generation · 下载 9.6 万'))   # (not a private repository)
        p = self.rows('hf_papers')['items']
        self.assertEqual((len(p), p[0]['url'], p[0]['heat'], p[0]['note']), (1, 'https://huggingface.co/papers/2610.34467', '▲ 12', 'NUS'))
        w = self.rows('weread_rising')['items']
        self.assertEqual((w[0]['title'], w[0]['url'], w[0]['heat'], w[0]['note']), ('剑来', 'https://weread.qq.com/book-detail?type=1&v=8e53', '在读 1.4 万', '烽火戏诸侯 · 推荐值 86.1%'))
        self.assertTrue(w[1]['url'].startswith('https://weread.qq.com/web/search/books?keyword='))   # no page of the book: the search of WeRead for it
        self.assertTrue(w[2]['url'].startswith('https://weread.qq.com/'))                           # an address that is not WeRead's is not used
        ol = self.rows('openlibrary')['items']
        self.assertEqual((len(ol), ol[0]['url'], ol[0]['note']), (1, 'https://openlibrary.org/works/OL17930368W', 'James Clear · 2016'))
        gu = self.rows('gutenberg')['items']
        self.assertEqual((len(gu), gu[0]['url'], gu[0]['heat'], gu[0]['note']), (1, 'https://www.gutenberg.org/ebooks/1342', '下载 190,246', 'Jane Austen'))
        q = self.rows('qqmusic')['items']
        self.assertEqual((len(q), q[0]['title'], q[0]['url']), (1, '歌 — 薛之谦', 'https://y.qq.com/n/ryqq/songDetail/004VBMk71TdUuR'))
        a = self.rows('apple_pod_cn')['items']
        self.assertEqual((len(a), a[0]['note']), (1, '声动活泼 · 商务'))
        s = self.rows('steam')['items']
        self.assertEqual([(r['heat'], r['note']) for r in s], [('¥268', ''), ('免费', ''), ('USD 19.99', '-20%')])

    def test_a_platform_that_is_a_feed_lists_the_newest_with_dates_and_no_rank(self):
        r = self.rows('producthunt')
        self.assertEqual((r['ranked'], [x['date'] for x in r['items']]), (False, ['2026-03-02', '2026-03-01']))
        self.assertEqual(r['items'][0]['url'], 'https://ex.example/a?id=1')                          # without the tracking parameter the story keeps its address
        items, _ = self.items('producthunt')
        self.assertEqual(items[0]['description'], '2026-03-02')                                      # (no "No. 1" for what is only the newest)
        self.assertEqual(self.rows('bbc')['items'][0]['url'], 'https://ex.example/a?id=1')

    def test_every_tophub_list_takes_only_links_to_its_own_sites_and_is_asked_for_less_often(self):
        ids = [k for k, v in self.saved.items() if v.get('via') == 'tophub.today']
        self.assertGreaterEqual(len(ids), 9)
        for pid in ids:
            host = self.saved[pid]['hosts'][0]
            page = ('<html><head><title>tophub</title></head><body><table><tr><td>1.</td><td><a href="https://www.%s/book/1" rel="nofollow">一本书</a></td><td class="ws">1.0万</td></tr>'
                    '<tr><td>2.</td><td><a href="https://evil.example/book/2" rel="nofollow">别的网站</a></td></tr><tr><td>3.</td><td><a href="https://www.%s/book/3">第三本</a></td></tr></table></body></html>') % (host, host)
            self.site.put('/' + pid, page, 'text/html; charset=utf-8')
            hotapi.PLATFORMS[pid]['url'] = self.site.url + '/' + pid
            r = self.rows(pid)
            self.assertEqual([x['title'] for x in r['items']], ['一本书', '第三本'], pid)
            self.assertEqual((r['via'], r['items'][0]['url']), ('tophub.today', 'https://www.%s/book/1' % host), pid)
        self.assertEqual({'qidian', 'qidian_f', 'zongheng', 'tuishujun', 'wechat', 'xueqiu', 'hupu'} - set(ids), set())
        self.assertTrue(all(hotapi.PLATFORMS[k]['ttl'] > hotapi.CACHE_SECONDS for k in ids + ['xiaohongshu']))
        self.assertNotIn('ttl', self.saved['weibo'])
        for _ in range(3):
            self.get('qidian')
        self.assertEqual(len([p for p, h in self.site.hits if p == '/qidian']), 1)
        hotapi._CACHE['qidian'] = (time.time() - hotapi.CACHE_SECONDS - 5, hotapi._CACHE['qidian'][1])         # (3 minutes old: still fresh for a list of another site)
        self.get('qidian')
        self.assertEqual(len([p for p, h in self.site.hits if p == '/qidian']), 1)
        hotapi._CACHE['qidian'] = (time.time() - hotapi.THIRD_SECONDS - 5, hotapi._CACHE['qidian'][1])
        self.get('qidian')
        self.assertEqual(len([p for p, h in self.site.hits if p == '/qidian']), 2)

    def test_the_feed_is_one_the_shelf_can_subscribe_to(self):
        self.site.put('/feed.xml', self.get('v2ex')[2].decode('utf-8'), 'application/rss+xml')
        d = feedapi.discover(self.site.url + '/feed.xml')
        self.assertEqual((d['kind'], d['title'], d['found']), ('feed', 'V2EX 今日热议', 1))
        self.assertEqual(d['items'][0]['url'], 'https://www.v2ex.com/t/1251126')

    def test_a_count_and_the_json_form(self):
        self.assertEqual(len(ET.fromstring(self.get('weibo', n=1)[2]).find('channel').findall('item')), 1)
        self.assertEqual(len(ET.fromstring(self.get('weibo', n=0)[2]).find('channel').findall('item')), 1)       # at least one
        self.assertEqual(len(ET.fromstring(self.get('weibo', n='x')[2]).find('channel').findall('item')), 2)
        d = self.rows('weibo')
        self.assertEqual((d['platform'], d['name'], d['col'], d['ranked'], d['items'][0]['title']), ('weibo', '微博热搜', 'news', True, '第一个话题'))
        self.assertAlmostEqual(d['updated'], time.time(), delta=60)                                    # when the platform was asked (the page says "updated ... ago")
        self.assertEqual(set(d['items'][0]), {'title', 'url', 'heat', 'note', 'alt', 'date'})

    def test_a_row_is_only_a_row_with_a_title_and_a_web_address(self):
        self.assertIsNone(hotapi._norm({'title': 'x', 'url': 'javascript:alert(1)'}))
        self.assertIsNone(hotapi._norm({'title': 'x', 'url': 'https://a.example/a b'}))
        self.assertIsNone(hotapi._norm({'title': '  ', 'url': 'https://a.example/'}))
        self.assertIsNone(hotapi._norm('x'))
        r = hotapi._norm({'title': ' A \n  title ', 'url': 'https://a.example/x?utm_source=a&at_medium=b&id=3', 'alt': 'javascript:1', 'heat': 'h', 'note': 'n'})
        self.assertEqual((r['title'], r['url'], r['alt']), ('A title', 'https://a.example/x?id=3', ''))
        self.assertEqual(hotapi._untrack('https://a.example/x?utm_source=a'), 'https://a.example/x')
        self.assertEqual(hotapi._untrack('https://a.example/x'), 'https://a.example/x')


class TestLanguageOfARow(unittest.TestCase):
    def test_the_title_of_a_hot_row_says_its_language_not_the_chinese_around_it(self):
        mk = lambda title, summary, lang='': agg.make_item('https://a.example/x', title, summary, lang=lang)['lang']
        self.assertEqual(mk('Anthropic S-1 filing', '第 1 名 · ▲ 1,510 · 1,480 条评论', 'en'), 'en')            # (an English list, the numbers are written in Chinese)
        self.assertEqual(mk('东鹏、蒙牛终止合作', '第 1 名 · 28886 万热度'), 'zh')
        self.assertEqual(mk('致童年の小曲', '第 2 名 · 播放 92 万'), 'zh')                                      # (one の does not make a Chinese title Japanese)
        self.assertEqual(mk('こんにちは世界', ''), 'ja')
        self.assertEqual(mk('A Chinese blog, English title', '这是一篇中文文章，讲的是库存周转率怎么算，用来做测试。'), 'zh')   # (an ordinary item: the summary still counts)


class TestWhatIsOn(HotBase):
    def test_every_list_is_on_unless_the_owner_turns_it_off_and_the_one_with_a_key_waits_for_it(self):
        self.assertEqual(hotapi.enabled({}), EVERY)
        self.assertNotIn('youtube', hotapi.enabled({}))
        self.assertIn('youtube', hotapi.enabled({'YOUTUBE_API_KEY': 'k'}))
        self.assertNotIn('youtube', hotapi.enabled({'YOUTUBE_API_KEY': '  '}))
        self.assertEqual(len(hotapi.enabled({'HOT_ENABLE': 'ALL', 'YOUTUBE_API_KEY': 'k'})), len(hotapi.PLATFORMS))

    def test_any_platform_can_be_turned_off_and_nonsense_is_ignored(self):
        self.assertEqual(hotapi.enabled({'HOT_DISABLE': 'juejin, v2ex'}), [k for k in EVERY if k not in ('juejin', 'v2ex')])
        self.assertEqual(hotapi.enabled({'HOT_ENABLE': 'all', 'HOT_DISABLE': 'weibo'}), [k for k in EVERY if k != 'weibo'])
        self.assertEqual(hotapi.enabled({'HOT_ENABLE': 'nonsense,;rm', 'HOT_DISABLE': '??'}), EVERY)
        self.assertEqual(self.get('github', env={'HOT_DISABLE': 'github'})[0], 404)
        self.assertEqual(self.get('weibo', env={})[0], 200)                                           # (the news platforms are on, as the owner decided)

    def test_the_index_says_what_is_on_by_column(self):
        st, ctype, body, _ = hotapi.handle({}, {})
        d = json.loads(body)
        self.assertEqual((st, d['service']), (200, 'shelf-hot'))
        self.assertEqual([c['id'] for c in d['columns']], ['news', 'tech', 'money', 'world', 'ai', 'play'])
        self.assertEqual([p['id'] for p in d['platforms']], EVERY)
        by = {p['id']: p for p in d['platforms']}
        self.assertEqual((by['weibo']['col'], by['weibo']['ranked'], by['zhihu_daily']['ranked'], by['producthunt']['ranked']), ('news', True, False, False))
        self.assertEqual((by['wechat']['via'], by['xiaohongshu']['via'], 'via' in by['weibo']), ('tophub.today', 'rebang.today', False))
        self.assertEqual({k for k, p in by.items() if p.get('read')} - {k for k, v in hotapi.PLATFORMS.items() if v['feed']}, {'zhihu_daily', 'devto', 'wikipedia', 'wikipedia_zh', 'juejin', 'v2ex', 'lobsters', 'hackernews', 'hf_models', 'hf_papers', 'kr36', 'mastodon'})     # what the reader may read in the page: news and blogs (feeds) and the lists whose rows are articles that were tried, not Product Hunt or the platforms' own pages
        self.assertTrue(by['theverge']['read'] and by['dw']['read'] and 'read' not in by['bbc'] and 'read' not in by['bloomberg'] and 'read' not in by['producthunt'] and 'read' not in by['weibo'] and 'read' not in by['github'])
        self.assertTrue(all(p['col'] in [c['id'] for c in d['columns']] for p in d['platforms']))
        gone = json.loads(hotapi.handle({}, {'HOT_DISABLE': ','.join(k for k, v in hotapi.PLATFORMS.items() if v['col'] == 'money')})[2])
        self.assertNotIn('money', [c['id'] for c in gone['columns']])                                 # a column with nothing in it is not offered

    def test_a_request_can_only_choose_among_the_fixed_platforms(self):
        for pid in ('../x', 'https://evil.example/', 'weibo/../../x', 'GITHUB', 'a' * 500, 'youtube'):
            self.assertEqual(self.get(pid)[0], 404, pid)
        self.assertEqual(self.site.hits, [])

    def test_every_platform_is_declared_completely(self):
        for pid, v in self.saved.items():                                                              # (as declared, not as the fixtures of these tests re-point them)
            self.assertRegex(pid, r'^[a-z0-9_]+$')
            self.assertIn(v['col'], hotapi.COLUMN_IDS, pid)
            self.assertTrue(v['name'] and v['name_en'] and v['lang'] in ('zh', 'en'), pid)
            self.assertTrue(v['home'].startswith('https://') and v['url'].startswith('https://'), pid)
            self.assertTrue(callable(v['parse']) != bool(v['feed']), pid)                             # a parser, or a feed
            self.assertEqual(v['ranked'], v['parse'] is not None and pid != 'zhihu_daily', pid)
            self.assertTrue(all(isinstance(x, str) for x in v['headers'].values()), pid)
        self.assertEqual({k for k, v in hotapi.PLATFORMS.items() if v.get('needs')}, {'youtube'})
        self.assertEqual(len({v['name'] for v in hotapi.PLATFORMS.values()}), len(hotapi.PLATFORMS))
        for c in hotapi.COLUMNS:
            self.assertGreaterEqual(len([1 for v in hotapi.PLATFORMS.values() if v['col'] == c['id']]), 5, c['id'])


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
        self.site.put('/weibo', 'not json', 'text/plain')
        self.assertEqual(self.get('weibo')[0], 502)
        self.site.put('/github', '<html>login</html>', 'text/html')
        self.assertEqual(self.get('github')[0], 502)
        self.site.put('/wechat', '<html>a login page</html>', 'text/html')
        self.assertEqual(self.get('wechat')[0], 502)
        self.site.put('/xiaohongshu', '<html>a login page</html>', 'text/html')
        self.assertEqual(self.get('xiaohongshu')[0], 502)
        self.site.put('/gtrends', '<!DOCTYPE x [<!ENTITY a "b">]><rss/>', 'application/xml')
        self.assertEqual(self.get('gtrends')[0], 502)
        self.site.put('/producthunt', 'not a feed at all', 'text/plain')
        self.assertEqual(self.get('producthunt')[0], 502)
        self.assertEqual(self.get('netease')[0], 200)                                             # one platform failing does not touch another

    def test_an_answer_of_a_shape_nobody_expected_is_a_502_not_a_crash(self):
        for pid, odd in (('douyin', '{"data": 5}'), ('hackernews', '{"hits": [1, 2, "x"]}'), ('steam', '{"top_sellers": []}'), ('ths', '{"data": {"stock_list": [[1]]}}'), ('weread_rising', '{"books": [5]}'), ('hf_papers', '{"a": 1}')):
            self.site.put('/' + pid, odd, 'application/json')
            st, _, body, _ = self.get(pid)
            self.assertEqual(st, 502, (pid, body[:120]))
            self.assertEqual(json.loads(body)['error'], 'upstream')

    def test_the_feed_is_cacheable_and_the_error_is_not(self):
        self.assertIn('s-maxage=600', self.get('v2ex')[3])
        self.assertEqual(self.get('nothing')[3], 'no-store')


class TestTheKeyedList(HotBase):
    URL = '/youtube?key=KEY123&region=%s'

    def setUp(self):
        super().setUp()
        hotapi.PLATFORMS['youtube']['url'] = self.site.url + '/youtube?key={YOUTUBE_API_KEY}&region={REGION}'

    def test_it_is_not_there_without_the_key_and_nothing_is_asked(self):
        self.assertEqual(self.get('youtube', env={})[0], 404)
        self.assertNotIn('youtube', [p['id'] for p in json.loads(hotapi.handle({}, {})[2])['platforms']])
        self.assertEqual(self.site.hits, [])

    def test_with_the_key_it_lists_the_most_popular_videos_of_the_country(self):
        self.site.put(self.URL % 'JP', json.dumps(YOUTUBE), 'application/json')
        env = {'YOUTUBE_API_KEY': 'KEY123', 'YOUTUBE_REGION': 'jp'}
        r = self.rows('youtube', env)
        self.assertEqual((len(r['items']), r['items'][0]['url'], r['items'][0]['heat'], r['items'][0]['note']), (1, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', '观看 123.5 万', 'A channel'))
        self.assertIn('youtube', [p['id'] for p in json.loads(hotapi.handle({}, env)[2])['platforms']])

    def test_the_country_is_us_unless_two_letters_are_given_and_the_key_is_quoted(self):
        self.site.put(self.URL % 'US', json.dumps(YOUTUBE), 'application/json')
        self.assertEqual(self.get('youtube', env={'YOUTUBE_API_KEY': 'KEY123'})[0], 200)
        hotapi._CACHE.clear()
        self.assertEqual(self.get('youtube', env={'YOUTUBE_API_KEY': 'KEY123', 'YOUTUBE_REGION': 'xx1;'})[0], 200)
        self.assertEqual(hotapi._url({'url': 'https://a.example/?k={YOUTUBE_API_KEY}&r={REGION}'}, {'YOUTUBE_API_KEY': 'a&b=c d'}, 1700000000), 'https://a.example/?k=a%26b%3Dc%20d&r=US')

    def test_the_key_never_leaves_in_an_error(self):
        st, _, body, _ = self.get('youtube', env={'YOUTUBE_API_KEY': 'KEY123'})                   # (no such route here: the site answers 404)
        self.assertEqual(st, 502)
        self.assertNotIn('KEY123', body.decode('utf-8'))
        self.site.put(self.URL % 'US', json.dumps(YOUTUBE), 'application/json')
        self.assertNotIn(b'KEY123', self.get('youtube', env={'YOUTUBE_API_KEY': 'KEY123'}, format='json')[2])
        self.assertNotIn(b'KEY123', self.get('youtube', env={'YOUTUBE_API_KEY': 'KEY123'})[2])


class TestCountries(HotBase):
    """A list of a country (YouTube, Google Trends): the country asked for, else the reader's address, else their language, else the owner's, else US."""
    ENV = {'YOUTUBE_API_KEY': 'K'}

    def setUp(self):
        super().setUp()
        hotapi.PLATFORMS['youtube']['url'] = self.site.url + '/youtube?region={REGION}&key={YOUTUBE_API_KEY}'
        hotapi.PLATFORMS['gtrends']['url'] = self.site.url + '/gtrends?geo={REGION}'
        for r in ('US', 'JP', 'DE', 'GB', 'KR', 'TW', 'FR', 'AT'):
            self.site.put('/youtube?region=%s&key=K' % r, json.dumps(YOUTUBE), 'application/json')
            self.site.put('/gtrends?geo=%s' % r, GTRENDS, 'application/xml')

    def ask(self, pid='youtube', env=None, headers=None, fmt=True, **q):
        query = {'p': [pid], **({'format': ['json']} if fmt else {}), **{k: [v] for k, v in q.items()}}
        return hotapi.handle(query, env if env is not None else self.ENV, headers=headers)

    def region(self, headers=None, env=None, **q):
        st, _, body, cache = self.ask(headers=headers, env=env, **q)
        self.assertEqual(st, 200, body[:200])
        return json.loads(body)['region'], cache

    def test_the_country_asked_for_wins_over_everything_and_the_answer_can_be_shared(self):
        self.assertEqual(self.region({'x-vercel-ip-country': 'DE', 'accept-language': 'ja-JP'}, region='jp'), ('JP', hotapi.CDN))     # (an answer of one fixed country is the same for everybody)
        self.assertEqual(self.region(region='KR')[0], 'KR')

    def test_auto_is_the_country_of_the_readers_address_and_is_not_shared(self):
        self.assertEqual(self.region({'x-vercel-ip-country': 'DE'}), ('DE', hotapi.PRIVATE))
        self.assertEqual(self.region({'X-Vercel-IP-Country': 'gb'}, region='auto'), ('GB', hotapi.PRIVATE))             # (any case; `auto` is the same as nothing)
        self.assertEqual(self.region({'cf-ipcountry': 'JP'})[0], 'JP')                                                    # (behind Cloudflare)
        self.assertEqual(self.region({'x-vercel-ip-country': 'DE', 'accept-language': 'ja-JP'})[0], 'DE')               # the address beats the language

    def test_without_a_usable_address_the_language_of_the_browser_decides(self):
        for lang, want in (('ja-JP,ja;q=0.9', 'JP'), ('en-GB,en;q=0.9', 'GB'), ('zh-TW,zh;q=0.9', 'TW'), ('fr', 'FR'), ('de-AT,de;q=0.8', 'AT'), ('zh-Hant', 'TW'), ('zh-CN,zh;q=0.9,en-US;q=0.8', 'US'), ('ko', 'KR')):
            self.assertEqual(self.region({'accept-language': lang})[0], want, lang)
        self.assertEqual(self.region({'x-vercel-ip-country': 'CN', 'accept-language': 'ja'})[0], 'JP')                  # China is not a country of YouTube's: the language is asked next
        self.assertEqual(self.region({'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8'})[0], 'US')                          # nothing fits: the default

    def test_then_the_owners_country_then_the_lists_own(self):
        self.assertEqual(self.region(env={**self.ENV, 'YOUTUBE_REGION': 'kr'})[0], 'KR')
        self.assertEqual(self.region({'x-vercel-ip-country': 'DE'}, env={**self.ENV, 'YOUTUBE_REGION': 'kr'})[0], 'DE')   # the reader beats the owner
        self.assertEqual(self.region(env={**self.ENV, 'YOUTUBE_REGION': 'xx1;'})[0], 'US')
        self.assertEqual(self.region()[0], 'US')

    def test_a_country_that_does_not_exist_is_the_same_as_auto(self):
        for bad in ('XX', '../x', 'jp;rm', 'a' * 200):
            self.assertEqual(self.region({'x-vercel-ip-country': 'DE'}, region=bad), ('DE', hotapi.PRIVATE), bad)

    def test_each_country_is_asked_once_in_a_while_and_kept_apart(self):
        for _ in range(3):
            self.region(region='JP')
            self.region({'x-vercel-ip-country': 'DE'})
        self.region(region='DE')
        paths = [p for p, h in self.site.hits]
        self.assertEqual(sorted(set(paths)), ['/youtube?region=DE&key=K', '/youtube?region=JP&key=K'])
        self.assertEqual(len(paths), 2)
        self.assertEqual(sorted(k for k in hotapi._CACHE if k.startswith('youtube')), ['youtube:DE', 'youtube:JP'])
        self.assertEqual(self.region(region='DE')[0], 'DE')

    def test_google_trends_is_one_list_with_countries_and_the_feed_says_which(self):
        st, ctype, body, cache = self.ask('gtrends', fmt=False, region='GB')
        ch = ET.fromstring(body).find('channel')
        self.assertEqual((st, ch.find('title').text, cache), (200, 'Google 热搜 · GB', hotapi.CDN))
        self.assertEqual([x for x in self.site.hits if x[0].startswith('/gtrends')][0][0], '/gtrends?geo=GB')
        self.assertEqual(self.region({'x-vercel-ip-country': 'JP'}, **{}) if False else json.loads(self.ask('gtrends', headers={'x-vercel-ip-country': 'FR'})[2])['region'], 'FR')
        self.assertNotIn('Google 热搜 ·', ET.fromstring(self.ask('weibo', fmt=False, region='JP')[2]).find('channel/title').text)       # (a list with no countries ignores the parameter)

    def test_a_list_with_no_countries_ignores_all_of_it_and_stays_shareable(self):
        st, _, body, cache = self.ask('weibo', headers={'x-vercel-ip-country': 'DE'}, region='JP')
        d = json.loads(body)
        self.assertEqual((st, 'region' in d, 'regions' in d, cache), (200, False, False, hotapi.CDN))

    def test_the_index_says_which_lists_have_countries(self):
        d = json.loads(hotapi.handle({}, self.ENV)[2])
        by = {p['id']: p for p in d['platforms']}
        self.assertIn('JP', by['youtube']['regions'])
        self.assertNotIn('CN', by['youtube']['regions'])
        self.assertTrue({'US', 'GB', 'JP', 'DE'} <= set(by['gtrends']['regions']))
        self.assertEqual([k for k, p in by.items() if 'regions' in p], ['gtrends', 'youtube'])
        self.assertEqual(hotapi.pick_region(hotapi.PLATFORMS['weibo'], {}, 'JP', {'x-vercel-ip-country': 'DE'}), '')
        for k in ('youtube', 'gtrends'):
            v = self.saved[k]
            self.assertIn(v['region'], v['regions'])
            self.assertIn('{REGION}', v['url'])
            self.assertEqual(len(set(v['regions'])), len(v['regions']))


class TestTheDay(unittest.TestCase):
    def test_wikipedia_is_asked_for_the_day_before_yesterday(self):
        self.assertEqual(hotapi._url(hotapi.PLATFORMS['wikipedia'], {}, 1700000000), 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/2023/11/12')
        self.assertEqual(hotapi._url(hotapi.PLATFORMS['wikipedia_zh'], {}, 1700000000), 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/zh.wikipedia/all-access/2023/11/12')
        self.assertEqual(hotapi._url({'url': 'https://a.example/x'}, {}, 0), 'https://a.example/x')


class TestTheOwnersLists(HotBase):
    def write(self, platforms):
        with open(os.path.join(self.tmp, 'registry', 'hot.json'), 'w', encoding='utf-8') as f:
            json.dump({'platforms': platforms}, f)

    def test_a_feed_a_rsshub_route_and_a_tophub_list_can_be_added(self):
        self.write([{'id': 'my-feed', 'name': '我的订阅', 'col': 'ai', 'feed': self.site.url + '/myfeed', 'home': 'https://ex.example/'},
                    {'id': 'zhihu-hot-x', 'name': '知乎热榜（RSSHub）', 'col': 'news', 'rsshub': '/zhihu/hot', 'ranked': True, 'home': 'https://www.zhihu.com/hot'},
                    {'id': 'my-tophub', 'name': '我的榜', 'name_en': 'Mine', 'col': 'play', 'tophub': 'AbCdEf12345'}])
        env = {'RSSHUB_BASE': 'https://rsshub.example.com/'}
        c = hotapi.custom(env)
        self.assertEqual(list(c), ['my-feed', 'zhihu-hot-x', 'my-tophub'])
        self.assertEqual((c['my-feed']['col'], c['my-feed']['ranked'], c['my-feed']['feed'], c['my-feed']['custom']), ('ai', False, True, True))
        self.assertEqual((c['zhihu-hot-x']['url'], c['zhihu-hot-x']['ranked'], c['zhihu-hot-x']['via']), ('https://rsshub.example.com/zhihu/hot', True, 'RSSHub'))
        self.assertEqual((c['my-feed']['read'], c['zhihu-hot-x']['read'], c['my-tophub']['read']), (True, False, False))        # a feed in time order is articles; a ranking is the platform's pages
        self.assertEqual((c['my-tophub']['url'], c['my-tophub']['via'], c['my-tophub']['name_en']), ('https://tophub.today/n/AbCdEf12345', 'tophub.today', 'Mine'))
        self.assertEqual(list(hotapi.custom({})), ['my-feed', 'my-tophub'])                           # no RSSHub of the owner's: no route of it
        self.site.put('/myfeed', RSS, 'application/rss+xml')
        r = self.rows('my-feed', env)
        self.assertEqual((r['col'], r['custom'] if 'custom' in r else None, [x['title'] for x in r['items']]), ('ai', None, ['Newest one', 'Older one']))
        idx = json.loads(hotapi.handle({}, env)[2])
        self.assertEqual([p['id'] for p in idx['platforms']][-3:], ['my-feed', 'zhihu-hot-x', 'my-tophub'])      # after the built-in ones, in the order written
        self.assertEqual(self.get('my-feed', env={'RSSHUB_BASE': '', 'HOT_DISABLE': 'my-feed'})[0], 404)

    def test_a_rsshub_list_is_asked_at_the_owners_rsshub(self):
        self.write([{'id': 'rsshub-one', 'name': 'One', 'col': 'news', 'rsshub': '/some/route'}])
        self.site.put('/some/route', RSS, 'application/rss+xml')
        self.assertEqual(self.get('rsshub-one', env={'RSSHUB_BASE': self.site.url})[0], 200)
        self.assertEqual([p for p, h in self.site.hits], ['/some/route'])

    def test_a_broken_entry_is_skipped_and_a_built_in_list_cannot_be_replaced(self):
        self.write([{'id': 'github', 'name': 'Mine', 'feed': 'https://ex.example/feed'}, {'id': 'A', 'name': 'Upper', 'feed': 'https://ex.example/feed'}, {'id': 'no-name', 'feed': 'https://ex.example/feed'},
                    {'id': 'js', 'name': 'JS', 'feed': 'javascript:alert(1)'}, {'id': 'ftp', 'name': 'FTP', 'feed': 'ftp://ex.example/feed'}, {'id': 'route-no-base', 'name': 'R', 'rsshub': '/a'},
                    {'id': 'bad-tophub', 'name': 'T', 'tophub': '../x'}, 'nonsense', {'id': 'dup', 'name': 'One', 'feed': 'https://ex.example/1'}, {'id': 'dup', 'name': 'Two', 'feed': 'https://ex.example/2'},
                    {'id': 'odd-col', 'name': 'Odd', 'col': 'nowhere', 'feed': 'https://ex.example/3', 'home': 5}])
        c = hotapi.custom({})
        self.assertEqual(list(c), ['dup', 'odd-col'])
        self.assertEqual((c['dup']['name'], c['odd-col']['col'], c['odd-col']['home']), ('One', 'news', 'https://ex.example/3'))      # (an unknown column is the first one; a home that is no address is the feed)
        self.assertEqual(hotapi.platforms({})['github']['name'], 'GitHub Trending（今日）')

    def test_no_file_or_a_broken_file_is_no_lists(self):
        self.assertEqual(hotapi.custom({}), {})
        with open(os.path.join(self.tmp, 'registry', 'hot.json'), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(hotapi.custom({}), {})
        with open(os.path.join(self.tmp, 'registry', 'hot.json'), 'w', encoding='utf-8') as f:
            f.write('[1, 2]')
        self.assertEqual(hotapi.custom({}), {})
        self.assertEqual(hotapi.enabled({}), EVERY)


class TestHttp(HotBase):
    def serve(self, path, method='GET', env=None, headers=None):
        class H:
            pass
        out = {'status': None, 'headers': {}, 'body': b''}
        h = H()
        h.path = path
        h.headers = headers or {}

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
        self.assertTrue(idx['ok'] and {'github', 'juejin'} <= {x['id'] for x in idx['platforms']} and len(idx['columns']) == 6)
        ET.fromstring(o['body'])
        for m in ('POST', 'PUT', 'DELETE'):
            self.assertEqual(self.serve('/api/hot?p=v2ex', m)['status'], 405)
        self.site.put('/youtube?region=DE&key=K', json.dumps(YOUTUBE), 'application/json')
        hotapi.PLATFORMS['youtube']['url'] = self.site.url + '/youtube?region={REGION}&key={YOUTUBE_API_KEY}'
        y = self.serve('/api/hot?p=youtube&format=json', env={'YOUTUBE_API_KEY': 'K'}, headers={'x-vercel-ip-country': 'DE'})
        self.assertEqual((y['status'], json.loads(y['body'])['region'], y['headers']['Cache-Control']), (200, 'DE', hotapi.PRIVATE))             # the address of the reader reaches the choice of the country
        j = self.serve('/api/hot?p=weibo&format=json&n=1')
        self.assertEqual((j['status'], j['headers']['Content-Type'], len(json.loads(j['body'])['items'])), (200, 'application/json; charset=utf-8', 1))


if __name__ == '__main__':
    unittest.main()
