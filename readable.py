"""readable.py: the article of a web page (the "reader mode" of an address): title, author, date and the text as safe html. Standard library only.

A page is parsed into a light tree; navigation, sidebars, footers, forms, ads and the like are dropped; every paragraph-like piece of text scores for the boxes around it (more
for long text and for sentences, less for boxes made of links), the best box (with the siblings that belong to it) is the article. The text then goes through htmlsafe.py,
the same allow-list that every other text from outside goes through. Pages that are not articles (a login wall, a bot check, a page drawn by scripts, a paywall, a video)
are said so, with a reason, instead of giving back whatever text happened to be there. No cookies are sent and nothing is logged in to: what a visitor without an account sees
is what is read.
"""
import html as _html
import json
import re
import urllib.parse
from html.parser import HTMLParser

import htmlsafe

MAX_NODES = 150_000
MAX_DEPTH = 120
MIN_TEXT = 200                       # characters of text below which a page is not taken for an article

VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())
SKIP_CONTENT = frozenset('script style noscript template svg math'.split())          # nothing inside them is kept
DROP = frozenset('script style noscript template iframe frame frameset object embed applet form select option optgroup textarea button input nav aside footer header '
                 'svg math canvas audio video source track dialog menu head title meta link base map area'.split())
KEEP_TAGS = frozenset('html body main article section'.split())
BLOCKISH = frozenset('address article aside blockquote body dd details dialog div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table tbody td tfoot th thead tr ul'.split())
SCORED = frozenset(('p', 'pre', 'td', 'blockquote'))

# class / id words: long ones are looked for inside the name, short ones only as a whole word
UNLIKELY_SUB = ('sidebar', 'comment', 'related', 'footer', 'sponsor', 'promo', 'newsletter', 'advert', 'social', 'share', 'breadcrumb', 'cookie', 'popup', 'modal', 'banner', 'toolbar',
                'widget', 'copyright', 'recommend', 'subscribe', 'shoutbox', 'disqus', 'pagination', 'navbar', 'topbar', 'masthead', 'outbrain', 'taboola', 'gdpr', 'reward', 'donate', 'qrcode',
                'author-box', 'authorbox', 'more-read', 'morepost', 'hotlist', 'hot-list', 'print-link', 'skip-link', 'sr-only', 'visually-hidden')
UNLIKELY_WORD = frozenset('ad ads adv nav menu tags tag meta hot login signin signup register footer header side rss toc bio'.split())
LIKELY_SUB = ('article', 'content', 'post', 'entry', 'story', 'body', 'rich_media', 'markdown', 'blog-text', 'main-text', 'detail')
POSITIVE_SUB = ('article', 'body', 'content', 'entry', 'hentry', 'main', 'post', 'text', 'blog', 'story', 'rich_media_content', 'markdown', 'detail')
NEGATIVE_SUB = ('hidden', 'banner', 'comment', 'contact', 'foot', 'footnote', 'gdpr', 'masthead', 'media', 'meta', 'outbrain', 'promo', 'related', 'scroll', 'share', 'shoutbox',
                'sidebar', 'sponsor', 'shopping', 'tags', 'tool', 'widget')
KNOWN = ('js_content', 'rich_media_content', 'cnblogs_post_body', 'content_views', 'article_content', 'post-content', 'entry-content', 'article-content', 'article__content', 'post-body',
         'markdown-body', 'show-content', 'richtext', 'post-richtext', 'articlebody', 'article-body', 'story-body', 'post__content', 'blog-content', 'text-content', 'main-content')
PUNCT = re.compile(r'[,，。.!！?？;；、:：]')

