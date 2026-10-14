#!/usr/bin/env python3
"""aggregate.py: the aggregator behind the public shelf directory. Python 3.9+, standard library only.

  python aggregate.py probe URL                      what is this address, and can it be listed? (writes nothing)
  python aggregate.py issue --event FILE --out FILE  check a submission issue (used by the GitHub Action)
  python aggregate.py approve URL [--tags a,b]       register a checked address (what the "approved" label does)
  python aggregate.py approve --event FILE --out FILE
  python aggregate.py remove ID                      take a shelf out of the directory
  python aggregate.py fetch [--only ID]              read every registered source into registry/snapshots/
  python aggregate.py build                          write docs/ (directory.json, feeds, shelves.opml) from the snapshots
  python aggregate.py run                            fetch + build (the daily job)
  python aggregate.py serve [--port 8770]            preview docs/ on this computer, with CORS, for the app's directory mode

Only metadata is listed: title, a short summary, cover address, date, tags and the link back. Nothing is copied.
"""
import argparse
import datetime
import email.utils
import hashlib
import html
import http.client
import http.server
import ipaddress
import json
import os
import posixpath
import re
import shutil
import socket
import ssl
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from xml.sax.saxutils import escape as xml_escape

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---- limits: everything fetched from the internet is untrusted and bounded
UA_TOKEN = 'ShelfDirectoryBot'
UA = UA_TOKEN + '/1 (link directory; reads public feeds and page metadata only)'
MAX_BYTES = 2 * 1024 * 1024        # one response
TIMEOUT = 15                       # seconds, per network operation
MAX_REDIRECTS = 5
MAX_ITEMS = 500                    # items read from one source
FEED_KEEP = 50                     # newest items kept from a feed
REPO_FILES = 500                   # files listed from one GitHub repository
REPO_READ = 30                     # of those, files whose first lines are read for a title and a summary
SUMMARY = 160
TITLE = 140
TAGS_PER_ITEM = 8
DOWN_AFTER = 7                     # failed runs in a row before a shelf is hidden from the directory
MAX_DIRECTORY_ITEMS = 5000
MAX_TAG_FEEDS = 200
ALLOW_LOCAL = False                # tests only: lets http:// and 127.0.0.1 through. Never set it in the Action.
GITHUB_API = 'https://api.github.com'
GITHUB_RAW = 'https://raw.githubusercontent.com'

PARSER = 2                         # raise it whenever what is read from a source changes: snapshots of older rules are then read again, not "not modified"
TYPES = ('article', 'note', 'video', 'audio', 'pdf', 'tool')
VIDEO_HOSTS = ('youtube.com', 'youtu.be', 'bilibili.com', 'vimeo.com')
COMMON_FEEDS = ('/feed.xml', '/atom.xml', '/rss.xml', '/index.xml', '/feed', '/rss', '/feed.json')
REPO_EXT = ('.md', '.markdown', '.html', '.htm', '.pdf')
EXCLUDE_DIRS = {'node_modules', 'vendor', 'third_party', 'dist', 'build', '_site', 'site-packages', 'venv', 'themes', 'test', 'tests', '__pycache__'}
EXCLUDE_STEMS = {'license', 'licence', 'changelog', 'changes', 'history', 'contributing', 'code_of_conduct', 'security', 'authors', 'notice',
                 'pull_request_template', 'issue_template', '_index', 'sidebar', '_sidebar', '_footer', 'summary'}


class Refused(Exception):
    """A submission or fetch that cannot go on. str(e) is Chinese; .en is the English line; .code the HTTP status, if any."""
    def __init__(self, zh, en='', code=None):
        super().__init__(zh)
        self.en, self.code = en or zh, code


class Unchanged(Exception):
    """304 Not Modified."""


# ======================================================================= paths and small files
def reg_path():
    return os.path.join(ROOT, 'registry', 'shelves.json')


def snap_dir():
    return os.path.join(ROOT, 'registry', 'snapshots')


def docs_dir():
    return os.path.join(ROOT, 'docs')


def read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_text(path, text):
    """Write only when the content changed (a daily run that finds nothing new must not make a commit)."""
    try:
        with open(path, 'r', encoding='utf-8', newline='') as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def write_json(path, obj):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=1) + '\n')


def config():
    c = read_json(os.path.join(ROOT, 'registry', 'config.json'), {})
    return c if isinstance(c, dict) else {}


def base_url():
    """Where docs/ is published: registry/config.json "base_url", else guessed from the GitHub Actions environment."""
    b = str(config().get('base_url') or '').strip()
    if not b:
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        if '/' in repo:
            owner, name = repo.split('/', 1)
            b = 'https://%s.github.io/' % owner.lower() if name.lower() == owner.lower() + '.github.io' else 'https://%s.github.io/%s/' % (owner.lower(), name)
    return b.rstrip('/') + '/' if b else ''


def today():
    return datetime.datetime.now(datetime.timezone.utc).date()


# ======================================================================= text helpers
def clean(s, n=0):
    s = re.sub(r'\s+', ' ', str(s if s is not None else '')).strip()
    if n and len(s) > n:
        s = s[:n].rstrip() + '…'
    return s


class _Strip(HTMLParser):
    BLOCK = {'p', 'br', 'div', 'li', 'ul', 'ol', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'figure', 'figcaption', 'section', 'article'}
    SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'head'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append(' ')

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append(' ')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def strip_html(s):
    p = _Strip()
    try:
        p.feed(str(s or ''))
        p.close()
    except Exception:
        pass
    return clean(''.join(p.out))


def slug(s, fallback='x'):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:40].strip('-') or fallback


def tag_key(t):
    return unicodedata.normalize('NFKC', str(t)).casefold().strip()


def norm_tags(values, limit=TAGS_PER_ITEM):
    out, seen = [], set()
    if isinstance(values, str):
        values = [values]
    for v in values or []:
        t = clean(v).lstrip('#').strip()
        k = tag_key(t)
        if not k or len(t) > 30 or k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def guess_lang(text):
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
    if re.search(r'[\u3040-\u30ff]', text):
        return 'ja'
    return 'zh' if cjk >= 2 and cjk * 2 >= len(re.findall(r'[A-Za-z]{2,}', text)) else 'en'


def norm_lang(lang, sample=''):
    """The language of a text. Chinese and Japanese are recognised from the script itself: many Chinese feeds declare "en".
    Otherwise what the feed declares, else English."""
    guess = guess_lang(sample)
    if guess in ('zh', 'ja'):
        return guess
    m = re.match(r'^([a-z]{2,3})(?:-|$)', clean(lang).lower().replace('_', '-'))
    return m.group(1) if m else guess


def parse_date(s):
    s = clean(s)
    if not s:
        return ''
    d = None
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    try:
        if m:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            d = email.utils.parsedate_to_datetime(s).date()
    except (ValueError, TypeError, OverflowError, IndexError):
        return ''
    if d.year < 1990:
        return ''
    return min(d, today()).isoformat()          # a post scheduled for later must not sit at the top for weeks


def abs_url(u, base=''):
    u = clean(u)
    if not u:
        return ''
    try:
        u = urllib.parse.urljoin(base, u)
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return ''
    if p.scheme not in ('http', 'https') or not p.netloc or len(u) > 1500:
        return ''
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ''))


def host_is(host, domain):
    return host == domain or host.endswith('.' + domain)


def guess_type(url, mime='', hint=''):
    if hint in TYPES:
        return hint
    p = urllib.parse.urlsplit(url)
    host, path, mime = (p.hostname or '').lower(), p.path.lower(), (mime or '').lower()
    if mime.startswith('video/') or any(host_is(host, h) for h in VIDEO_HOSTS) and (host == 'youtu.be' or re.search(r'/(watch|shorts|video|v|embed)\b', path) or host_is(host, 'vimeo.com') and re.search(r'/\d+', path)):
        return 'video'
    if mime.startswith('audio/'):
        return 'audio'
    if mime == 'application/pdf' or path.endswith('.pdf'):
        return 'pdf'
    if path.endswith(('.md', '.markdown', '.txt')):
        return 'note'
    return 'article'


def make_item(url, title, summary='', image='', date='', tags=None, lang='', typ='', base='', mime=''):
    url = abs_url(url, base)
    if not url:
        return None
    summary = clean(summary, SUMMARY)
    title = clean(title, TITLE) or clean(summary, 80) or (urllib.parse.urlsplit(url).hostname or url)
    if summary == title:
        summary = ''
    image = abs_url(image, base)
    return {'url': url, 'title': title, 'summary': summary, 'image': image if image.startswith('https://') else '', 'date': parse_date(date),
            'tags': norm_tags(tags), 'lang': norm_lang(lang, title + ' ' + summary), 'type': guess_type(url, mime, typ)}


