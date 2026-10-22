"""readable: the article of a page, and the pages that are not articles. Layouts as they are found on the web (a blog, a Chinese blog platform, a messaging-platform article,
a news page with a lot of links around the story, text without paragraphs), the walls (login, subscription, bot check, scripts only), and the limits.

    python -X utf8 -m unittest discover -s tests -v
"""
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import readable as rd  # noqa: E402

URL = 'https://blog.example/2026/10/lead-time/'
EN = 'Lead time is the gap between the moment an order is placed and the moment the goods are on the shelf, and it moves with the supplier calendar. '
ZH = '库存周转率等于销货成本除以平均库存，周转天数则是它的倒数乘以三百六十五，数字越小说明货卖得越快，占用的资金也越少。'


def page(body, head='', lang='en'):
    return '<!doctype html><html lang="%s"><head><meta charset="utf-8">%s</head><body>%s</body></html>' % (lang, head, body)


def paras(text, n=6, tag='p'):
    return ''.join('<%s>%s %d</%s>' % (tag, text, i, tag) for i in range(n))


BLOG = page('''
<header class="site-header"><a href="/">My Blog</a><nav><ul><li><a href="/">Home</a></li><li><a href="/about">About</a></li><li><a href="/archive">Archive</a></li></ul></nav></header>
<div class="layout">
  <main>
    <article class="post">
      <h1 class="post-title">Lead time in one picture</h1>
      <p class="byline">By <a href="/me">Ann</a> · <time datetime="2026-10-14T08:00:00Z">Oct 14</time></p>
      <div class="entry-content">%s<figure><img src="/img/chart.png" alt="chart"><figcaption>The chart</figcaption></figure>
        <h2>Why it matters</h2>%s<pre><code>lead = order_to_shelf()</code></pre><blockquote><p>Quote of someone.</p></blockquote></div>
      <div class="share"><a href="https://twitter.com/x">Tweet</a> <a href="https://fb.com/x">Share</a></div>
    </article>
    <section id="comments" class="comments"><h3>Comments</h3>%s</section>
  </main>
  <aside class="sidebar"><h3>Recent posts</h3><ul><li><a href="/a">A</a></li><li><a href="/b">B</a></li><li><a href="/c">C</a></li></ul></aside>
</div>
<footer class="site-footer"><p>© 2026 My Blog. All rights reserved. <a href="/privacy">Privacy</a></p></footer>
''' % (paras(EN, 4), paras(EN, 4), paras('Great post, thanks for sharing it with us all.', 5)),
    head='<title>Lead time in one picture | My Blog</title><meta property="og:site_name" content="My Blog"><meta name="author" content="Ann"><meta property="article:published_time" content="2026-10-14T08:00:00Z">'
         '<meta property="og:image" content="/img/og.png">')