LOGIN_URL = re.compile(r'(^|/)(login|log-in|signin|sign-in|sign_in|signup|sign-up|passport|sso|auth|authorize|accounts?/login|member/login|user/login|oauth)(/|\.|\?|$)', re.I)
LOGIN_TEXT = re.compile(r'请先登录|请登录|登录后(?:查看|阅读|可见|才能)|登录查看|扫码登录|注册登录|登录[/／]注册|您还没有登录|需要登录|sign in to (?:continue|read|view)|log in to (?:continue|read|view)|please (?:sign|log) in|login required|you must be (?:logged|signed) in|create (?:a free )?account to (?:continue|read)', re.I)
PAYWALL_TEXT = re.compile(r'subscribe to (?:continue|read|unlock)|subscribers? only|already a subscriber|become a (?:member|subscriber)|this (?:article|story|content) is for (?:members|subscribers)|开通会员|会员专享|付费阅读|订阅后(?:查看|阅读)|阅读全文需|购买后(?:查看|阅读)', re.I)
CHALLENGE_TEXT = re.compile(r'just a moment|checking your browser|verify(?:ing)? you are (?:a )?human|are you a robot|attention required|access denied|captcha|security check|ddos protection|enable javascript and cookies|访问验证|安全验证|请完成验证|环境异常|去验证|人机验证|请求过于频繁|访问过于频繁|403 forbidden', re.I)
NEEDS_JS_TEXT = re.compile(r'enable javascript|you need to enable javascript|javascript is (?:required|disabled)|请开启javascript|请启用javascript|浏览器不支持|noscript', re.I)


class Unreadable(Exception):
    """The page is not something to read here. .code: login | paywall | blocked | needs_js | empty | pdf | gone | unreachable | unsupported; .zh / .en: the reason in words."""
    MESSAGES = {
        'login': ('这个页面需要登录才能看，这里读不了', 'This page needs a login to be seen, so it cannot be read here'),
        'paywall': ('这个页面只给订阅用户看，这里读不了', 'This page is for subscribers only, so it cannot be read here'),
        'blocked': ('对方的网站不让程序读取这个页面（需要验证，或者拒绝了访问）', 'The site does not let programs read this page (a check, or access refused)'),
        'needs_js': ('这个页面要靠脚本才能显示出来，这里读不了', 'This page is drawn by scripts, so it cannot be read here'),
        'empty': ('在这个页面上没有找到可以读的正文', 'No article text was found on this page'),
        'pdf': ('这是一个 PDF 文件，不能在这里当文章读', 'This is a PDF file, not an article to read here'),
        'gone': ('这个页面已经不存在了', 'This page no longer exists'),
        'unreachable': ('连不上这个网站，或者它出错了', 'The site could not be reached, or it failed'),
        'unsupported': ('这类地址不能在这里读', 'This kind of address cannot be read here'),
        'robots': ('对方网站的 robots.txt 不允许程序读取这个页面', "The site's robots.txt does not allow programs to read this page"),
    }

    def __init__(self, code):
        zh, en = self.MESSAGES.get(code, self.MESSAGES['empty'])
        super().__init__(zh)
        self.code, self.zh, self.en = code, zh, en