# ======================================================================= safe fetching
def is_public_ip(ip):
    ip = ipaddress.ip_address(ip.split('%')[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def via_proxy(host):
    """True when requests to this host go through a proxy (HTTPS_PROXY, or the system's) that is not bypassed for it."""
    try:
        return bool(urllib.request.getproxies().get('https')) and not urllib.request.proxy_bypass(host)
    except Exception:
        return False


def check_url(url):
    """The only door to the network: https, port 443, never an IP literal or a name for this machine or a private network,
    and (without a proxy) a name that resolves to public addresses only."""
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError:
        raise Refused('网址格式不对', 'Malformed address')
    if p.scheme != 'https' and not (ALLOW_LOCAL and p.scheme == 'http'):
        raise Refused('只支持 https 网址', 'Only https addresses are supported')
    if not p.hostname or p.username or p.password:
        raise Refused('网址里不能带账号或密码', 'Addresses with credentials are not accepted')
    if ALLOW_LOCAL:
        return
    if port not in (None, 443):
        raise Refused('只支持标准的 https 端口（443）', 'Only the standard https port (443) is supported')
    host = p.hostname.lower()
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        if not is_public_ip(host):
            raise Refused('不允许访问内网或本机地址', 'Private and local addresses are not allowed')
        return
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.localdomain')):
        raise Refused('不允许访问内网或本机地址', 'Private and local addresses are not allowed')
    if via_proxy(host):
        return                      # a proxy connects and resolves the name; what this machine's DNS says is of no use there (and is often poisoned)
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise Refused('找不到这个网站（域名无法解析）', 'The host name does not resolve')
    for info in infos:
        if not is_public_ip(info[4][0]):
            raise Refused('不允许访问内网或本机地址', 'Private and local addresses are not allowed')


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header('Authorization')          # a token never follows a redirect to another host
        return new


def decode(body, headers=None):
    ctype = (headers or {}).get('content-type', '')
    m = re.search(r'charset=["\']?([\w-]+)', ctype, re.I)
    if body.startswith(b'\xef\xbb\xbf'):
        return body[3:].decode('utf-8', 'replace')
    cands = [m.group(1)] if m else []
    if not m:
        s = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I) or re.search(rb'<\?xml[^>]+encoding=["\']([\w-]+)', body[:200], re.I)
        if s:
            cands.append(s.group(1).decode('ascii', 'ignore'))
    cands += ['utf-8', 'gb18030']
    for c in cands:
        try:
            return body.decode(c)
        except UnicodeDecodeError as e:
            if c.lower().replace('-', '') == 'utf8' and e.start >= len(body) - 3:      # only the last character was cut off (a Range or size cut)
                return body[:e.start].decode('utf-8')
        except LookupError:
            continue
    return body.decode('utf-8', 'replace')


def fetch(url, accept='*/*', max_bytes=MAX_BYTES, headers=None, truncate=False):
    """-> (final address, response headers in lower case, body). Raises Refused, or Unchanged on 304."""
    check_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Encoding': 'identity', **(headers or {})})
    handlers = [_Redirect]
    if ALLOW_LOCAL:
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=TIMEOUT) as r:
            body = r.read(max_bytes + 1)
            if len(body) > max_bytes:
                if not truncate:
                    raise Refused('文件太大（超过 %d MB）' % (max_bytes // 1048576 or 1), 'The file is too large')
                body = body[:max_bytes]
            return r.geturl(), {k.lower(): v for k, v in r.headers.items()}, body
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 304:
            raise Unchanged()
        raise Refused('网站返回了错误（HTTP %d）' % e.code, 'The site answered with HTTP %d' % e.code, e.code)
    except Refused:
        raise
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, ConnectionError, http.client.HTTPException, OSError) as e:
        raise Refused('连不上这个网站：%s' % (getattr(e, 'reason', None) or e), 'Could not connect: %s' % (getattr(e, 'reason', None) or e))


_ROBOTS = {}


def robots_allows(url):
    """Pages (not feeds) are read only where robots.txt allows it. A missing or unreachable robots.txt allows."""
    p = urllib.parse.urlsplit(url)
    origin = '%s://%s' % (p.scheme, p.netloc)
    rp = _ROBOTS.get(origin)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            _, h, body = fetch(origin + '/robots.txt', max_bytes=512 * 1024, truncate=True)
            rp.parse(decode(body, h).splitlines())
        except (Refused, Unchanged):
            rp.parse([])
        _ROBOTS[origin] = rp
    return rp.can_fetch(UA_TOKEN, url)


# ======================================================================= feeds: JSON Feed, Atom, RSS
NS_MEDIA, NS_XML = 'http://search.yahoo.com/mrss/', 'http://www.w3.org/XML/1998/namespace'


def _l(el):
    return el.tag.rsplit('}', 1)[-1] if isinstance(el.tag, str) else ''


def _kids(el, name):
    return [c for c in el if _l(c) == name]


def _kid(el, name):
    k = _kids(el, name)
    return k[0] if k else None


def _txt(el):
    return ''.join(el.itertext()).strip() if el is not None else ''


def _deep(el, name):
    return [c for c in el.iter() if _l(c) == name]


def _media_image(el):
    for t in _deep(el, 'thumbnail'):
        if t.get('url'):
            return t.get('url')
    for c in _deep(el, 'content'):
        if c.get('url') and (c.get('medium') == 'image' or (c.get('type') or '').startswith('image/')):
            return c.get('url')
    for c in _deep(el, 'image'):
        if c.get('href'):
            return c.get('href')
    return ''


def sniff(headers, body):
    """'jsonfeed', 'xmlfeed' or 'other' (a page)."""
    head = body[:4096].lstrip(b'\xef\xbb\xbf \t\r\n')
    if head.startswith(b'{'):
        try:
            d = json.loads(decode(body, headers))
            return 'jsonfeed' if isinstance(d, dict) and isinstance(d.get('items'), list) else 'other'
        except ValueError:
            return 'other'
    low = head.lower()
    m = re.search(rb'<(rss|feed|rdf:rdf)\b', low)
    h = re.search(rb'<html\b', low)
    return 'xmlfeed' if m and not (h and h.start() < m.start()) else 'other'


def parse_jsonfeed(d, base):
    if not isinstance(d, dict) or not isinstance(d.get('items'), list):
        raise Refused('不是有效的订阅源', 'Not a valid feed')
    ext = d.get('_shelf') if isinstance(d.get('_shelf'), dict) else {}
    shelf = {'name': clean(d.get('title'), 80), 'description': clean(d.get('description'), 200), 'home': abs_url(d.get('home_page_url'), base),
             'lang': d.get('language') or '', 'license': clean(ext.get('license'), 80)}
    items = []
    for e in d['items'][:MAX_ITEMS]:
        if not isinstance(e, dict):
            continue
        mime = ''
        for a in e.get('attachments') or []:
            if isinstance(a, dict) and str(a.get('mime_type', '')).split('/')[0] in ('video', 'audio', 'application'):
                mime = a['mime_type']
                break
        es = e.get('_shelf') if isinstance(e.get('_shelf'), dict) else {}
        summary = e.get('summary') or e.get('content_text') or strip_html(e.get('content_html') or '')
        tags = e.get('tags') if isinstance(e.get('tags'), list) else []
        if es.get('category'):                         # a site that lists its category among the tags: the category is not a tag of the directory
            tags = [t for t in tags if str(t).casefold() != str(es['category']).casefold()]
        it = make_item(e.get('url') or e.get('external_url'), e.get('title'), strip_html(summary), e.get('image') or e.get('banner_image'),
                       e.get('date_published') or e.get('date_modified'), tags, e.get('language') or shelf['lang'], es.get('type'), base, mime)
        if it:
            items.append(it)
    return shelf, items


def _xml_text(el):
    """Text of an Atom / RSS text element; html-typed ones are tag-stripped."""
    if el is None:
        return ''
    t = _txt(el)
    return strip_html(t) if (el.get('type') or '').lower() in ('html', 'xhtml') else clean(t)


def parse_atom(root, base):
    home = ''
    for l in _kids(root, 'link'):
        if (l.get('rel') or 'alternate') == 'alternate' and l.get('href'):
            home = l.get('href')
            break
    shelf = {'name': clean(_xml_text(_kid(root, 'title')), 80), 'description': clean(_xml_text(_kid(root, 'subtitle')), 200), 'home': abs_url(home, base),
             'lang': root.get('{%s}lang' % NS_XML) or '', 'license': ''}
    items = []
    for e in _kids(root, 'entry')[:MAX_ITEMS]:
        link, mime = '', ''
        for l in _kids(e, 'link'):
            rel = l.get('rel') or 'alternate'
            if rel == 'alternate' and l.get('href') and not link:
                link = l.get('href')
            elif rel == 'enclosure' and l.get('type') and not mime:
                mime = l.get('type')
        summary = _xml_text(_kid(e, 'summary')) or ''
        if not summary:
            group = _deep(e, 'description')
            summary = clean(_txt(group[0])) if group else ''
        if not summary:
            summary = _xml_text(_kid(e, 'content'))
        hint = 'video' if _deep(e, 'videoId') else ''
        it = make_item(link, _xml_text(_kid(e, 'title')), summary, _media_image(e), _txt(_kid(e, 'published')) or _txt(_kid(e, 'updated')),
                       [c.get('term') or c.get('label') for c in _kids(e, 'category')], e.get('{%s}lang' % NS_XML) or shelf['lang'], hint, base, mime)
        if it:
            items.append(it)
    return shelf, items


def parse_rss(root, base):
    channel = _kid(root, 'channel')                      # RSS 1.0 (rdf:RDF) keeps its items next to the channel, RSS 2.0 inside it
    if channel is None:
        raise Refused('不是有效的订阅源', 'Not a valid feed')
    shelf = {'name': clean(_xml_text(_kid(channel, 'title')), 80), 'description': clean(strip_html(_txt(_kid(channel, 'description'))), 200),
             'home': abs_url(_txt(_kid(channel, 'link')), base), 'lang': _txt(_kid(channel, 'language')), 'license': ''}
    items = []
    for e in [x for x in (channel if _l(root) == 'rss' else root).iter() if _l(x) == 'item'][:MAX_ITEMS]:
        link = _txt(_kid(e, 'link'))
        guid = _kid(e, 'guid')
        if not link and guid is not None and guid.get('isPermaLink', 'true') != 'false':
            link = _txt(guid)
        mime = ''
        for enc in _kids(e, 'enclosure'):
            if enc.get('type'):
                mime = enc.get('type')
        summary = strip_html(_txt(_kid(e, 'description'))) or strip_html(_txt(_kid(e, 'encoded')))
        image = _media_image(e)
        if not image:
            for enc in _kids(e, 'enclosure'):
                if (enc.get('type') or '').startswith('image/'):
                    image = enc.get('url') or ''
        it = make_item(link, clean(_txt(_kid(e, 'title'))), summary, image, _txt(_kid(e, 'pubDate')) or _txt(_kid(e, 'date')),
                       [_txt(c) for c in _kids(e, 'category')] + [_txt(c) for c in _kids(e, 'subject')], shelf['lang'], '', base,
                       mime if not mime.startswith('image/') else '')
        if it:
            items.append(it)
    return shelf, items


def parse_feed(body, headers, base):
    """-> (shelf info, items). Accepts JSON Feed, Atom and RSS 2.0 / 1.0; an XML document with a DOCTYPE or entities is refused."""
    kind = sniff(headers, body)
    if kind == 'jsonfeed':
        return parse_jsonfeed(json.loads(decode(body, headers)), base)
    if kind != 'xmlfeed':
        raise Refused('这个地址不是订阅源（RSS / Atom / JSON Feed）', 'This address is not a feed (RSS / Atom / JSON Feed)')
    if re.search(rb'<!\s*(DOCTYPE|ENTITY)', body, re.I):
        raise Refused('订阅源含有 DOCTYPE / ENTITY 声明，出于安全原因不予读取', 'The feed has a DOCTYPE or ENTITY declaration and is refused for safety')
    try:
        root = ET.fromstring(body.lstrip(b'\xef\xbb\xbf \t\r\n'))
    except ET.ParseError:
        raise Refused('订阅源的 XML 格式有误', 'The feed is not well-formed XML')
    name = _l(root)
    if name == 'feed':
        return parse_atom(root, base)
    if name in ('rss', 'RDF'):
        return parse_rss(root, base)
    raise Refused('不是有效的订阅源', 'Not a valid feed')


# ======================================================================= web pages
class _Page(HTMLParser):
    """Title, meta tags and link tags of a page. It reads on past </head>: some big sites (YouTube) put them after <body>."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.multi, self.links, self.title, self.lang = {}, {}, [], '', ''
        self._title, self.titled = False, False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'html':
            self.lang = a.get('lang', '')
        elif tag == 'title' and not self.titled:
            self._title = True
        elif tag == 'meta':
            key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
            if key and 'content' in a and len(self.links) + len(self.meta) < 2000:
                self.meta.setdefault(key, a['content'])
                self.multi.setdefault(key, []).append(a['content'])
        elif tag == 'link' and len(self.links) < 500:
            self.links.append(a)

    def handle_endtag(self, tag):
        if tag == 'title' and self._title:
            self._title, self.titled = False, True

    def handle_data(self, data):
        if self._title:
            self.title += data


def parse_page(text, base):
    p = _Page()
    try:
        p.feed(text[:MAX_BYTES])
        p.close()
    except Exception:
        pass
    m = p.meta
    first = lambda *keys: next((clean(m[k]) for k in keys if clean(m.get(k))), '')
    feeds = []
    for l in p.links:
        rel, typ = l.get('rel', '').lower().split(), l.get('type', '').lower()
        if 'alternate' in rel and typ in ('application/feed+json', 'application/atom+xml', 'application/rss+xml', 'application/json') and l.get('href'):
            if typ == 'application/json' and 'feed' not in (l.get('title', '') + l['href']).lower():
                continue
            u = abs_url(l['href'], base)
            if u:
                feeds.append((0 if 'json' in typ else 1 if 'atom' in typ else 2, u))
    canonical = next((abs_url(l.get('href'), base) for l in p.links if 'canonical' in l.get('rel', '').lower().split()), '')
    tags = list(p.multi.get('article:tag', [])) + re.split(r'[,，、;；]', m.get('keywords', ''))
    return {'title': first('og:title', 'twitter:title') or clean(p.title), 'description': first('og:description', 'description', 'twitter:description'),
            'image': first('og:image', 'twitter:image', 'twitter:image:src'), 'site': first('og:site_name', 'application-name'),
            'lang': p.lang or first('og:locale'), 'date': first('article:published_time', 'og:article:published_time', 'datepublished', 'date', 'dc.date', 'pubdate'),
            'tags': tags, 'og_type': first('og:type'), 'canonical': canonical, 'feeds': [u for _, u in sorted(feeds, key=lambda x: x[0])]}


def is_home(url):
    p = urllib.parse.urlsplit(url)
    segs = [s for s in p.path.split('/') if s]
    if p.query:
        return False
    indexed = bool(segs) and segs[-1].lower() in ('index.html', 'index.htm', 'index.php')       # /blog/ is often redirected to /blog/index.html
    if indexed:
        segs.pop()
    if not segs:
        return True
    return len(segs) == 1 and '.' not in segs[0] and (p.path.endswith('/') or indexed)          # example.com/blog/ , or a GitHub project site


def feed_like(url):
    p = urllib.parse.urlsplit(url)
    path = p.path.lower()
    return path.endswith(('.xml', '.rss', '.atom', '.json', '/feed', '/rss', '/atom')) or 'feeds/videos.xml' in path


# ======================================================================= markdown (only what a title and a summary need)
def _list_value(v):
    if isinstance(v, list):
        return [clean(x) for x in v]
    v = str(v or '').strip()
    if v.startswith('['):
        try:
            return [clean(x) for x in json.loads(v)]
        except ValueError:
            v = v.strip('[]')
    return [clean(x.strip('"\' ')) for x in re.split(r'[,，]', v) if x.strip('"\' ')]


def front_matter(text):
    """-> (dict of lower-case keys, body). YAML (---) and TOML (+++) front matter, the common simple forms only."""
    m = re.match(r'^\ufeff?(---|\+\+\+)[ \t]*\r?\n(.*?)\r?\n\1[ \t]*(?:\r?\n|$)', text, re.S)
    if not m:
        return {}, text
    meta, cur = {}, None
    for line in m.group(2).splitlines():
        kv = re.match(r'^([A-Za-z_][\w-]*)\s*[:=]\s*(.*)$', line)
        if kv:
            cur, v = kv.group(1).lower(), kv.group(2).strip()
            meta[cur] = v if v.startswith('[') else v.strip('"\'')
        elif cur and re.match(r'^\s*-\s+', line):
            val = re.sub(r'^\s*-\s+', '', line).strip().strip('"\'')
            if not isinstance(meta.get(cur), list):
                meta[cur] = [meta[cur]] if meta.get(cur) else []
            meta[cur].append(val)
    return meta, text[m.end():]


def first_paragraph(body):
    para, fence = [], False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(('```', '~~~')):
            fence = not fence
            continue
        if fence:
            continue
        if not s:
            if para:
                break
            continue
        if re.match(r'^(#{1,6}\s|[=\-*_]{3,}$|\||<!--|!\[|\[!\[|<\w[^>]*>$|\[\w+\]:\s)', s):
            if para:
                break
            continue
        para.append(re.sub(r'^(>\s*|[-*+]\s+|\d+[.)]\s+)', '', s))
        if sum(len(x) for x in para) > 400:
            break
    t = ' '.join(para)
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', t)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'<[^>]+>', '', t)
    return clean(re.sub(r'[`*~]+', '', t))


def humanize(name):
    """-> (title from a file name, date in its name or '')."""
    stem = posixpath.splitext(posixpath.basename(name))[0]
    m = re.match(r'^(\d{4})[-_.](\d{2})[-_.](\d{2})[-_. ]+(.+)$', stem)
    date, stem = (('%s-%s-%s' % m.group(1, 2, 3)), m.group(4)) if m else ('', stem)
    return clean(re.sub(r'[-_]+', ' ', stem)) or stem, date


def read_markdown(text, name=''):
    """-> dict(title, summary, date, tags, lang) of a Markdown document."""
    meta, body = front_matter(text)
    title = clean(meta.get('title'))
    atx, setext = re.search(r'^\s{0,3}#\s+(.+?)\s*#*\s*$', body, re.M), re.search(r'^(\S[^\n]*)\n=+\s*$', body, re.M)
    if not title:
        m = atx or setext
        title = clean(re.sub(r'[`*_]+|<[^>]+>', '', m.group(1))) if m else ''
    nm_title, nm_date = humanize(name) if name else ('', '')
    if atx:                                                          # the heading is the title, not the summary
        body = body[:atx.start()] + body[atx.end():]
    elif setext:
        body = body[:setext.start()] + body[setext.end():]
    summary = clean(meta.get('description') or meta.get('summary') or meta.get('excerpt')) or first_paragraph(body)
    tags = _list_value(meta.get('tags') or meta.get('keywords') or meta.get('categories') or '')
    return {'title': title or nm_title, 'summary': summary, 'date': meta.get('date') or meta.get('created') or nm_date, 'tags': tags, 'lang': meta.get('lang') or meta.get('language') or ''}


# ======================================================================= what is this address?
def normalize_input(raw):
    u = clean(raw)
    if not u:
        raise Refused('请填写网址', 'Please give an address')
    if not re.match(r'^[a-z][a-z0-9+.-]*://', u, re.I):
        u = 'https://' + u
    if u[:7].lower() == 'http://' and not ALLOW_LOCAL:
        u = 'https://' + u[7:]              # try the secure address of the same page
    try:
        p = urllib.parse.urlsplit(u)
        p.port
    except ValueError:
        raise Refused('网址格式不对', 'Malformed address')
    if not p.hostname or len(u) > 1500 or not re.fullmatch(r'[\w.:-]+', p.hostname):
        raise Refused('网址格式不对', 'Malformed address')
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc, p.path, p.query, ''))


def classify(url):
    """The shape of an address says what it is. No network."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    segs = [urllib.parse.unquote(s) for s in p.path.split('/') if s]
    q = urllib.parse.parse_qs(p.query)
    if host in ('github.com', 'www.github.com'):
        if len(segs) < 2:
            return {'kind': 'unsupported', 'zh': '请贴具体的仓库网址（github.com/用户/仓库），不是用户主页', 'en': 'Give a repository address (github.com/user/repo), not a profile'}
        o, r = segs[0], re.sub(r'\.git$', '', segs[1])
        if len(segs) == 2:
            return {'kind': 'repo', 'owner': o, 'repo': r, 'ref': '', 'path': ''}
        if segs[2] == 'tree' and len(segs) >= 4:
            return {'kind': 'repo', 'owner': o, 'repo': r, 'ref': segs[3], 'path': '/'.join(segs[4:])}
        if segs[2] == 'blob' and len(segs) >= 5:
            return {'kind': 'file', 'owner': o, 'repo': r, 'ref': segs[3], 'path': '/'.join(segs[4:])}
        return {'kind': 'unsupported', 'zh': '这个 GitHub 网址不是仓库、文件夹或文件，请贴仓库首页或 .md 文件的网址', 'en': 'This GitHub address is not a repository, folder or file'}
    if host == 'gist.github.com' and segs and re.fullmatch(r'[0-9a-f]{20,}|\d{4,}', segs[-1]):          # new gists have a hex id, old ones a number
        return {'kind': 'gist', 'id': segs[-1]}
    if host == 'raw.githubusercontent.com' and len(segs) >= 4:
        return {'kind': 'file', 'owner': segs[0], 'repo': segs[1], 'ref': segs[2], 'path': '/'.join(segs[3:])}
    if host_is(host, 'youtube.com') or host == 'youtu.be':
        if host == 'youtu.be' and segs or p.path == '/watch' and q.get('v') or segs[:1] == ['shorts']:
            return {'kind': 'video'}
        if segs[:1] == ['channel'] and len(segs) > 1 and re.fullmatch(r'[\w-]{10,}', segs[1]):
            return {'kind': 'feed', 'feed': 'https://www.youtube.com/feeds/videos.xml?channel_id=' + segs[1]}
        if p.path == '/playlist' and q.get('list'):
            return {'kind': 'feed', 'feed': 'https://www.youtube.com/feeds/videos.xml?playlist_id=' + urllib.parse.quote(q['list'][0])}
        if segs and (segs[0].startswith('@') or segs[0] in ('c', 'user')):
            return {'kind': 'web', 'home': True}                  # a channel page: its feed is what is meant
    ext = posixpath.splitext(p.path)[1].lower()
    if ext in ('.md', '.markdown', '.txt'):
        return {'kind': 'doc'}
    if ext == '.pdf':
        return {'kind': 'pdf'}
    return {'kind': 'web'}


def gh_json(path, max_bytes=MAX_BYTES):
    h = {'X-GitHub-Api-Version': '2022-11-28'}
    tok = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if tok and GITHUB_API.startswith('https://api.github.com'):
        h['Authorization'] = 'Bearer ' + tok
    try:
        _, hd, body = fetch(GITHUB_API + path, 'application/vnd.github+json', max_bytes, h)
    except Refused as e:
        if e.code == 404:
            raise Refused('找不到这个仓库或文件，或者它不是公开的', 'Not found, or not public', 404)
        if e.code in (403, 429):
            raise Refused('GitHub 暂时限流，请稍后再试', 'GitHub rate limit, try again later', e.code)
        raise
    try:
        return json.loads(decode(body, hd))
    except ValueError:
        raise Refused('GitHub 返回了无法识别的内容', 'GitHub returned something unreadable')


def natural_key(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', s)]


def blob_url(o, r, ref, path):
    return 'https://github.com/%s/%s/blob/%s/%s' % (urllib.parse.quote(o), urllib.parse.quote(r), urllib.parse.quote(ref), urllib.parse.quote(path))


def raw_url(o, r, ref, path):
    return '%s/%s/%s/%s/%s' % (GITHUB_RAW, urllib.parse.quote(o), urllib.parse.quote(r), urllib.parse.quote(ref), urllib.parse.quote(path))


def read_head(url, nbytes=8192):
    """The first bytes of a text file (a Range request; a server that ignores it is cut off at 256 KB)."""
    _, h, body = fetch(url, 'text/plain,text/markdown,*/*', 256 * 1024, {'Range': 'bytes=0-%d' % (nbytes - 1)}, truncate=True)
    return decode(body[:nbytes * 4], h)


def read_repo(spec, prev=None):
    o, r = spec['owner'], spec['repo']
    info = gh_json('/repos/%s/%s' % (urllib.parse.quote(o), urllib.parse.quote(r)))
    if not isinstance(info, dict) or info.get('private'):
        raise Refused('找不到这个仓库或它不是公开的', 'Not found, or not public')
    ref = spec.get('ref') or info.get('default_branch') or 'main'
    tree = gh_json('/repos/%s/%s/git/trees/%s?recursive=1' % (urllib.parse.quote(o), urllib.parse.quote(r), urllib.parse.quote(ref)), 8 * 1024 * 1024)
    prefix = (spec.get('path') or '').strip('/')
    pre = prefix + '/' if prefix else ''
    cands = []
    for e in tree.get('tree') or []:
        p = e.get('path') or ''
        if e.get('type') != 'blob' or (pre and not p.startswith(pre)):
            continue
        parts = p[len(pre):].split('/')
        name = parts[-1]
        stem, ext = posixpath.splitext(name)
        if ext.lower() not in REPO_EXT or stem.lower().replace('-', '_') in EXCLUDE_STEMS:
            continue
        if any(x.lower() in EXCLUDE_DIRS or x.startswith('.') for x in parts[:-1]):
            continue
        if stem.lower() == 'readme' and len(parts) > 1:                  # only the folder's own README
            continue
        cands.append(p)
    warnings = []
    if tree.get('truncated'):
        warnings.append(('仓库太大，只读取了其中一部分文件', 'The repository is large; only part of it was read'))
    cands.sort(key=natural_key, reverse=True)
    if len(cands) > REPO_FILES:
        warnings.append(('有 %d 个文件，只收录前 %d 个（按路径倒序）' % (len(cands), REPO_FILES), '%d files found, the first %d are listed' % (len(cands), REPO_FILES)))
        cands = cands[:REPO_FILES]
    pages_root = r.lower() == o.lower() + '.github.io'
    items, tags, read_langs, unread = [], norm_tags(info.get('topics'), 5), [], []
    for n, p in enumerate(cands):
        ext = posixpath.splitext(p)[1].lower()
        rel = p[len(pre):]
        title, date = humanize(rel)
        summary, itags, lang, got = '', [], '', False
        if n < REPO_READ and ext != '.pdf':
            try:
                text = read_head(raw_url(o, r, ref, p))
                if ext in ('.md', '.markdown'):
                    md = read_markdown(text, rel)
                    title, summary, date, itags, lang = md['title'] or title, md['summary'], md['date'] or date, md['tags'], md['lang']
                else:
                    pg = parse_page(text, '')
                    title, summary, date = pg['title'] or title, pg['description'], pg['date'] or date
                got = True
            except (Refused, Unchanged):
                pass
        if rel.lower() in ('readme.md', 'readme.markdown') and title.lower() == 'readme':
            title = info.get('name') or r
        link = 'https://%s/%s' % (r, urllib.parse.quote(p)) if pages_root and ext in ('.html', '.htm') else blob_url(o, r, ref, p)
        it = make_item(link, title, summary, '', date, itags, lang, 'note' if ext in ('.md', '.markdown') else 'pdf' if ext == '.pdf' else 'article')
        if it:
            items.append(it)
            (read_langs if got else unread).append(it)
    if not items:
        raise Refused('在这个仓库%s里没有找到 .md / .html / .pdf 文件' % ('（文件夹）' if prefix else ''), 'No .md / .html / .pdf files found')
    # a file whose text was not read has only its file name to go by (issue-312.md): it takes the language of the files that were read
    counts = {}
    for it in read_langs:
        counts[it['lang']] = counts.get(it['lang'], 0) + 1
    repo_lang = max(counts, key=counts.get) if counts else ''
    for it in unread:
        if repo_lang:
            it['lang'] = repo_lang
    lic = info.get('license') if isinstance(info.get('license'), dict) else {}
    home = info.get('html_url') or 'https://github.com/%s/%s' % (o, r)
    shelf = {'name': '%s/%s' % (o, r) + (' · ' + prefix if prefix else ''), 'description': clean(info.get('description'), 200), 'home': home + ('/tree/%s/%s' % (urllib.parse.quote(ref), urllib.parse.quote(prefix)) if prefix else ''),
             'lang': repo_lang, 'license': '' if lic.get('spdx_id') in (None, 'NOASSERTION') else lic.get('spdx_id'), 'tags': tags}
    return {'shelf': shelf, 'items': items, 'warnings': warnings}


def read_gist(gid):
    info = gh_json('/gists/' + urllib.parse.quote(gid))
    files = info.get('files') if isinstance(info, dict) else None
    if not files:
        raise Refused('这个 Gist 里没有文件', 'This gist has no files')
    pick = next((n for n in files if n.lower().endswith(('.md', '.markdown'))), None) or next((n for n in files if n.lower().endswith(('.txt', '.html'))), None)
    if not pick:
        raise Refused('这个 Gist 里没有 Markdown 文件（.md）', 'This gist has no Markdown file (.md)')
    f = files[pick]
    text = f.get('content')
    if text is None and f.get('raw_url'):
        text = read_head(f['raw_url'])
    md = read_markdown(text or '', pick) if pick.lower().endswith(('.md', '.markdown', '.txt')) else {'title': humanize(pick)[0], 'summary': '', 'date': '', 'tags': [], 'lang': ''}
    title = md['title'] if md['title'] and md['title'] != humanize(pick)[0] else clean(info.get('description')) or md['title']
    owner = (info.get('owner') or {}).get('login') or 'gist'
    it = make_item(info.get('html_url') or 'https://gist.github.com/' + gid, title, md['summary'], '', md['date'] or info.get('created_at'), md['tags'], md['lang'], 'note')
    warn = [('这个 Gist 有多个文件，只收录 %s' % pick, 'The gist has several files; only %s is listed' % pick)] if len(files) > 1 else []
    return {'shelf': {'name': owner + ' · Gist', 'description': '', 'home': 'https://gist.github.com/' + owner, 'lang': '', 'license': '', 'tags': []}, 'items': [it], 'warnings': warn}


def read_link(entry, pre=None):
    """One page, one document or one video."""
    sub, url = entry.get('sub') or 'page', entry['source']
    if sub == 'gist':
        return read_gist(entry['gist'])
    if sub == 'file':
        s = entry['repo']
        txt = read_head(raw_url(s['owner'], s['repo'], s['ref'], s['path']))
        ext = posixpath.splitext(s['path'])[1].lower()
        if ext == '.pdf':
            title, date = humanize(s['path'])
            md = {'title': title, 'summary': '', 'date': date, 'tags': [], 'lang': ''}
        elif ext in ('.html', '.htm'):
            pg = parse_page(txt, '')
            md = {'title': pg['title'] or humanize(s['path'])[0], 'summary': pg['description'], 'date': pg['date'], 'tags': pg['tags'], 'lang': pg['lang']}
        else:
            md = read_markdown(txt, s['path'])
        it = make_item(blob_url(s['owner'], s['repo'], s['ref'], s['path']), md['title'], md['summary'], '', md['date'], md['tags'], md['lang'], 'pdf' if ext == '.pdf' else 'note' if ext not in ('.html', '.htm') else 'article')
        return {'shelf': {'name': '%s/%s' % (s['owner'], s['repo']), 'description': '', 'home': 'https://github.com/%s/%s' % (s['owner'], s['repo']), 'lang': '', 'license': '', 'tags': []}, 'items': [it], 'warnings': []}
    host = urllib.parse.urlsplit(url).hostname or ''
    if sub == 'doc':
        txt = read_head(url)
        md = read_markdown(txt, urllib.parse.urlsplit(url).path)
        it = make_item(url, md['title'], md['summary'], '', md['date'], md['tags'], md['lang'], 'note')
        return {'shelf': {'name': re.sub(r'^www\.', '', host), 'description': '', 'home': '%s://%s/' % (urllib.parse.urlsplit(url).scheme, host), 'lang': '', 'license': '', 'tags': []}, 'items': [it], 'warnings': []}
    if sub == 'pdf':
        _, h, body = fetch(url, 'application/pdf,*/*', 4096, {'Range': 'bytes=0-1023'}, truncate=True)
        if not body.startswith(b'%PDF'):
            raise Refused('这个地址不是 PDF 文件', 'This address is not a PDF file')
        title, date = humanize(urllib.parse.unquote(urllib.parse.urlsplit(url).path))
        it = make_item(url, title, '', '', date, [], '', 'pdf')
        return {'shelf': {'name': re.sub(r'^www\.', '', host), 'description': '', 'home': '%s://%s/' % (urllib.parse.urlsplit(url).scheme, host), 'lang': '', 'license': '', 'tags': []}, 'items': [it], 'warnings': []}
    # a web page (or a video page)
    if sub == 'video' and (host_is(host, 'youtube.com') or host == 'youtu.be'):
        try:
            _, h, body = fetch('https://www.youtube.com/oembed?format=json&url=' + urllib.parse.quote(url, safe=''), 'application/json', 256 * 1024)
            d = json.loads(decode(body, h))
            it = make_item(url, d.get('title'), '', d.get('thumbnail_url'), '', [], '', 'video')
            if it:
                it['date'] = ''
                return {'shelf': {'name': clean(d.get('author_name'), 80) or 'YouTube', 'description': '', 'home': abs_url(d.get('author_url')) or 'https://www.youtube.com/', 'lang': '', 'license': '', 'tags': []},
                        'items': [it], 'warnings': []}
        except (Refused, ValueError):
            pass
    if pre is None:
        if not robots_allows(url):
            raise Refused('这个网站的 robots.txt 不允许读取这个页面。可以改贴它的订阅源（RSS / Atom）地址', 'robots.txt does not allow reading this page; give its feed address instead')
        pre = fetch(url, 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.5')
    final, h, body = pre
    pg = parse_page(decode(body, h), final)
    if not pg['title']:
        raise Refused('读不到这个页面的标题（可能需要登录，或不是网页）', 'The page has no readable title (login needed, or not a web page)')
    link = pg['canonical'] if pg['canonical'] and urllib.parse.urlsplit(pg['canonical']).hostname == urllib.parse.urlsplit(final).hostname else final
    it = make_item(link, pg['title'], pg['description'], pg['image'], pg['date'], pg['tags'], pg['lang'], 'video' if pg['og_type'].startswith('video') else '')
    fh = urllib.parse.urlsplit(final).hostname or host
    return {'shelf': {'name': clean(pg['site'], 80) or re.sub(r'^www\.', '', fh), 'description': '', 'home': '%s://%s/' % (urllib.parse.urlsplit(final).scheme, urllib.parse.urlsplit(final).netloc),
                      'lang': '', 'license': '', 'tags': []}, 'items': [it], 'warnings': [], 'feeds': pg['feeds']}


def read_feed(entry, prev=None, pre=None):
    cond = {}
    if prev and not pre and prev.get('parser') == PARSER:         # a snapshot made by older rules is read again in full
        if prev.get('etag'):
            cond['If-None-Match'] = prev['etag']
        if prev.get('modified'):
            cond['If-Modified-Since'] = prev['modified']
    if pre is None:
        pre = fetch(entry['source'], 'application/feed+json,application/atom+xml,application/rss+xml,application/xml;q=0.9,text/xml;q=0.9,*/*;q=0.5', headers=cond)
    final, h, body = pre
    shelf, items = parse_feed(body, h, final)
    shelf['tags'] = []
    if not shelf['home']:
        p = urllib.parse.urlsplit(final)
        shelf['home'] = '%s://%s/' % (p.scheme, p.netloc)
    if not shelf['name']:
        shelf['name'] = re.sub(r'^www\.', '', urllib.parse.urlsplit(final).hostname or '')
    dated = sorted(items, key=lambda i: i['date'], reverse=True)
    keep = dated[:FEED_KEEP] if all(i['date'] for i in items) else items[:FEED_KEEP]       # feeds are newest-first; dates decide when they all have one
    return {'shelf': shelf, 'items': keep, 'warnings': [], 'etag': h.get('etag', ''), 'modified': h.get('last-modified', '')}


def read_source(entry, prev=None, pre=None):
    """Read a registered (or just discovered) source -> {'shelf', 'items', 'warnings', ...}. Raises Refused / Unchanged."""
    kind = entry['kind']
    res = read_feed(entry, prev, pre) if kind == 'feed' else read_repo(entry['repo'], prev) if kind == 'repo' else read_link(entry, pre)
    shelf = res['shelf']
    shelf['lang'] = norm_lang(shelf.get('lang'), ' '.join(i['title'] for i in res['items'][:20]))
    res['items'] = [i for i in res['items'] if i]
    if not res['items']:
        raise Refused('没有读到可以收录的内容', 'Nothing to list was found')
    return res


def discover(raw):
    """What is this address? -> (registry entry without id, the first response if it is worth keeping, warnings).

    The shape decides first (GitHub, Gist, YouTube, .md, .pdf); anything else is fetched once: a feed is a feed, a home page
    leads to its feed, any other page is one link.
    """
    url = normalize_input(raw)
    c = classify(url)
    k = c['kind']
    if k == 'unsupported':
        raise Refused(c['zh'], c['en'])
    if k == 'repo':
        return {'kind': 'repo', 'url': url, 'repo': {x: c[x] for x in ('owner', 'repo', 'ref', 'path')}}, None, []
    if k == 'file':
        ext = posixpath.splitext(c['path'])[1].lower()
        if ext not in REPO_EXT + ('.txt',):
            raise Refused('只支持 .md / .html / .pdf 文件', 'Only .md / .html / .pdf files are supported')
        return {'kind': 'link', 'sub': 'file', 'url': url, 'source': blob_url(c['owner'], c['repo'], c['ref'], c['path']), 'repo': {x: c[x] for x in ('owner', 'repo', 'ref', 'path')}}, None, []
    if k == 'gist':
        return {'kind': 'link', 'sub': 'gist', 'url': url, 'source': 'https://gist.github.com/' + c['id'], 'gist': c['id']}, None, []
    if k == 'feed':
        return {'kind': 'feed', 'url': url, 'source': c['feed']}, None, []
    if k in ('doc', 'pdf'):
        return {'kind': 'link', 'sub': k, 'url': url, 'source': url}, None, []
    if k == 'video':
        return {'kind': 'link', 'sub': 'video', 'url': url, 'source': url}, None, []
    # a web address: a feed, a page that leads to a feed, or a page
    warnings, pre = [], None
    disallowed = not feed_like(url) and not robots_allows(url)
    page = None
    if not disallowed:
        pre = fetch(url, 'text/html,application/xhtml+xml,application/atom+xml,application/rss+xml,application/feed+json,application/xml;q=0.8,*/*;q=0.5')
        final, h, body = pre
        if sniff(h, body) != 'other':
            return {'kind': 'feed', 'url': url, 'source': final}, pre, []
        page = parse_page(decode(body, h), final)
    final = pre[0] if pre else url
    home = c.get('home') or is_home(url) or is_home(final)
    if home or disallowed:
        linked = page['feeds'] if page else []
        tries = linked + [urllib.parse.urljoin(final, c) for c in COMMON_FEEDS if not linked]
        seen, failed = set(), []
        for f in tries[:7]:
            if f in seen:
                continue
            seen.add(f)
            try:
                fp = fetch(f, 'application/feed+json,application/atom+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.5')
            except (Refused, Unchanged) as ex:
                if f in linked:
                    failed.append((f, str(ex)))
                continue
            if sniff(fp[1], fp[2]) != 'other':
                return {'kind': 'feed', 'url': url, 'source': fp[0]}, fp, []
            if f in linked:
                failed.append((f, '不是订阅源'))
        if disallowed:
            raise Refused('这个网站的 robots.txt 不允许读取这个页面，也没有找到订阅源。可以改贴它的订阅源（RSS / Atom）地址', 'robots.txt does not allow reading this page and no feed was found; give the feed address instead')
        if failed:
            warnings.append(('找到了订阅源（%s）但读取失败：%s。只收录这一个页面' % failed[0], 'A feed was found (%s) but could not be read: %s. Only this page is listed' % failed[0]))
        else:
            warnings.append(('没有找到这个网站的订阅源，只收录这一个页面', 'No feed found for this site; only this page is listed'))
    elif page and page['feeds']:
        warnings.append(('这个网站有订阅源（%s）。如果想收录整个网站，请改贴网站首页的网址' % page['feeds'][0], 'This site has a feed (%s). To list the whole site, give its home page instead' % page['feeds'][0]))
    return {'kind': 'link', 'sub': 'page', 'url': url, 'source': final}, pre, warnings


# ======================================================================= registry
def load_registry():
    r = read_json(reg_path(), {})
    r = r if isinstance(r, dict) else {}
    if not isinstance(r.get('shelves'), list):
        r['shelves'] = []
    return r


def blocked(entry):
    try:
        with open(os.path.join(ROOT, 'registry', 'blocklist.txt'), 'r', encoding='utf-8-sig') as f:
            rules = [l.strip().lower() for l in f if l.strip() and not l.strip().startswith('#')]
    except OSError:
        return False
    host = (urllib.parse.urlsplit(entry.get('source') or entry.get('url') or '').hostname or '').lower()
    gh = '%s/%s' % (entry['repo']['owner'].lower(), entry['repo']['repo'].lower()) if entry.get('repo') else ''
    full = (entry.get('url') or '').lower()
    return any(r == host or host.endswith('.' + r) or (gh and r in (gh, 'github.com/' + gh)) or (r.startswith('http') and full.startswith(r)) for r in rules)


def make_id(entry, taken):
    k = entry['kind']
    if k == 'repo':
        s = entry['repo']
        base = 'gh-' + slug(s['owner']) + '-' + slug(s['repo']) + ('-' + slug(s['path'].replace('/', '-')) if s.get('path') else '')
    elif k == 'feed':                                       # named after the address that was submitted, not a feed service in between (feedburner)
        base = slug(re.sub(r'^www\.', '', urllib.parse.urlsplit(entry.get('url') or entry['source']).hostname or 'feed'))
    else:
        base = 'link-' + hashlib.sha1(entry['source'].encode()).hexdigest()[:8]
    cand, n = base, 1
    while cand in taken:
        n += 1
        cand = '%s-%d' % (base, n)
    return cand


def find_entry(reg, entry):
    for e in reg['shelves']:
        if e['kind'] == entry['kind'] and (e.get('source') == entry.get('source') if entry['kind'] != 'repo' else e.get('repo') == entry.get('repo')):
            return e
    return None


def probe(raw):
    """Everything a maintainer needs to decide: what it is, what was found, and why not if it cannot be listed."""
    entry, pre, warnings = discover(raw)
    if blocked(entry):
        raise Refused('这个网站不在收录范围内', 'This site is not accepted')
    res = read_source(entry, None, pre)
    entry.setdefault('source', entry['url'])
    if entry['kind'] == 'repo':
        entry['source'] = 'github:%s/%s' % (entry['repo']['owner'], entry['repo']['repo'])
    existing = find_entry(load_registry(), entry)
    return {'entry': entry, 'shelf': res['shelf'], 'items': res['items'], 'warnings': warnings + res.get('warnings', []), 'existing': existing['id'] if existing else ''}


def approve(raw, tags=None, issue=None):
    r = probe(raw)
    if r['existing']:
        raise Refused('已经在目录里了（%s）' % r['existing'], 'Already in the directory (%s)' % r['existing'])
    reg = load_registry()
    e = r['entry']
    e['id'] = make_id(e, {x['id'] for x in reg['shelves']})
    e['name'] = r['shelf']['name']
    e['tags'] = norm_tags(tags, 5)
    e['added'] = today().isoformat()
    if issue:
        e['issue'] = issue
    reg['shelves'].append({k: e[k] for k in ('id', 'kind', 'sub', 'url', 'source', 'repo', 'gist', 'name', 'tags', 'added', 'issue') if k in e})
    write_json(reg_path(), reg)
    fetch_all(only=e['id'])
    return e, r


def remove(sid):
    if not re.fullmatch(r'[a-z0-9-]+', sid or ''):
        raise Refused('书架编号不对', 'Bad shelf id')
    reg = load_registry()
    keep = [e for e in reg['shelves'] if e['id'] != sid]
    if len(keep) == len(reg['shelves']):
        raise Refused('目录里没有这个书架：' + sid, 'No such shelf: ' + sid)
    reg['shelves'] = keep
    write_json(reg_path(), reg)
    try:
        os.remove(os.path.join(snap_dir(), sid + '.json'))
    except OSError:
        pass


# ======================================================================= snapshots (the daily fetch)
def load_snapshot(sid):
    s = read_json(os.path.join(snap_dir(), sid + '.json'), None)
    return s if isinstance(s, dict) else None


def fetch_all(only=None, log=print):
    reg = load_registry()
    day = today().isoformat()
    ok = bad = 0
    for e in reg['shelves']:
        if only and e['id'] != only:
            continue
        prev = load_snapshot(e['id'])
        snap = dict(prev) if prev else {'id': e['id'], 'items': [], 'shelf': {}, 'fails': 0}
        try:
            res = read_source(e, prev)
            seen = {i['url']: i['date'] for i in (prev or {}).get('items', []) if i.get('date')}
            for i in res['items']:
                if not i['date'] and e['kind'] != 'repo':
                    i['date'] = seen.get(i['url']) or day              # no date of its own: the day it was first seen
            snap.update({'kind': e['kind'], 'parser': PARSER, 'status': 'ok', 'error': '', 'fails': 0, 'shelf': res['shelf'], 'items': res['items'],
                         'etag': res.get('etag', ''), 'modified': res.get('modified', '')})
            ok += 1
            log('ok    %-28s %3d items  %s' % (e['id'], len(res['items']), res['shelf'].get('name', '')))
        except Unchanged:
            snap.update({'status': 'ok', 'error': '', 'fails': 0})
            ok += 1
            log('same  %-28s (not modified)' % e['id'])
        except Refused as ex:
            snap.update({'status': 'error', 'error': str(ex), 'fails': snap.get('fails', 0) + 1})
            bad += 1
            log('FAIL  %-28s %s' % (e['id'], ex))
        except Exception as ex:                       # one broken source must never stop the run
            snap.update({'status': 'error', 'error': '%s: %s' % (type(ex).__name__, ex), 'fails': snap.get('fails', 0) + 1})
            bad += 1
            log('FAIL  %-28s %s: %s' % (e['id'], type(ex).__name__, ex))
        snap['id'] = e['id']
        write_json(os.path.join(snap_dir(), e['id'] + '.json'), snap)
    log('fetch: %d ok, %d failed' % (ok, bad))
    return ok, bad


# ======================================================================= build docs/
def dedupe_key(url):
    p = urllib.parse.urlsplit(url)
    q = [kv for kv in urllib.parse.parse_qsl(p.query) if not kv[0].lower().startswith('utm_')]
    return '%s%s?%s' % ((p.hostname or '').lower(), p.path.rstrip('/'), urllib.parse.urlencode(q))


def atom_feed(title, subtitle, self_url, home, items, shelves):
    latest = max([i['date'] for i in items if i['date']] or ['1970-01-01']) + 'T00:00:00Z'
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">', '  <id>%s</id>' % xml_escape(self_url or home or 'urn:shelf-directory'),
           '  <title>%s</title>' % xml_escape(title)]
    if subtitle:
        out.append('  <subtitle>%s</subtitle>' % xml_escape(subtitle))
    if self_url:
        out.append('  <link rel="self" type="application/atom+xml" href="%s"/>' % xml_escape(self_url, {'"': '&quot;'}))
    out += ['  <updated>%s</updated>' % latest, '  <author><name>%s</name></author>' % xml_escape(config().get('title') or 'Shelf directory')]
    for i in items:
        stamp = (i['date'] or '1970-01-01') + 'T00:00:00Z'
        out += ['  <entry>', '    <id>%s</id>' % xml_escape(i['url']), '    <title>%s</title>' % xml_escape(i['title']),
                '    <link rel="alternate" type="text/html" href="%s"/>' % xml_escape(i['url'], {'"': '&quot;'}), '    <updated>%s</updated>' % stamp]
        if i['date']:
            out.append('    <published>%s</published>' % stamp)
        if i['summary']:
            out.append('    <summary type="text">%s</summary>' % xml_escape(i['summary']))
        out.append('    <source><title>%s</title></source>' % xml_escape(shelves[i['shelf']]['name']))
        out += ['    <category term="%s"/>' % xml_escape(t, {'"': '&quot;'}) for t in i['tags']]
        out.append('  </entry>')
    out.append('</feed>')
    return '\n'.join(out) + '\n'


def json_feed(title, subtitle, self_url, home, items, shelves):
    jf = {'version': 'https://jsonfeed.org/version/1.1', 'title': title}
    if home:
        jf['home_page_url'] = home
    if self_url:
        jf['feed_url'] = self_url
    if subtitle:
        jf['description'] = subtitle
    jf['_shelf'] = {'spec': 1}
    jf['items'] = []
    for i in items:
        e = {'id': i['url'], 'url': i['url'], 'title': i['title'], 'content_text': i['summary'] or i['title']}
        if i['summary']:
            e['summary'] = i['summary']
        if i['date']:
            e['date_published'] = i['date'] + 'T00:00:00Z'
        if i['image']:
            e['image'] = i['image']
        e['tags'] = i['tags']
        e['authors'] = [{'name': shelves[i['shelf']]['name']}]
        e['_shelf'] = {'type': i['type'], 'shelf': i['shelf']}
        jf['items'].append(e)
    return json.dumps(jf, ensure_ascii=False, indent=2) + '\n'


def build():
    base = base_url()
    cfg = config()
    reg = load_registry()
    alias = read_json(os.path.join(ROOT, 'registry', 'tags.json'), {})
    alias = {tag_key(k): v for k, v in alias.items() if not str(k).startswith('_')} if isinstance(alias, dict) else {}
    repo = str(cfg.get('repo') or os.environ.get('GITHUB_REPOSITORY') or '').strip()
    shelves, items, seen = {}, [], set()
    for e in reg['shelves']:
        s = load_snapshot(e['id'])
        if not s or not s.get('items') or s.get('fails', 0) >= DOWN_AFTER:
            continue
        info = s.get('shelf') or {}
        stags = norm_tags(list(e.get('tags') or []) + list(info.get('tags') or []), 5)
        shelves[e['id']] = {'id': e['id'], 'name': e.get('name') or info.get('name') or e['id'], 'description': info.get('description', ''), 'home': info.get('home', ''),
                            'kind': e['kind'], 'lang': info.get('lang') or '', 'license': info.get('license', ''), 'tags': stags, 'n': 0, 'updated': '',
                            'feed': e['source'] if e['kind'] == 'feed' else '', 'status': s.get('status', 'ok')}
        for i in s['items']:
            k = dedupe_key(i['url'])
            if k in seen:
                continue
            seen.add(k)
            tags, keys = [], set()
            for t in list(i.get('tags') or []) + stags:
                t = clean(alias.get(tag_key(t), t))
                if tag_key(t) not in keys and t:
                    keys.add(tag_key(t))
                    tags.append(t)
            items.append({'id': hashlib.sha1(i['url'].encode()).hexdigest()[:10], 'title': i['title'], 'summary': i.get('summary', ''), 'url': i['url'], 'image': i.get('image', ''),
                          'date': i.get('date', ''), 'type': i.get('type') if i.get('type') in TYPES else 'article', 'lang': i.get('lang') or 'en', 'tags': tags[:TAGS_PER_ITEM], 'shelf': e['id']})
    items.sort(key=lambda i: (i['date'], i['shelf'], i['title']), reverse=True)
    items = items[:MAX_DIRECTORY_ITEMS]
    for i in items:
        sh = shelves[i['shelf']]
        sh['n'] += 1
        sh['updated'] = max(sh['updated'], i['date'])
    tag_n, tag_name = {}, {}
    for i in items:
        for t in i['tags']:
            tag_n[tag_key(t)] = tag_n.get(tag_key(t), 0) + 1
            tag_name.setdefault(tag_key(t), t)
    # a tag's feed address is ASCII: letters and digits as they are, a tag in Chinese gets a short fixed code
    tags = sorted(({'name': tag_name[k], 'slug': slug(tag_name[k], 't-' + hashlib.sha1(k.encode()).hexdigest()[:6]), 'n': n} for k, n in tag_n.items()),
                  key=lambda t: (-t['n'], t['name']))
    used = set()
    for t in tags:                                          # two tags may share a slug: the second one gets a suffix
        b, n = t['slug'], 1
        while t['slug'] in used:
            n += 1
            t['slug'] = '%s-%d' % (b, n)
        used.add(t['slug'])
    types = {}
    for i in items:
        types[i['type']] = types.get(i['type'], 0) + 1
    shelf_list = sorted((s for s in shelves.values() if s['n']), key=lambda s: (-s['n'], s['name']))
    for s in shelf_list:
        s['shelf_feed'] = 'feed/shelf/%s' % s['id']
    day = today().isoformat()
    out = {}                                                # relative path -> text
    out['directory.json'] = json.dumps({'spec': 1, 'title': cfg.get('title') or 'Shelf directory', 'repo': repo, 'generated': day, 'base': base, 'count': len(items), 'types': types, 'shelves': shelf_list,
                                        'tags': tags[:300], 'feeds': {'all': 'feed/all', 'type': 'feed/type/{type}', 'tag': 'feed/tag/{slug}', 'shelf': 'feed/shelf/{id}'},
                                        'items': items}, ensure_ascii=False, separators=(',', ':')) + '\n'
    title = cfg.get('title') or 'Shelf directory'

    def feeds(path, sub, rows):
        rows = rows[:FEED_KEEP]
        self_x, self_j = (base + path + '.xml', base + path + '.json') if base else ('', '')
        out[path + '.xml'] = atom_feed(sub, title, self_x, base, rows, shelves)
        out[path + '.json'] = json_feed(sub, title, self_j, base, rows, shelves)

    feeds('feed/all', title, items)
    for ty in types:
        feeds('feed/type/' + ty, '%s · %s' % (title, ty), [i for i in items if i['type'] == ty])
    for t in tags[:MAX_TAG_FEEDS]:
        feeds('feed/tag/' + t['slug'], '%s · #%s' % (title, t['name']), [i for i in items if any(tag_key(x) == tag_key(t['name']) for x in i['tags'])])
    for s in shelf_list:
        feeds('feed/shelf/' + s['id'], '%s · %s' % (title, s['name']), [i for i in items if i['shelf'] == s['id']])
    opml = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0">', '  <head><title>%s</title></head>' % xml_escape(title + ' · shelves'), '  <body>',
            '    <outline text="%s" title="%s">' % (xml_escape(title, {'"': '&quot;'}), xml_escape(title, {'"': '&quot;'}))]
    for s in shelf_list:
        xml = s['feed'] or (base + s['shelf_feed'] + '.xml')
        opml.append('      <outline type="rss" text="%s" title="%s" xmlUrl="%s" htmlUrl="%s"/>' % tuple(xml_escape(x, {'"': '&quot;'}) for x in (s['name'], s['name'], xml, s['home'])))
    opml += ['    </outline>', '  </body>', '</opml>']
    out['shelves.opml'] = '\n'.join(opml) + '\n'
    out['index.html'] = ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                         '<meta name="robots" content="noindex, follow"><title>%s</title></head><body style="font:16px/1.8 system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 1em">'
                         '<h1>%s</h1><p>这里是公共目录的数据，由页面「公共目录」读取，本身不是给人读的网页。This is the data behind the directory view.</p><ul>'
                         '<li><a href="directory.json">directory.json</a> (%d items, %d shelves)</li><li><a href="shelves.opml">shelves.opml</a></li>'
                         '<li><a href="feed/all.xml">feed/all.xml</a> · <a href="feed/all.json">feed/all.json</a></li>%s</ul></body></html>\n'
                         % (xml_escape(title), xml_escape(title), len(items), len(shelf_list),
                            '<li><a href="https://github.com/%s">GitHub</a></li>' % xml_escape(repo) if repo else ''))
    out['.nojekyll'] = ''
    docs = docs_dir()
    changed = 0
    for rel, text in out.items():
        changed += write_text(os.path.join(docs, *rel.split('/')), text)
    feed_root = os.path.join(docs, 'feed')                    # feeds of a tag or shelf that no longer exists: gone. Nothing else in docs/ (a CNAME file, say) is touched.
    for d, dns, fns in os.walk(feed_root):
        for f in fns:
            rel = os.path.relpath(os.path.join(d, f), docs).replace(os.sep, '/')
            if rel not in out:
                os.remove(os.path.join(d, f))
                changed += 1
    for d, dns, fns in os.walk(feed_root, topdown=False):
        if not os.listdir(d):
            os.rmdir(d)
    print('build: %d items, %d shelves, %d tags, %d files (%d changed) in docs/' % (len(items), len(shelf_list), len(tags), len(out), changed))
    return out


