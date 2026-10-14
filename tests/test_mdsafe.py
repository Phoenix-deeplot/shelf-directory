"""mdsafe: what strangers write must never become markup, script, a tracking pixel, or a slow request.

    python -X utf8 -m unittest discover -s tests -v
"""
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import mdsafe  # noqa: E402


class TestMdSafe(unittest.TestCase):
    def test_basic_blocks(self):
        h = mdsafe.render('# 标题\n\n第一段 **粗体** 和 *斜体* 和 `代码`。\n\n- 一\n- 二\n  - 二之一\n\n1. a\n2. b\n\n> 引用\n\n---\n\n| a | b |\n|---|--:|\n| 1 | 2 |\n')
        for part in ('<h1>标题</h1>', '<strong>粗体</strong>', '<em>斜体</em>', '<code>代码</code>', '<ul>', '<ol>', '<blockquote>', '<hr>', '<table>', 'text-align:right'):
            self.assertIn(part, h)

    def test_raw_html_is_text(self):
        for evil in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '<iframe src="//evil"></iframe>', '<a href="javascript:alert(1)">x</a>',
                     '<style>body{display:none}</style>', '<svg onload=alert(1)>', '<!-- --><b onmouseover=alert(1)>x</b>'):
            h = mdsafe.render('before\n\n' + evil + '\n\nafter')
            self.assertNotRegex(h, r'<(script|img|iframe|style|svg|b)\b', evil)
            self.assertIn('&lt;', h)

    def test_only_http_links_and_they_are_marked(self):
        h = mdsafe.render('[ok](https://example.com/a?x=1&y=2) [js](javascript:alert(1)) [data](data:text/html;base64,AAAA) [mail](mailto:a@b.c) [rel](/etc/passwd) [rel2](page.html) [ftp](ftp://x/y)')
        self.assertEqual(len(re.findall(r'<a ', h)), 1)
        self.assertIn('href="https://example.com/a?x=1&amp;y=2" rel="nofollow ugc noopener noreferrer" target="_blank">ok</a>', h)
        for text in ('js', 'data', 'mail', 'rel', 'rel2', 'ftp'):
            self.assertIn(text, h)                                              # the words stay, the link goes
        self.assertNotIn('javascript:', h)

    def test_autolink_and_title_attribute(self):
        h = mdsafe.render('<https://example.com/x> and [t](https://a.example "title \\" onmouseover=\\"x") and [u](https://b.example "fine title")')
        self.assertIn('<a href="https://example.com/x"', h)
        self.assertIn('>u</a>', h)                                               # a normal link title is accepted and dropped
        self.assertNotRegex(h, r'<[^>]*\sonmouseover=')                          # a quote trick cannot add an attribute to any tag

    def test_images_are_never_embedded(self):
        h = mdsafe.render('![cat](https://tracker.example/pixel.gif) ![x](javascript:alert(1)) ![](https://a.example/b.png) <img src="https://t.example/p.gif">')
        self.assertNotIn('<img', h)
        self.assertIn('[图片] cat', h)
        self.assertIn('href="https://tracker.example/pixel.gif"', h)             # a link, which only a click follows
        self.assertNotIn('javascript:', h)

    def test_code_is_escaped(self):
        h = mdsafe.render('```html\n<script>alert("x")</script>\n```\n\n`<b>`')
        self.assertIn('&lt;script&gt;', h)
        self.assertNotIn('<script', h)
        self.assertIn('class="language-html"', h)
        self.assertNotIn('<b>', h)

    def test_language_hint_cannot_break_out(self):
        h = mdsafe.render('```a"><script>x</script>\ncode\n```')
        self.assertNotIn('<script', h)

    def test_entities_and_nul(self):
        h = mdsafe.render('a &amp; b &lt;script&gt; \x00 \x00 1 \x00 [x](https://a.example)')
        self.assertNotIn('\x00', h)
        self.assertIn('&amp;amp;', h)                                           # shown as the text the author typed, not decoded into markup

    def test_deep_nesting_falls_back_to_plain_text(self):
        t0 = time.time()
        h = mdsafe.render('>' * 3000 + ' deep')
        self.assertTrue(h.startswith('<pre>') or h.count('<blockquote>') <= mdsafe.DEPTH_MAX + 1)
        h = mdsafe.render('\n'.join('  ' * i + '- x' for i in range(400)))
        self.assertLess(time.time() - t0, 3)
        self.assertIn('x', h)

    def test_hostile_text_is_fast(self):
        samples = ['`' * 20000, '*' * 20000, '_' * 20000, '[' * 5000 + ']' * 5000, '[a](' * 3000, '![' * 3000, ('**a ' * 6000), '~~' * 8000, '<' * 10000, '| a ' * 5000 + '\n' + '|---' * 5000,
                   '\\' * 20000, ('a`b`' * 5000), '1. ' * 10000]
        for s in samples:
            t0 = time.time()
            mdsafe.render(s)
            self.assertLess(time.time() - t0, 2.5, repr(s[:30]))

    def test_very_long_line_is_cut(self):
        h = mdsafe.render('x' * 100000)
        self.assertLess(len(h), mdsafe.LINE_MAX + 20)

    def test_plain_text(self):
        t = mdsafe.plain_text('# T\n\nSome **bold** [link](https://a.example) text.\n\n```\ncode\n```\n\n- item\n\n![i](x.png)')
        self.assertEqual(t, 'T Some bold link text. item')


if __name__ == '__main__':
    unittest.main()