class TestArticles(unittest.TestCase):
    def read(self, html, url=URL):
        return rd.extract(html, url)

    def test_a_blog_post_is_found_and_the_rest_of_the_page_is_not(self):
        a = self.read(BLOG)
        c = a['content']
        self.assertIn('Lead time is the gap', c)
        self.assertIn('<h2>Why it matters</h2>', c)
        self.assertIn('<pre><code>lead = order_to_shelf()</code></pre>', c)
        self.assertIn('<figcaption>The chart</figcaption>', c)
        for out in ('Recent posts', 'All rights reserved', 'Great post, thanks', 'Tweet', 'Archive', 'Comments'):
            self.assertNotIn(out, c, out)

    def test_what_the_page_says_about_itself(self):
        a = self.read(BLOG)
        self.assertEqual((a['title'], a['site'], a['author'], a['date'], a['lang']), ('Lead time in one picture', 'My Blog', 'Ann', '2026-10-14', 'en'))
        self.assertEqual(a['image'], 'https://blog.example/img/og.png')
        self.assertTrue(a['excerpt'].startswith('Lead time is the gap'))

    def test_the_title_is_not_repeated_in_the_text_and_images_and_links_are_absolute(self):
        a = self.read(BLOG)
        self.assertNotIn('Lead time in one picture', a['content'])
        self.assertIn('src="https://blog.example/img/chart.png"', a['content'])
        self.assertNotIn('<script', a['content'])

    def test_a_chinese_blog_platform_post(self):
        html = page('<div id="top"><ul><li><a href="/">首页</a></li><li><a href="/new">新随笔</a></li><li><a href="/contact">联系</a></li></ul></div>'
                    '<div id="main"><div id="post_detail"><h1 class="postTitle"><a href="/p/1">库存周转率怎么看</a></h1><div id="cnblogs_post_body" class="blogpost-body">%s</div></div>'
                    '<div id="comment_form"><p>发表评论请先登录。</p></div></div><div id="sidebar"><h3>最新随笔</h3><ul><li><a href="/p/2">一</a></li></ul></div>' % paras(ZH, 6),
                    head='<title>库存周转率怎么看 - 某某 - 博客园</title>', lang='zh-CN')
        a = self.read(html, 'https://www.cnblogs.com/someone/p/1.html')
        self.assertEqual(a['title'], '库存周转率怎么看')
        self.assertIn('周转天数则是它的倒数', a['content'])
        self.assertNotIn('最新随笔', a['content'])
        self.assertNotIn('发表评论', a['content'])

    def test_a_messaging_platform_article_with_lazy_images_and_sections(self):
        body = ('<div id="js_article"><h1 class="rich_media_title" id="activity-name">  牛鞭效应的一个小例子  </h1><div class="rich_media_meta_list"><span id="js_name">某公众号</span><em id="publish_time">2026-10-13</em></div>'
                '<div class="rich_media_content" id="js_content" style="visibility: hidden;">' + ''.join('<section><p><span>%s</span></p></section>' % (ZH + str(i)) for i in range(6)) +
                '<p><img data-src="https://mmbiz.example/pic1.jpg" data-w="640" src="data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="></p></div></div>'
                '<div id="js_pc_qr_code" class="qr_code_pc"><p>微信扫一扫关注该公众号</p></div>')
        a = self.read(page(body, head='<title>牛鞭效应的一个小例子</title>', lang='zh-CN'), 'https://mp.weixin.example/s/abc')
        self.assertIn('周转天数', a['content'])
        self.assertIn('src="https://mmbiz.example/pic1.jpg"', a['content'])            # the picture comes from data-src; the placeholder is not used
        self.assertNotIn('微信扫一扫', a['content'])

    def test_a_news_page_with_many_links_around_the_story(self):
        links = ''.join('<li><a href="/n/%d">Another headline that is quite long number %d</a></li>' % (i, i) for i in range(30))
        html = page('<div class="top"><ul>%s</ul></div><div class="story-body"><h1>Port strike ends</h1>%s</div><div class="more"><h3>More news</h3><ul>%s</ul></div>' % (links, paras(EN, 7), links),
                    head='<title>Port strike ends - Daily News</title>')
        a = self.read(html, 'https://news.example/2026/port')
        self.assertIn('Lead time is the gap', a['content'])
        self.assertNotIn('Another headline', a['content'])
        self.assertEqual(a['title'], 'Port strike ends')

    def test_text_in_a_div_with_line_breaks_and_no_paragraphs(self):
        html = page('<div class="nav"><a href="/">home</a></div><div id="content">' + '<br><br>'.join(EN + str(i) for i in range(8)) + '</div><div class="foot">x</div>')
        a = self.read(html)
        self.assertIn('Lead time is the gap', a['content'])
        self.assertGreater(len(a['content']), 800)

    def test_a_list_and_a_table_inside_the_article_stay(self):
        html = page('<article><h1>Checklist</h1>' + paras(EN, 3) + '<ul><li>first thing to do before the call, with enough words</li><li>second thing to do before the call</li></ul>'
                    '<table><tr><th>Item</th><th>Days</th></tr><tr><td>Lead</td><td>12</td></tr></table>' + paras(EN, 3) + '</article>')
        c = self.read(html)['content']
        self.assertIn('<li>first thing to do', c)
        self.assertIn('<td>12</td>', c)

    def test_the_article_tag_alone_is_not_enough_when_the_text_is_elsewhere(self):
        html = page('<article class="teaser"><p>Short teaser only.</p></article><div class="post-content">' + paras(EN, 6) + '</div>')
        self.assertIn('Lead time is the gap', self.read(html)['content'])

    def test_hostile_text_is_made_safe(self):
        html = page('<article>' + paras(EN, 4) + '<script>alert(1)</script><p onclick="x()">Click <a href="javascript:alert(2)">here</a></p><iframe src="https://evil.example/"></iframe>'
                    '<img src="x" onerror="alert(3)"><style>p{display:none}</style></article>')
        c = self.read(html)['content']
        for bad in ('<script', 'alert', 'onclick', 'onerror', 'javascript:', '<iframe', '<style', 'evil.example'):
            self.assertNotIn(bad, c, bad)

    def test_metadata_from_structured_data_and_a_title_with_the_site_name(self):
        ld = '<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"The real headline","datePublished":"2026-09-01T10:00:00+08:00","author":{"@type":"Person","name":"Bo"},"publisher":{"@type":"Organization","name":"The Times"}}</script>'
        a = self.read(page('<article>' + paras(EN, 6) + '</article>', head='<title>The real headline – The Times</title>' + ld))
        self.assertEqual((a['title'], a['date'], a['author'], a['site']), ('The real headline', '2026-09-01', 'Bo', 'The Times'))

    def test_a_long_article_is_cut_at_the_limit_and_says_so(self):
        html = page('<article>' + paras(EN * 3, 500) + '</article>')
        a = rd.extract(html, URL, limit=20000)
        self.assertTrue(a['cut'])
        self.assertLessEqual(len(a['content']), 20300)
        self.assertEqual(a['content'].count('<p>'), a['content'].count('</p>'))