# ======================================================================= the tree
class Node:
    __slots__ = ('tag', 'attrs', 'parent', 'kids', 'text', 'score', 'tl', 'll', 'dead', 'imgs', 'ps')

    def __init__(self, tag, attrs=None, parent=None, text=''):
        self.tag, self.attrs, self.parent, self.kids, self.text = tag, attrs or {}, parent, [], text
        self.score, self.tl, self.ll, self.dead, self.imgs, self.ps = 0.0, 0, 0, False, 0, 0

    def name(self):
        return ((self.attrs.get('class') or '') + ' ' + (self.attrs.get('id') or '')).lower()


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.cur, self.n, self.skip, self.depth = self.root, 0, [], 0
        self.meta, self.title, self.lang, self.ld = {}, '', '', []
        self._in_title = self._in_ld = False
        self._ld_buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'html':
            self.lang = a.get('lang', '') or a.get('xml:lang', '')
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
            if key and 'content' in a:
                self.meta.setdefault(key, a['content'].strip())
        if tag == 'title' and not self.title:
            self._in_title = True
        if tag == 'script' and 'ld+json' in a.get('type', '').lower():
            self._in_ld, self._ld_buf = True, []
        if self.skip:
            if tag in SKIP_CONTENT and tag not in VOID:
                self.skip.append(tag)
            return
        if tag in SKIP_CONTENT and tag not in VOID:
            self.skip.append(tag)
            return
        if self.n > MAX_NODES:
            return
        if self.depth >= MAX_DEPTH and tag not in VOID:                    # deeper than any real page: the tag is dropped, its text stays in the box above
            return
        self._implied(tag)
        node = Node(tag, a, self.cur)
        self.cur.kids.append(node)
        self.n += 1
        if tag not in VOID:
            self.cur, self.depth = node, self.depth + 1

    def _implied(self, tag):
        c = self.cur
        if c.tag == 'p' and tag in BLOCKISH:
            self._pop()
        elif c.tag == 'li' and tag == 'li':
            self._pop()
        elif c.tag in ('td', 'th') and tag in ('td', 'th', 'tr'):
            self._pop()
            if tag == 'tr' and self.cur.tag == 'tr':
                self._pop()
        elif c.tag == 'tr' and tag == 'tr':
            self._pop()
        elif c.tag in ('dt', 'dd') and tag in ('dt', 'dd'):
            self._pop()

    def _pop(self):
        if self.cur.parent is not None:
            self.cur, self.depth = self.cur.parent, max(0, self.depth - 1)

    def handle_startendtag(self, tag, attrs):
        if tag in SKIP_CONTENT:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.cur.tag == tag and self.cur.parent is not None:
            self._pop()

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        if tag == 'script' and self._in_ld:
            self._in_ld = False
            try:
                self.ld.append(json.loads(''.join(self._ld_buf)))
            except ValueError:
                pass
        if self.skip:
            if self.skip[-1] == tag:
                self.skip.pop()
            elif tag in self.skip:
                while self.skip and self.skip.pop() != tag:
                    pass
            return
        if tag in VOID:
            return
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent
            d, x = 0, self.cur
            while x.parent is not None:
                d, x = d + 1, x.parent
            self.depth = d

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld:
            self._ld_buf.append(data)
        if self.skip or self.n > MAX_NODES or not data:
            return
        if data.strip() or self.cur.tag == 'pre':
            self.cur.kids.append(Node(None, parent=self.cur, text=data))
            self.n += 1
        elif self.cur.kids and self.cur.kids[-1].tag is not None:
            self.cur.kids.append(Node(None, parent=self.cur, text=' '))


def _walk(root):
    """Nodes in document order, without recursion (pages can be nested absurdly deep)."""
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.kids))


def _measure(root):
    order = list(_walk(root))
    for n in reversed(order):                                    # children before parents
        if n.tag is None:
            n.tl = len(re.sub(r'\s+', ' ', n.text).strip()) if n.text.strip() else 0
            n.ll = 0
            continue
        tl = ll = imgs = ps = 0
        for k in n.kids:
            if k.dead:
                continue
            tl += k.tl
            ll += k.ll
            imgs += k.imgs + (1 if k.tag == 'img' else 0)
            ps += k.ps + (1 if k.tag == 'p' else 0)
        n.tl, n.ll, n.imgs, n.ps = tl, (tl if n.tag == 'a' else ll), imgs, ps
    return order


def _hidden(n):
    a = n.attrs
    if 'hidden' in a or a.get('aria-hidden', '').lower() == 'true':
        return True
    return bool(re.search(r'display\s*:\s*none', a.get('style', ''), re.I))                   # (visibility:hidden is how some platforms hold an article until a script shows it)


def _words(name):
    return set(re.split(r'[^a-z0-9]+', name))


