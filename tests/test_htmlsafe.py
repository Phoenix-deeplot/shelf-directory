"""htmlsafe: somebody else's HTML becomes a small safe subset. Hostile inputs first.

    python -X utf8 -m unittest discover -s tests -v
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import htmlsafe as hs  # noqa: E402

BASE = 'https://blog.example/posts/one/'


def clean(raw, base=BASE, limit=60000):
    return hs.sanitize(raw, base, limit)[0]


from html.parser import HTMLParser  # noqa: E402


class _Names(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [k for k, v in attrs]


def tag_names(out):
    p = _Names()
    p.feed(out)
    return p.tags


def attribute_names(out):
    p = _Names()
    p.feed(out)
    return p.attrs


class TestNothingRuns(unittest.TestCase):
    def test_script_style_and_what_is_inside_them_go(self):
        out = clean('<p>a</p><script>alert(1)</script><style>p{color:red}</style><p>b</p>')
        self.assertEqual(out, '<p>a</p><p>b</p>')
        self.assertNotIn('alert', out)

    def test_frames_objects_forms_svg_math_are_dropped_with_their_content(self):
        for tag in ('iframe', 'object', 'applet', 'form', 'svg', 'math', 'canvas', 'audio', 'video', 'select', 'textarea', 'button', 'noscript', 'template', 'nav', 'aside', 'dialog'):
            out = clean('<p>keep</p><%s>INSIDE<script>x()</script></%s><p>also</p>' % (tag, tag))
            self.assertEqual(out, '<p>keep</p><p>also</p>', tag)

    def test_void_dropped_tags_do_not_swallow_the_rest(self):
        out = clean('<p>a</p><input type="text" value="x"><meta charset="utf-8"><link rel="stylesheet" href="x.css"><embed src="x.swf"><p>b</p>')
        self.assertEqual(out, '<p>a</p><p>b</p>')

    def test_self_closing_script_and_svg_do_not_swallow_the_rest(self):
        self.assertEqual(clean('<p>a</p><svg/><p>b</p><script/><p>c</p>'), '<p>a</p><p>b</p><p>c</p>')

    def test_event_handlers_and_style_attributes_never_survive(self):
        out = clean('<p onclick="x()" style="color:red" class="a" id="b" onmouseover="y()">t</p><img src="https://a.example/i.png" onerror="z()" style="x">'
                    '<a href="https://a.example/" onclick="q()">l</a><div data-x="1" contenteditable="true">d</div>')
        self.assertNotRegex(out, r'on\w+=|style=|class=|id=|data-|contenteditable')

    def test_dangerous_addresses_are_not_links(self):
        for bad in ('javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', 'java\nscript:alert(1)', ' javascript:alert(1)', '\x01javascript:alert(1)', 'data:text/html,<script>x</script>',
                    'vbscript:x', 'file:///etc/passwd', 'ftp://a.example/x', 'mailto:a@b.example', '', 'http://'):
            out = clean('<a href="%s">t</a>' % bad)
            self.assertEqual(out, '<a>t</a>', repr(bad))

    def test_a_link_to_a_place_in_the_article_becomes_a_link_to_the_original(self):
        self.assertIn('href="https://blog.example/posts/one/#top"', clean('<a href="#top">t</a>'))

    def test_dangerous_image_sources_are_dropped(self):
        for bad in ('javascript:alert(1)', 'data:image/png;base64,AAAA', 'data:image/svg+xml,<svg onload=x()>', 'file:///x.png', 'blob:https://a/x', ''):
            self.assertEqual(clean('<p>x<img src="%s">y</p>' % bad), '<p>xy</p>', repr(bad))

    def test_comments_cdata_doctype_and_processing_instructions_vanish(self):
        out = clean('<!DOCTYPE html><!-- <script>x()</script> --><p>a<![CDATA[<script>x()</script>]]></p><?php echo 1 ?><p>b</p>')
        self.assertNotIn('script', out)
        self.assertNotIn('<!--', out)
        self.assertIn('<p>b</p>', out)

    def test_entities_cannot_rebuild_a_tag(self):
        out = clean('<p>&lt;script&gt;alert(1)&lt;/script&gt; &#60;img src=x onerror=y&#62;</p>')
        self.assertNotIn('<script', out)
        self.assertNotIn('<img', out)
        self.assertIn('&lt;script&gt;', out)

    def test_attribute_values_cannot_break_out(self):
        out = clean('<a href=\'https://a.example/"><script>x()</script>\' title=\'"><b>\'>t</a><img src="https://a.example/x.png&quot; onerror=&quot;y()" alt=\'" onerror="z()\'>')
        self.assertNotIn('<script', out)
        self.assertNotIn('onerror', attribute_names(out))
        self.assertNotIn('script', tag_names(out))
        for tag in re.findall(r'<([a-z0-9]+)', out):
            self.assertIn(tag, hs.ALLOWED)

    def test_uppercase_and_odd_spacing(self):
        out = clean('<SCRIPT >x()</SCRIPT ><P ONCLICK=x>t</P  ><IMG SRC="HTTPS://A.EXAMPLE/X.PNG">')
        self.assertNotIn('x()', out)
        self.assertNotRegex(out, r'(?i)onclick')
        self.assertIn('<p>t</p>', out)
        self.assertIn('<img src="https://A.EXAMPLE/X.PNG"', out)

    def test_every_tag_in_the_output_is_on_the_list_and_attributes_are_few(self):
        hostile = ('<body onload=x()><div><marquee>m</marquee><blink>b</blink><font color=red>f</font><center>c</center><section><article><h1 id=x>h</h1><a href="https://a.example/">l</a>'
                   '<xmp><script>x()</script></xmp><plaintext><script>y()</script>')
        out = clean(hostile)
        for tag in re.findall(r'<([a-z0-9]+)', out):
            self.assertIn(tag, hs.ALLOWED)
        self.assertNotIn('<script', out)
        self.assertNotIn('onload', out)


class TestWhatIsKept(unittest.TestCase):
    def test_the_usual_text_of_an_article(self):
        raw = ('<h2>Title</h2><p>One <em>two</em> <strong>three</strong> <code>four()</code></p><ul><li>a</li><li>b</li></ul><ol start="3"><li>c</li></ol>'
               '<blockquote><p>quoted</p></blockquote><pre><code>x = 1\n  y = 2</code></pre><hr><table><thead><tr><th>h</th></tr></thead><tbody><tr><td colspan="2">d</td></tr></tbody></table>')
        out = clean(raw)
        for part in ('<h2>Title</h2>', '<em>two</em>', '<strong>three</strong>', '<code>four()</code>', '<ul><li>a</li><li>b</li></ul>', '<ol start="3">', '<blockquote><p>quoted</p></blockquote>',
                     '<pre><code>x = 1\n  y = 2</code></pre>', '<hr>', '<td colspan="2">d</td>'):
            self.assertIn(part, out)

    def test_chinese_and_symbols_and_escaping_of_text(self):
        out = clean('<p>中文 &amp; 符号 &lt;b&gt; 😀 "quote" \'q\'</p>')
        self.assertEqual(out, '<p>中文 &amp; 符号 &lt;b&gt; 😀 "quote" \'q\'</p>')

    def test_links_are_absolute_and_open_elsewhere_safely(self):
        out = clean('<a href="/about">a</a> <a href="../up">b</a> <a href="https://other.example/x?y=1&amp;z=2#f" title="T">c</a> <a href="//cdn.example/x">d</a>')
        self.assertIn('href="https://blog.example/about"', out)
        self.assertIn('href="https://blog.example/posts/up"', out)
        self.assertIn('href="https://other.example/x?y=1&amp;z=2#f"', out)
        self.assertIn('href="https://cdn.example/x"', out)
        self.assertEqual(out.count('rel="noopener noreferrer nofollow ugc"'), 4)
        self.assertEqual(out.count('target="_blank"'), 4)

    def test_images_are_https_lazy_and_without_referrer(self):
        out = clean('<img src="/a.png" alt="A"><img src="http://cdn.example/b.jpg"><img data-src="https://cdn.example/lazy.jpg" src="data:image/gif;base64,R0lGOD">'
                    '<img srcset="https://cdn.example/s1.jpg 1x, https://cdn.example/s2.jpg 2x"><img src="https://cdn.example/w.png" width="640" height="480">')
        self.assertIn('src="https://blog.example/a.png" alt="A"', out)
        self.assertIn('src="https://cdn.example/b.jpg"', out)
        self.assertIn('src="https://cdn.example/lazy.jpg"', out)
        self.assertIn('src="https://cdn.example/s1.jpg"', out)
        self.assertIn('width="640" height="480"', out)
        self.assertEqual(out.count('referrerpolicy="no-referrer"'), 5)
        self.assertEqual(out.count('loading="lazy"'), 5)

    def test_tracking_pixels_go(self):
        self.assertEqual(clean('<p>x</p><img src="https://t.example/p.gif" width="1" height="1"><img src="https://t.example/q.gif" width="0">'), '<p>x</p>')

    def test_unknown_tags_go_but_their_text_stays(self):
        self.assertEqual(clean('<section><article><font color="red">red</font> <center>c</center></article></section>'), 'red c')

    def test_text_outside_any_tag(self):
        self.assertEqual(clean('just text &amp; more'), 'just text &amp; more')


class TestStructure(unittest.TestCase):
    def test_unclosed_tags_are_closed_in_order(self):
        self.assertEqual(clean('<div><p>a<em>b<strong>c'), '<div><p>a<em>b<strong>c</strong></em></p></div>')

    def test_stray_and_crossed_end_tags(self):
        self.assertEqual(clean('</p></div>a<p>b</em></p>'), 'a<p>b</p>')
        self.assertEqual(clean('<p><em>a</p>b</em>'), '<p><em>a</em></p>b')

    def test_a_dropped_tag_closed_by_an_outer_end_tag(self):
        self.assertEqual(clean('<div><form><div>x</div></div>after'), '<div></div>after')            # everything inside the form is dropped; its unclosed end swallows the div's own
        self.assertNotIn('x', clean('<form><p>x</p>'))

    def test_deep_nesting_is_cut_not_followed(self):
        out = clean('<div>' * 500 + 'deep' + '</div>' * 500)
        self.assertLessEqual(out.count('<div>'), hs.MAX_DEPTH)
        self.assertIn('deep', out)
        self.assertEqual(out.count('<div>'), out.count('</div>'))

    def test_output_is_always_balanced(self):
        for raw in ('<p><b><i>x', '<ul><li>a<li>b</ul>', '<table><tr><td>x', '<a href="https://a.example/"><a href="https://b.example/">x', '<blockquote><p>x</blockquote></p>'):
            out = clean(raw)
            for tag in set(re.findall(r'<([a-z0-9]+)', out)) - hs.VOID:
                self.assertEqual(len(re.findall(r'<%s[ >]' % tag, out)), len(re.findall(r'</%s>' % tag, out)), (raw, out))


class TestLimits(unittest.TestCase):
    def test_it_stops_at_the_limit_and_says_so(self):
        raw = ''.join('<p>%s</p>' % ('word ' * 40) for _ in range(200))
        out, cut = hs.sanitize(raw, BASE, 5000)
        self.assertTrue(cut)
        self.assertLessEqual(len(out), 5000 + 200)                       # plus the closing tags
        self.assertEqual(out.count('<p>'), out.count('</p>'))
        out2, cut2 = hs.sanitize('<p>short</p>', BASE, 5000)
        self.assertFalse(cut2)

    def test_a_huge_input_is_fast_and_bounded(self):
        import time
        raw = '<p>' + 'x ' * 400000 + '</p>' + '<div>' * 100000
        t0 = time.time()
        out, cut = hs.sanitize(raw, BASE, 60000)
        self.assertLess(time.time() - t0, 5)
        self.assertLessEqual(len(out), 60000 + 100)
        self.assertTrue(cut)

    def test_garbage_in_nothing_breaks(self):
        for raw in ('', None, '<', '<<<>>>', '<a href=', '<p', '&', '&#xFFFFFFFF;', '\x00\x01<p>\x02a</p>', '<p>' + '&amp;' * 1000 + '</p>', b'bytes'.decode(), '<!--', '<![CDATA[', '<script>'):
            out, cut = hs.sanitize(raw, BASE, 1000)
            self.assertIsInstance(out, str)
            self.assertNotIn('\x00', out)

    def test_text_len_counts_text_not_markup(self):
        self.assertEqual(hs.text_len('<p>ab</p><img src="x"><p> c&amp;d </p>'), len('ab c&d'))
        self.assertEqual(hs.text_len(''), 0)


class TestSafeUrl(unittest.TestCase):
    def test_what_is_accepted(self):
        self.assertEqual(hs.safe_url('https://a.example/x?y=1#z'), 'https://a.example/x?y=1#z')
        self.assertEqual(hs.safe_url('HTTP://A.example/x'), 'http://A.example/x')
        self.assertEqual(hs.safe_url('/p', 'https://a.example/q/r'), 'https://a.example/p')
        self.assertEqual(hs.safe_url('http://a.example/x.png', image=True), 'https://a.example/x.png')

    def test_what_is_not(self):
        for bad in (None, '', 'x', 'javascript:1', 'https://' + 'a' * 3000, 'http://[::1', 'https://a.example\x00/x'):
            v = hs.safe_url(bad)
            self.assertTrue(v == '' or v.startswith('https://a.example'), (bad, v))
        self.assertEqual(hs.safe_url('https://a.example\x00/x'), 'https://a.example/x')                   # control characters are removed, not trusted


if __name__ == '__main__':
    unittest.main()