class TestWalls(unittest.TestCase):
    def code(self, html, url=URL):
        with self.assertRaises(rd.Unreadable) as cm:
            rd.extract(html, url)
        return cm.exception.code

    def test_a_login_wall(self):
        self.assertEqual(self.code(page('<div class="login-box"><h2>请先登录</h2><p>登录后查看全文。</p><form><input name="u"></form></div><footer>© 2026</footer>', head='<title>登录</title>', lang='zh-CN')), 'login')
        self.assertEqual(self.code(page('<div><h1>Sign in to continue</h1><p>Please sign in to read this story.</p></div>')), 'login')

    def test_a_login_address(self):
        self.assertEqual(self.code(page('<div><p>Welcome back.</p></div>'), 'https://accounts.example/login?next=/story'), 'login')
        self.assertEqual(self.code(page('<div><p>Welcome back.</p></div>'), 'https://passport.example/web/index'), 'login')

    def test_a_subscription_wall_after_a_teaser(self):
        teaser = '<article><h1>Big story</h1><p>' + EN * 4 + '</p><div class="paywall"><p>Subscribe to continue reading this article.</p></div></article>'
        self.assertEqual(self.code(page(teaser)), 'paywall')

    def test_a_bot_check(self):
        self.assertEqual(self.code(page('<div><h1>Just a moment...</h1><p>Checking your browser before accessing the site.</p></div>', head='<title>Just a moment...</title>')), 'blocked')
        self.assertEqual(self.code(page('<div><p>环境异常，请完成验证后继续访问。</p></div>', lang='zh-CN')), 'blocked')

    def test_a_page_drawn_by_scripts(self):
        self.assertEqual(self.code(page('<div id="root"></div><script src="/a.js"></script><script src="/b.js"></script><noscript>You need to enable JavaScript to run this app.</noscript>')), 'needs_js')

    def test_a_page_with_no_article_in_it(self):
        self.assertEqual(self.code(page('<nav><a href="/a">A</a><a href="/b">B</a></nav><p>Hello.</p>')), 'empty')
        self.assertEqual(self.code(''), 'empty')
        self.assertEqual(self.code('not html at all, just a few words'), 'empty')

    def test_a_long_article_that_mentions_logging_in_is_still_an_article(self):
        html = page('<article><h1>How to sign in safely</h1>' + paras(EN, 10) + '<p>Please sign in to your bank only on its own site.</p></article>')
        self.assertIn('Lead time', rd.extract(html, URL)['content'])

    def test_every_reason_has_words_in_both_languages(self):
        for code in rd.Unreadable.MESSAGES:
            e = rd.Unreadable(code)
            self.assertTrue(e.zh and e.en and e.code == code)


class TestLimits(unittest.TestCase):
    def test_absurd_nesting_does_not_break_it(self):
        html = page('<div>' * 8000 + '<p>' + EN * 5 + '</p>' + '</div>' * 8000)
        self.assertIn('Lead time', rd.extract(html, URL)['content'])

    def test_a_big_page_is_read_in_reasonable_time(self):
        rows = ''.join('<div class="row"><a href="/x/%d">%s %d</a></div>' % (i, 'a headline with some words in it ' * 2, i) for i in range(6000))
        html = page('<div class="listing">%s</div><article>%s</article>' % (rows, paras(EN, 12)))
        t0 = time.time()
        a = rd.extract(html, URL)
        self.assertLess(time.time() - t0, 8)
        self.assertIn('Lead time', a['content'])

    def test_garbage_in_nothing_crashes(self):
        for src in ('<', '<<<>>>', '<div', '<p>' * 5000, '\x00\x01<p>x', '<script>', '<!--', '<![CDATA[', '&#xFFFFFFF;', '<table>' * 3000, '</div></div></p>'):
            with self.assertRaises(rd.Unreadable):
                rd.extract(src, URL)


if __name__ == '__main__':
    unittest.main()