def _unlikely(n):
    if n.tag in KEEP_TAGS or not (n.attrs.get('class') or n.attrs.get('id')):
        return False
    name = n.name()
    if any(k in name for k in LIKELY_SUB):
        return False
    return any(s in name for s in UNLIKELY_SUB) or bool(_words(name) & UNLIKELY_WORD)


def _weight(n):
    w = 0
    for v in (n.attrs.get('class') or '', n.attrs.get('id') or ''):
        v = v.lower()
        if not v:
            continue
        if any(s in v for s in NEGATIVE_SUB):
            w -= 25
        if any(s in v for s in POSITIVE_SUB):
            w += 25
    return w


def _init_score(n):
    s = _weight(n)
    if n.tag == 'div':
        s += 5
    elif n.tag in ('pre', 'td', 'blockquote'):
        s += 3
    elif n.tag in ('address', 'ol', 'ul', 'dl', 'dd', 'dt', 'li', 'form'):
        s -= 3
    elif n.tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'th'):
        s -= 5
    name = n.name()
    if any(k in name for k in KNOWN):
        s += 30
    return s


def _density(n):
    return n.ll / n.tl if n.tl else 1.0


def _clean_dead(root):
    for n in _walk(root):
        if n.tag is None or n.dead:
            continue
        if n.tag in DROP and n.tag not in KEEP_TAGS or _hidden(n) or _unlikely(n):
            n.dead = True
        elif n.tag == 'footer' or n.tag == 'header':
            n.dead = True


