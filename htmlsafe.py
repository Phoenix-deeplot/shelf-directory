"""htmlsafe.py: HTML written by somebody else -> a small, safe subset of it (the text of an article, for the reader of a feed).

The update service (feedapi.py) hands the readers of the site the text of an article when the feed carries it. That text is somebody else's HTML, so it is rebuilt here
from an allow-list and never passed on as it came: only these tags, only these attributes with their values checked, every address http(s), every tag closed in the order
it was opened, nothing that runs (script, style, frames, forms, svg, event handlers, javascript: / data: addresses ...). The page that shows it cleans it once more (DOMPurify)
and puts it in a document that allows no script at all; this file is the first of those walls, not the only one.

Standard library only.
"""
import html
import re
import urllib.parse
from html.parser import HTMLParser

ALLOWED = frozenset('p br hr h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code em strong b i u s del ins sub sup small mark a img figure figcaption table thead tbody tfoot tr th td caption '
                    'dl dt dd div span abbr cite q kbd samp var details summary'.split())
VOID = frozenset(('br', 'hr', 'img'))
NOEND = frozenset('source track input meta link base area embed param wbr col frame br hr img'.split())      # tags that have no end tag: nothing to wait for after them
DROP = frozenset('script style noscript template iframe frame frameset object embed applet svg math canvas audio video source track form select option optgroup textarea button input '
                 'head title meta link base nav aside map area dialog'.split())           # dropped together with what is inside them
LAZY_SRC = ('data-src', 'data-original', 'data-lazy-src', 'data-lazy', 'data-actualsrc')
MAX_DEPTH = 30
MAX_URL = 2000
_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_SPACE = re.compile(r'[\x00-\x20\x7f]+')


def safe_url(value, base='', image=False):
    """The address as an absolute http(s) address, or ''. (An image on plain http is asked for on https: a page on https may not show the other kind.)"""
    v = _SPACE.sub('', str(value or ''))                      # "java\nscript:" and friends: no control character or space may stay inside an address
    if not v or len(v) > MAX_URL:
        return ''
    try:
        full = urllib.parse.urljoin(base, v) if base else v
        p = urllib.parse.urlsplit(full)
    except ValueError:
        return ''
    if p.scheme.lower() not in ('http', 'https') or not p.hostname:
        return ''
    scheme = 'https' if image else p.scheme.lower()
    return urllib.parse.urlunsplit((scheme, p.netloc, p.path, p.query, p.fragment))


def _int(v, hi):
    m = re.match(r'\s*(\d{1,6})\s*(px)?\s*$', str(v or ''))
    return min(int(m.group(1)), hi) if m else 0


class _Safe(HTMLParser):
    def __init__(self, base, limit):
        super().__init__(convert_charrefs=True)
        self.base, self.limit = base, limit
        self.out, self.stack, self.drop = [], [], []
        self.size, self.cut = 0, False

    # ---- output
    def emit(self, s, force=False):
        """True when it was written. Closing tags are always written (the argument force): a tag that was opened is closed, the limit or not."""
        if not force:
            if self.cut:
                return False
            if self.size + len(s) > self.limit:
                self.cut = True
                return False
        self.size += len(s)
        self.out.append(s)
        return True

    def attrs(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        out = []
        if tag == 'a':
            href = safe_url(a.get('href'), self.base)
            if href:
                out.append(' href="%s" target="_blank" rel="noopener noreferrer nofollow ugc"' % html.escape(href))
            if a.get('title'):
                out.append(' title="%s"' % html.escape(a['title'][:200]))
        elif tag == 'img':
            src = ''
            for k in ('src',) + LAZY_SRC:
                src = safe_url(a.get(k), self.base, image=True)
                if src and not src.startswith('data:'):
                    break
            if not src and a.get('srcset'):
                src = safe_url(a['srcset'].split(',')[0].strip().split(' ')[0], self.base, image=True)
            if not src:
                return None
            w, h = _int(a.get('width'), 5000), _int(a.get('height'), 5000)
            if (a.get('width') and w <= 2) or (a.get('height') and h <= 2):          # a tracking pixel
                return None
            out.append(' src="%s" alt="%s" loading="lazy" decoding="async" referrerpolicy="no-referrer"' % (html.escape(src), html.escape(a.get('alt', '')[:300])))
            if a.get('title'):
                out.append(' title="%s"' % html.escape(a['title'][:200]))
            if w and h:
                out.append(' width="%d" height="%d"' % (w, h))
        elif tag in ('td', 'th'):
            for k in ('colspan', 'rowspan'):
                n = _int(a.get(k), 30)
                if n > 1:
                    out.append(' %s="%d"' % (k, n))
        elif tag == 'ol':
            n = _int(a.get('start'), 100000)
            if n > 1:
                out.append(' start="%d"' % n)
        return ''.join(out)

    # ---- parser events
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.drop:
            if tag in DROP and tag not in NOEND:
                self.drop.append(tag)
            return
        if tag in DROP:
            if tag not in NOEND:
                self.drop.append(tag)
            return
        if tag not in ALLOWED or len(self.stack) >= MAX_DEPTH:
            return                                                                               # the tag goes, what is inside it stays
        if self.cut:
            return
        extra = self.attrs(tag, attrs)
        if extra is None:
            return
        if self.emit('<%s%s>' % (tag, extra)) and tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.drop or tag in DROP:                                  # <svg/> <script/>: nothing is kept, and nothing is waited for
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID and tag in ALLOWED and not self.drop and self.stack and self.stack[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop:
            if self.drop[-1] == tag:
                self.drop.pop()
            elif tag in self.drop:                                   # an end tag that closes something further out: everything in between ends too
                while self.drop and self.drop.pop() != tag:
                    pass
                return
            elif tag in self.stack:                                  # it closes an element that is open outside the dropped part: the dropped part ends with it
                self.drop = []
            else:
                return
        if tag in VOID or tag not in ALLOWED or tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.emit('</%s>' % t, force=True)
            if t == tag:
                break

    def handle_data(self, data):
        if self.drop or not data:
            return
        self.emit(html.escape(_CTRL.sub('', data), quote=False))

    def result(self):
        while self.stack:
            self.emit('</%s>' % self.stack.pop(), force=True)
        return ''.join(self.out)


def sanitize(raw, base='', limit=60000):
    """-> (safe html, cut): `cut` is True when the text was longer than `limit` characters and the rest was left out."""
    p = _Safe(base, limit)
    try:
        p.feed(str(raw or ''))
        p.close()
    except Exception:
        pass                                                                                     # what could be read before the break is what is kept
    return p.result(), p.cut


def text_len(safe_html):
    """How much text (not markup) a piece of safe html holds."""
    return len(re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', safe_html))).strip())