# ======================================================================= issues (GitHub Action)
KIND_NAMES = {
    ('feed', ''): ('订阅源书架（整个网站 / 博客 / 频道）', 'Feed shelf (a whole site, blog or channel)'),
    ('repo', ''): ('GitHub 仓库书架', 'GitHub repository shelf'),
    ('link', 'page'): ('单篇网页', 'A single page'), ('link', 'video'): ('单个视频', 'A single video'),
    ('link', 'doc'): ('Markdown / 文本文档', 'A Markdown / text document'), ('link', 'pdf'): ('PDF 文件', 'A PDF file'),
    ('link', 'gist'): ('Gist 文章', 'A gist'), ('link', 'file'): ('GitHub 上的单个文件', 'A single file on GitHub'),
}


def md_esc(s):
    s = re.sub(r'([\\`*_{}\[\]<>|~])', r'\\\1', str(s))
    return s.replace('@', '@\u200b').replace('\n', ' ')


def md_link(text, url):
    for ch, rep in ((')', '%29'), ('(', '%28'), (' ', '%20'), ('<', '%3C'), ('>', '%3E'), ('"', '%22')):
        url = url.replace(ch, rep)
    return '[%s](%s)' % (md_esc(text), url)


def issue_fields(body):
    """GitHub issue forms write '### Label' headings followed by the answer."""
    secs, cur = {}, None
    for line in (body or '').replace('\r', '').split('\n'):
        m = re.match(r'^###\s+(.+?)\s*$', line)
        if m:
            cur = m.group(1)
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(line)
    return {k: '\n'.join(v).strip() for k, v in secs.items()}