def _pick(root):
    """The box that holds the article, with the siblings that belong to it -> a list of nodes, or []."""
    paras = []
    for n in _walk(root):
        if n.dead or n.tag is None:
            continue
        # a node inside a dead one is dead as well
        p = n.parent
        if p is not None and p.dead:
            n.dead = True
            continue
        if n.tag in SCORED or (n.tag in ('div', 'section', 'li') and not any(k.tag in BLOCKISH and not k.dead for k in n.kids if k.tag)):
            paras.append(n)
    _measure(root)
    scored = []
    for n in paras:
        if n.tl < 25:
            continue
        par = n.parent
        gp = par.parent if par is not None else None
        if par is None or par.dead:
            continue
        for x in (par, gp):
            if x is not None and not x.score and x.tag not in (None, 'root'):
                x.score = _init_score(x)
        pts = 1 + len(PUNCT.findall(' '.join(k.text for k in n.kids if k.tag is None)[:2000])) + min(n.tl // 100, 3)
        par.score += pts
        if gp is not None and gp.tag != 'root':
            gp.score += pts / 2
        scored += [x for x in (par, gp) if x is not None and x.tag != 'root']
    best, best_score = None, 0.0
    for x in set(scored):
        if x.dead or x.tl < MIN_TEXT // 2:
            continue
        s = x.score * (1 - _density(x))
        if s > best_score:
            best, best_score = x, s
    if best is None:
        return []
    # a box that holds a single, bigger box of its own: the article is the inner one only when the outer adds nothing
    parent = best.parent
    keep = [best]
    if parent is not None and parent.tag not in (None, 'root'):
        thr = max(10.0, best_score * 0.2)
        keep = []
        for sib in parent.kids:
            if sib is best:
                keep.append(sib)
            elif sib.dead or sib.tag is None:
                continue
            elif sib.score and sib.score * (1 - _density(sib)) >= thr:
                keep.append(sib)
            elif sib.tag == 'p' and ((sib.tl > 80 and _density(sib) < 0.25) or (0 < sib.tl <= 80 and sib.ll == 0 and re.search(r'[.。！？!?]\s*$', _text_of(sib)))):
                keep.append(sib)
    return keep


def _text_of(n):
    return re.sub(r'\s+', ' ', ''.join(x.text for x in _walk(n) if x.tag is None and not _dead_chain(x))).strip()


def _dead_chain(n):
    while n is not None:
        if n.dead:
            return True
        n = n.parent
    return False


def _prune(boxes, title):
    """Inside the article: boxes that are mostly links, forms, share rows, empty ones and the title repeated."""
    seen_h1 = False
    for box in boxes:
        for n in list(_walk(box)):
            if n is box or n.tag is None or n.dead:
                continue
            if n.tag == 'h1' and not seen_h1 and title and _same(_text_of(n), title):
                n.dead, seen_h1 = True, True
                continue
            if n.tag in ('div', 'section', 'ul', 'ol', 'table', 'dl', 'span', 'p') and n.tl:
                d = _density(n)
                if (d > 0.6 and n.tl < 500 and n.imgs < 3) or (n.tag in ('ul', 'ol') and d > 0.35 and n.tl < 1000):
                    n.dead = True
                    continue
            if n.tag in ('div', 'section', 'p', 'span') and n.tl == 0 and n.imgs == 0 and not any(k.tag in ('img', 'br', 'hr') for k in n.kids if not k.dead):
                n.dead = True
    # (measures are stale after pruning; they are only used to decide what to drop)


def _same(a, b):
    f = lambda s: re.sub(r'[\W_]+', '', s.lower())
    a, b = f(a), f(b)
    return bool(a) and bool(b) and (a == b or a in b or b in a)


_KEEP_ATTRS = {'a': ('href', 'title'), 'img': ('src', 'data-src', 'data-original', 'data-lazy-src', 'data-actualsrc', 'srcset', 'alt', 'title', 'width', 'height'),
               'td': ('colspan', 'rowspan'), 'th': ('colspan', 'rowspan'), 'ol': ('start',)}


def _serialize(n, out):
    if n.dead:
        return
    if n.tag is None:
        out.append(_html.escape(n.text, quote=False))
        return
    if n.tag in ('root', 'html', 'body'):
        for k in n.kids:
            _serialize(k, out)
        return
    attrs = ''.join(' %s="%s"' % (k, _html.escape(n.attrs[k])) for k in _KEEP_ATTRS.get(n.tag, ()) if k in n.attrs)
    if n.tag in VOID:
        out.append('<%s%s>' % (n.tag, attrs))
        return
    out.append('<%s%s>' % (n.tag, attrs))
    stack = [(k, False) for k in reversed(n.kids)]
    # (children are written recursively but the tree is at most MAX_DEPTH deep)
    for k in reversed([kk for kk in stack]):
        pass
    for k in n.kids:
        _serialize(k, out)
    out.append('</%s>' % n.tag)


# ======================================================================= what the page says about itself
def _first(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return re.sub(r'\s+', ' ', _html.unescape(v)).strip()
    return ''


def _ld_article(ld):
    out = {}
    stack = list(ld) if isinstance(ld, list) else [ld]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, dict):
            t = x.get('@type')
            ts = t if isinstance(t, list) else [t]
            if any(str(y) in ('Article', 'NewsArticle', 'BlogPosting', 'TechArticle', 'ScholarlyArticle', 'Report') for y in ts):
                out = out or x
            if '@graph' in x:
                stack.extend(x['@graph'] if isinstance(x['@graph'], list) else [x['@graph']])
    return out


def _clean_title(title, site):
    t = re.sub(r'\s+', ' ', title).strip()
    if not t:
        return ''
    parts = [p.strip() for p in re.split(r'\s+[|｜\-–—·•»_]\s+|[｜|]', t) if p.strip()]
    if len(parts) > 1:
        if site:
            kept = [p for p in parts if not _same(p, site) or len(p) > len(site) + 8]
            parts = kept or parts
        if len(parts) > 1:
            return max(parts, key=len)
    return parts[0] if parts else t


def _date(*vals):
    for v in vals:
        m = re.match(r'\s*(\d{4})-(\d{2})-(\d{2})', str(v or ''))
        if m:
            return '%s-%s-%s' % m.groups()
    return ''


def extract(source, url, limit=150000):
    """-> dict(title, site, author, date, lang, image, excerpt, content, cut) of an article, or raises Unreadable.

    `source` is the page's html as text, `url` its final address (relative links and images are made absolute against it).
    """
    tree = _Tree()
    try:
        tree.feed(source)
        tree.close()
    except Exception:
        pass
    root, meta = tree.root, tree.meta
    ld = _ld_article([x for x in tree.ld if x])
    site = _first(meta.get('og:site_name'), (ld.get('publisher') or {}).get('name') if isinstance(ld.get('publisher'), dict) else '', urllib.parse.urlsplit(url).hostname or '')
    h1 = next((n for n in _walk(root) if n.tag == 'h1' and _text_of(n)), None)
    title = _clean_title(_first(meta.get('og:title'), meta.get('twitter:title'), ld.get('headline'), tree.title, _text_of(h1) if h1 else ''), site)
    author = _first(meta.get('author'), meta.get('article:author'), (ld.get('author') or {}).get('name') if isinstance(ld.get('author'), dict) else (ld.get('author') if isinstance(ld.get('author'), str) else ''))
    if re.match(r'https?://', author):
        author = ''
    date = _date(meta.get('article:published_time'), meta.get('datepublished'), meta.get('og:published_time'), meta.get('date'), ld.get('datePublished'), meta.get('pubdate'))
    if not date:
        t = next((n.attrs.get('datetime') for n in _walk(root) if n.tag == 'time' and n.attrs.get('datetime')), '')
        date = _date(t)
    image = urllib.parse.urljoin(url, meta.get('og:image') or '') if meta.get('og:image') else ''

    _clean_dead(root)
    boxes = _pick(root)
    page_text = re.sub(r'\s+', ' ', ''.join(x.text for x in _walk(root) if x.tag is None)).strip()           # everything the page says (the walls live in the boxes that were dropped)
    if not boxes:
        raise _why(page_text, url, title, source, 0)
    _prune(boxes, title)
    parts = []
    for b in boxes:
        _serialize(b, parts)
    safe, cut = htmlsafe.sanitize(''.join(parts), url, limit)
    n = htmlsafe.text_len(safe)
    if n < MIN_TEXT:
        raise _why(page_text, url, title, source, n)
    # a page that shows a few lines and then asks for a login or a subscription is that, not an article
    plain = re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]*>', ' ', safe))).strip()
    if n < 1000 and (PAYWALL_TEXT.search(page_text[:8000]) or LOGIN_TEXT.search(plain)):                     # (a login box elsewhere on a page says nothing about the article; the article's own words do)
        raise Unreadable('paywall' if PAYWALL_TEXT.search(page_text[:8000]) else 'login')
    excerpt = plain[:200]
    return {'title': title, 'site': site, 'author': author[:120], 'date': date, 'lang': (tree.lang or '')[:12], 'image': image if image.startswith('https://') else '',
            'excerpt': excerpt, 'content': safe, 'cut': cut}


def _why(page_text, url, title, source, got):
    """Not an article: which kind of not."""
    p = urllib.parse.urlsplit(url)
    head = (title + ' ' + page_text[:4000])
    if LOGIN_URL.search(p.path or '') or LOGIN_URL.search(p.netloc.split('.')[0]):
        return Unreadable('login')
    if CHALLENGE_TEXT.search(head) and len(page_text) < 3000:
        return Unreadable('blocked')
    if LOGIN_TEXT.search(head) and len(page_text) < 4000:
        return Unreadable('login')
    if PAYWALL_TEXT.search(head):
        return Unreadable('paywall')
    if len(page_text) < 400 and (NEEDS_JS_TEXT.search(source[:200000]) or len(re.findall(r'<script', source, re.I)) >= 3 or re.search(r'<div[^>]+id=["\'](?:root|app|__next|__nuxt)["\']', source, re.I)):
        return Unreadable('needs_js')
    return Unreadable('empty')