def read_submission(event):
    """-> (url, tags, rights ok, issue number). Only the text of the form is used, and only as data."""
    issue = (event or {}).get('issue') or {}
    body = issue.get('body') or ''
    f = issue_fields(body)
    pick = lambda *pats: next((v for k, v in f.items() if any(re.search(p, k, re.I) for p in pats)), '')
    url_text = pick(r'网址', r'\burl\b', r'link', r'地址') or body
    m = re.search(r'https?://[^\s<>"\')\]]+', url_text)
    if m:
        url = m.group(0)
    else:                                                       # "example.com/blog" typed without https://
        first = next((l.strip() for l in url_text.splitlines() if l.strip()), '')
        url = first if re.match(r'^[\w.-]+\.[A-Za-z]{2,}(/\S*)?$', first) else ''
    url = url.rstrip('.,;:!?，。；')
    rights = pick(r'权利', r'right')
    ok = bool(rights) and not re.match(r'^_?no response_?$', rights, re.I) and not (re.search(r'\[[ ]\]', rights) and not re.search(r'\[[xX]\]', rights))
    tags = [t for t in re.split(r'[,，、\n]', pick(r'标签', r'tag')) if t.strip() and not re.match(r'^_?no response_?$', t.strip(), re.I)]
    return url.strip(), tags, ok, issue.get('number')


def describe(r):
    e, s = r['entry'], r['shelf']
    zh, en = KIND_NAMES.get((e['kind'], e.get('sub', '')), (e['kind'], e['kind']))
    n = len(r['items'])
    src = e.get('source') or e['url']
    lines = ['**识别为 / Recognised as：** %s · %s' % (zh, en), '', '| | |', '|---|---|', '| 名称 / Name | %s |' % md_esc(s.get('name', '')),
             '| 来源 / Source | %s |' % (md_link(src, src) if src.startswith('http') else md_esc(src))]
    lines += ['| 找到 / Found | %d |' % n, '| 语言 / Language | %s |' % md_esc(s.get('lang', ''))]
    if s.get('description'):
        lines.append('| 简介 / About | %s |' % md_esc(s['description']))
    if s.get('license'):
        lines.append('| 许可 / License | %s |' % md_esc(s['license']))
    tags = norm_tags(sorted({t for i in r['items'] for t in i['tags']}, key=lambda t: -sum(t in i['tags'] for i in r['items'])), 8)
    if tags:
        lines.append('| 标签 / Tags | %s |' % md_esc(', '.join(tags)))
    lines += ['', '最新几条 / Latest:']
    for i in sorted(r['items'], key=lambda i: i['date'], reverse=True)[:5]:
        lines.append('- %s %s%s' % (md_link(i['title'], i['url']), '· ' + i['date'] if i['date'] else '', ' · `%s`' % i['type']))
    for zhw, enw in r['warnings']:
        lines += ['', '⚠️ %s / %s' % (md_esc(zhw), md_esc(enw))]
    return '\n'.join(lines)


def handle_issue(event, mode):
    """mode 'check' (opened / edited) or 'approve' (the maintainer added the label). -> dict for the workflow."""
    url, tags, rights, number = read_submission(event)
    res = {'number': number, 'ok': False, 'comment': '', 'add': [], 'remove': [], 'close': False}
    try:
        if not url:
            raise Refused('没有在表单里找到网址。请用「收录申请」表单提交。', 'No address found. Please use the submission form.')
        if not rights:
            raise Refused('请在表单里确认「我有权发布这些内容」。', 'Please confirm that you have the right to publish this content in the form.')
        if mode == 'approve':
            e, r = approve(url, tags, number)
            res.update({'ok': True, 'close': True, 'add': ['listed'], 'remove': ['checked', 'invalid'],
                        'comment': '✅ 已收录，编号 `%s`，每天自动更新。/ Listed as `%s`; it is refreshed every day.\n\n%s' % (e['id'], e['id'], describe(r))})
        else:
            r = probe(url)
            tail = ('ℹ️ 这个地址已经在目录里（`%s`）。/ Already listed.' % r['existing']) if r['existing'] else \
                   '✅ **自动检查通过，等待维护者审核。** 审核通过后维护者会给这个 issue 加上 `approved` 标签，之后会自动进入目录。\n\n' \
                   '✅ **Automatic checks passed; waiting for a maintainer.** Once approved (label `approved`) it is listed automatically.'
            res.update({'ok': True, 'add': ['checked'], 'remove': ['invalid'], 'comment': describe(r) + '\n\n' + tail})
    except Refused as ex:
        res.update({'ok': False, 'add': ['invalid'], 'remove': ['checked'],
                    'comment': '❌ **暂时无法收录 / Cannot be listed:** %s\n\n%s\n\n修改上面的网址后（编辑本 issue）会自动重新检查。Edit the address above and it is checked again.' % (md_esc(str(ex)), md_esc(ex.en))})
    return res


# ======================================================================= command line
def serve(port):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=docs_dir(), **k)

        def end_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-store')
            super().end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), H)
    print('docs/ on http://127.0.0.1:%d/  (CORS open, Ctrl+C to stop)' % port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def show(r):
    e, s = r['entry'], r['shelf']
    zh, en = KIND_NAMES.get((e['kind'], e.get('sub', '')), (e['kind'], ''))
    print('%s  [%s]' % (zh, e['kind']))
    print('  name     :', s.get('name'))
    print('  source   :', e.get('source') or e['url'])
    print('  language :', s.get('lang'), '   license:', s.get('license') or '-', '   tags:', ', '.join(s.get('tags') or []) or '-')
    print('  found    :', len(r['items']), 'item(s)' + ('   (already listed as %s)' % r['existing'] if r['existing'] else ''))
    for i in r['items'][:5]:
        print('   -', i['date'] or '          ', i['type'].ljust(7), i['title'])
    for zhw, enw in r['warnings']:
        print('  warning  :', zhw)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('probe')
    p.add_argument('url')
    p = sub.add_parser('issue')
    p.add_argument('--event', required=True)
    p.add_argument('--out', required=True)
    p = sub.add_parser('approve')
    p.add_argument('url', nargs='?')
    p.add_argument('--tags', default='')
    p.add_argument('--event')
    p.add_argument('--out')
    p = sub.add_parser('remove')
    p.add_argument('id')
    p = sub.add_parser('fetch')
    p.add_argument('--only')
    sub.add_parser('build')
    sub.add_parser('run')
    p = sub.add_parser('serve')
    p.add_argument('--port', type=int, default=8770)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'probe':
            show(probe(a.url))
        elif a.cmd == 'issue':
            res = handle_issue(read_json(a.event, {}), 'check')
            write_json(a.out, res)
            print(res['comment'])
        elif a.cmd == 'approve' and a.event:
            res = handle_issue(read_json(a.event, {}), 'approve')
            if res['ok']:
                build()
            write_json(a.out or os.devnull, res)
            print(res['comment'])
            return 0 if res['ok'] else 1
        elif a.cmd == 'approve':
            if not a.url:
                ap.error('give an address, or --event FILE')
            e, r = approve(a.url, [t for t in re.split(r'[,，]', a.tags) if t.strip()])
            show(r)
            build()
            print('listed as', e['id'])
        elif a.cmd == 'remove':
            remove(a.id)
            build()
            print('removed', a.id)
        elif a.cmd == 'fetch':
            fetch_all(a.only)
        elif a.cmd == 'build':
            build()
        elif a.cmd == 'run':
            fetch_all()
            build()
        elif a.cmd == 'serve':
            serve(a.port)
    except Refused as e:
        print('Refused: %s\n         %s' % (e, e.en), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
