#!/usr/bin/env python3
"""aggregate.py: the aggregator behind the public shelf directory. Python 3.9+, standard library only.

  python aggregate.py probe URL                      what is this address, and can it be listed? (writes nothing)
  python aggregate.py issue --event FILE --out FILE  check a submission issue (used by the GitHub Action)
  python aggregate.py approve URL [--tags a,b]       register a checked address (what the "approved" label does)
  python aggregate.py approve --event FILE --out FILE
  python aggregate.py reject --event FILE --out FILE remember a refused submission (its hash only) and close it
  python aggregate.py apply --result FILE            post the result of `issue` / `approve` / `reject` in the issue (GitHub Action)
  python aggregate.py remove ID | post:SLUG          take a shelf, or a hosted post, out of the directory
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
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from xml.sax.saxutils import escape as xml_escape

import mdsafe

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---- limits: everything fetched from the internet is untrusted and bounded
UA_TOKEN = 'ShelfDirectoryBot'
UA = UA_TOKEN + '/1 (link directory; reads public feeds and page metadata only)'
MAX_BYTES = 2 * 1024 * 1024        # one response
TIMEOUT = 15                       # seconds, per network operation
DEADLINE = None                    # time.monotonic() value by which all reading must be over (None: no limit); set by the submit function
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
TYPES = ('article', 'note', 'video', 'audio', 'pdf', 'tool', 'book')
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
    if os.path.dirname(path):                                  # "result.json" has no folder: makedirs('') would fail (this broke every workflow run)
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
    b = str(os.environ.get('SHELF_BASE_URL') or config().get('base_url') or '').strip()
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


TRACKING = re.compile(r'^(utm_.*|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|spm|share_token|xtor|_hsenc|_hsmi|yclid|ref_src|vero_id|oly_enc_id|oly_anon_id)$', re.I)


def url_key(url):
    """Two addresses with the same key are the same page: http / https, www., the default port, a trailing slash, index.html, a fragment,
    the order of the parameters and tracking parameters (utm_*, fbclid, ...) do not count. youtu.be/ID is youtube.com/watch?v=ID, github.com/o/r.git is github.com/o/r."""
    try:
        p = urllib.parse.urlsplit(str(url).strip())
        host = (p.hostname or '').lower()
    except ValueError:
        return str(url).strip().lower()
    host = re.sub(r'^www\.', '', host)
    path = re.sub(r'/{2,}', '/', urllib.parse.unquote(p.path))
    path = re.sub(r'/index\.(html?|php)$', '', path).rstrip('/')
    query = sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k))
    segs = [s for s in path.split('/') if s]
    if host == 'm.youtube.com':
        host = 'youtube.com'
    if host == 'youtu.be' and segs:
        host, path, query = 'youtube.com', '/watch', [('v', segs[0])]
    elif host == 'youtube.com':
        if segs[:1] in (['shorts'], ['embed']) and len(segs) > 1:
            path, query = '/watch', [('v', segs[1])]
        elif path == '/watch':
            query = [kv for kv in query if kv[0] == 'v']
    elif host == 'github.com':
        path = re.sub(r'\.git$', '', path).lower()
    return host + path + ('?' + urllib.parse.urlencode(query) if query else '')


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


def check_url_syntax(url):
    """What check_url refuses without asking the network (nothing is resolved or fetched): for the public submit function, which must never
    touch an address an outsider names. https, port 443, no credentials, and no IP literal or name of this machine / a private network."""
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError:
        raise Refused('网址格式不对', 'Malformed address')
    host = (p.hostname or '').lower()
    if p.scheme != 'https':
        raise Refused('只支持 https 网址', 'Only https addresses are supported')
    if not host or p.username or p.password or not re.fullmatch(r'[\w.:-]+', host) or ('.' not in host and ':' not in host):
        raise Refused('网址格式不对', 'Malformed address')
    if port not in (None, 443):
        raise Refused('只支持标准的 https 端口（443）', 'Only the standard https port (443) is supported')
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if (literal and not is_public_ip(host)) or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.localdomain')):
        raise Refused('不允许访问内网或本机地址', 'Private and local addresses are not allowed')


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


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Resolves the name once, HERE, refuses it unless every address is public, and connects to one of those addresses itself.
    check_url alone leaves a gap (a name can answer "public" to the check and "10.0.0.5" to the connection a moment later: DNS rebinding);
    this closes it. TLS still verifies the certificate against the name."""

    def connect(self):
        port = self.port or 443
        try:
            infos = socket.getaddrinfo(self.host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise Refused('找不到这个网站（域名无法解析）', 'The host name does not resolve')
        if not infos or any(not is_public_ip(i[4][0]) for i in infos):
            raise Refused('不允许访问内网或本机地址', 'Private and local addresses are not allowed')
        last = None
        for info in infos:
            try:
                sock = socket.create_connection((info[4][0], port), self.timeout)
                break
            except OSError as e:
                last = e
        else:
            raise last
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


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
    timeout = TIMEOUT
    if DEADLINE is not None:                                    # a caller with a time limit of its own (the submit function): the whole reading must end by then
        left = DEADLINE - time.monotonic()
        if left <= 0:
            raise Refused('读取这个网站花的时间太长了', 'Reading this site took too long')
        timeout = min(TIMEOUT, left)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Encoding': 'identity', **(headers or {})})
    handlers = [_Redirect]
    if ALLOW_LOCAL:
        handlers.append(urllib.request.ProxyHandler({}))
    elif not urllib.request.getproxies().get('https'):          # no proxy (the Action, a Vercel function): connect only to the address that was checked
        handlers.append(_PinnedHTTPSHandler())
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as r:
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
        if es.get('listed') is False:                  # the owner keeps this one on the site but out of every directory
            continue
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


def strip_site_suffix(title, site=''):
    """"Article title - Site name" -> "Article title": a short tail after a separator is the site's name (only for a title long enough to lose it)."""
    t = clean(title)
    if len(t) < 20:
        return t
    m = re.match(r'^(.{12,}?)\s*(?:[-–—|·_｜]|\s[-–—|·]\s)\s*([^-–—|·_｜]{1,16})$', t)
    if m and (not site or site.lower() in m.group(2).lower() or m.group(2).lower() in site.lower() or len(m.group(2)) <= 10):
        return m.group(1).strip()
    return t


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
    keywords = [k for k in re.split(r'[,，、;；]', m.get('keywords', '')) if k.strip()]
    tags = list(p.multi.get('article:tag', [])) + (keywords if len(keywords) <= 6 else [])        # a long keywords list is the whole site's, not this page's
    return {'title': strip_site_suffix(first('og:title', 'twitter:title') or clean(p.title), first('og:site_name', 'application-name')), 'description': first('og:description', 'description', 'twitter:description'),
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
            if v.startswith('"') and v.endswith('"') and len(v) > 1:          # a double-quoted scalar is JSON (that is how hosted posts are written)
                try:
                    meta[cur] = json.loads(v)
                    continue
                except ValueError:
                    pass
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
    query = '&'.join(part for part in p.query.split('&') if part and not TRACKING.match(part.split('=')[0]))      # ?utm_source=x is not a different page
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc, p.path, query, ''))


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
    if info.get('fork'):                                  # a copy lists the same files as the original
        parent = (info.get('parent') or {}).get('full_name') or (info.get('source') or {}).get('full_name') or ''
        raise Refused('这是一个 fork（复制品）%s，请改为收录原仓库' % ('，原仓库是 ' + parent if parent else ''),
                      'This is a fork%s; list the original repository instead' % (' of ' + parent if parent else ''))
    ref = spec.get('ref') or info.get('default_branch') or 'main'
    tree = gh_json('/repos/%s/%s/git/trees/%s?recursive=1' % (urllib.parse.quote(o), urllib.parse.quote(r), urllib.parse.quote(ref)), 8 * 1024 * 1024)
    prefix = (spec.get('path') or '').strip('/')
    pre = prefix + '/' if prefix else ''
    cands, shas = [], {}
    for e in tree.get('tree') or []:
        p = e.get('path') or ''
        if e.get('type') != 'blob' or (pre and not p.startswith(pre)):
            continue
        shas[p] = e.get('sha') or ''
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
    seen_sha, unique = set(), []                          # git gives every file a hash of its content: the same text twice in one repository is listed once
    for p in cands:
        if shas.get(p) and shas[p] in seen_sha:
            continue
        seen_sha.add(shas.get(p))
        unique.append(p)
    cands = unique
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
            it['sha'] = shas.get(p, '')                       # also lets the build drop a file that another shelf lists with the same content
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
        seen, failed, shared = set(), [], ''
        sub_dir = bool(urllib.parse.urlsplit(final).path.strip('/'))              # a folder of a site (example.com/blog/), not the site itself
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
                if sub_dir and find_entry(load_registry(), {'kind': 'feed', 'url': url, 'source': fp[0]}):
                    shared = fp[0]                                                 # the feed of the whole site, which is listed: this folder is judged as a link of its own
                    break
                return {'kind': 'feed', 'url': url, 'source': fp[0]}, fp, []
            if f in linked:
                failed.append((f, '不是订阅源'))
        if shared:
            warnings.append(('这个目录用的是整个网站的订阅源（%s），而这个网站已经收录了，所以只把这个地址作为一个链接收录' % shared,
                             'This folder uses the feed of the whole site (%s), which is listed already, so only this address is listed, as a link' % shared))
        elif disallowed:
            raise Refused('这个网站的 robots.txt 不允许读取这个页面，也没有找到订阅源。可以改贴它的订阅源（RSS / Atom）地址', 'robots.txt does not allow reading this page and no feed was found; give the feed address instead')
        elif failed:
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


def _repo_key(spec):
    return tuple(str(spec.get(k) or '').lower().strip('/') for k in ('owner', 'repo', 'ref', 'path'))


def find_entry(reg, entry):
    """The registered shelf that is this very source (addresses compared with url_key, so www / https / a trailing slash do not make a new one)."""
    for e in reg['shelves']:
        if e['kind'] != entry['kind']:
            continue
        if entry['kind'] == 'repo':
            if _repo_key(e.get('repo') or {}) == _repo_key(entry.get('repo') or {}):
                return e
        elif url_key(e.get('source') or '') == url_key(entry.get('source') or '') or (entry['kind'] == 'feed' and entry.get('url') and url_key(e.get('url') or '') == url_key(entry['url'])):
            return e
    return None


DUPLICATE_TEXT = {
    'shelf': ('已经在目录里了（%s）', 'Already in the directory (%s)'),
    'repo': ('这个仓库（或它的上层文件夹）已经收录了（%s）', 'This repository (or a folder above it) is listed already (%s)'),
    'item': ('这一篇已经收录了（在书架 %s 里）', 'This one is listed already (in shelf %s)'),
}


def find_duplicate(entry, items):
    """What the directory already has of this. -> (kind, shelf id) or None.
    'shelf': the same source; 'repo': the repository (or a folder above) is listed; 'item': a single page that another shelf lists already."""
    reg = load_registry()
    e = find_entry(reg, entry)
    if e:
        return 'shelf', e['id']
    if entry['kind'] == 'repo':
        want = _repo_key(entry['repo'])
        for r in reg['shelves']:
            if r['kind'] == 'repo' and _repo_key(r['repo'])[:2] == want[:2]:
                have_path, path = _repo_key(r['repo'])[3], want[3]
                if not have_path or path == have_path or path.startswith(have_path + '/'):
                    return 'repo', r['id']
    if entry['kind'] == 'link':
        keys = {url_key(i['url']) for i in items}
        for r in reg['shelves']:
            snap = load_snapshot(r['id'])
            if snap and any(url_key(i['url']) in keys for i in snap.get('items', [])):
                return 'item', r['id']
    return None


# ======================================================================= the same thing under another address: title, author, summary
SIMILAR_SUMMARY, SIMILAR_SUMMARY_LOOSE, MIN_SUMMARY, SUMMARY_COMPARED = 0.8, 0.4, 20, 200
GENERIC_TITLES = {'home', 'index', 'readme', 'about', 'blog', 'untitled', 'introduction', '首页', '主页', '博客', '关于', '无标题', '简介'}
SIMILAR_TEXT = {'summary': ('摘要几乎一样', 'the summary is almost the same'), 'title': ('标题和作者相同', 'the title and the author are the same')}


def norm_words(s):
    """Letters and digits only, lower case, full-width forms folded: what is left when spacing, punctuation and brackets are ignored."""
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(s or '')).lower())


def summary_similarity(a, b):
    """0..1: how much of the opening of two summaries is the same (overlap of 3-character pieces). 0 when either is too short to say anything."""
    a, b = (norm_words(clean(x, SUMMARY))[:SUMMARY_COMPARED] for x in (a, b))      # the directory keeps 160 characters of a summary: both sides are cut the same way first
    if len(a) < MIN_SUMMARY or len(b) < MIN_SUMMARY:
        return 0.0
    sa, sb = {a[i:i + 3] for i in range(len(a) - 2)}, {b[i:i + 3] for i in range(len(b) - 2)}
    return len(sa & sb) / len(sa | sb)


def similar_to(a, b):
    """Are two entries (dicts with title, author, summary) the same thing, kept under two addresses? -> '' or 'summary' / 'title'.
    A summary that is almost the same is enough. The same title is enough when the authors are the same (or one is unknown and the summaries
    do not speak against it); a title that says nothing (Home, README) never is, and neither is one with a different author."""
    sim = summary_similarity(a.get('summary'), b.get('summary'))
    if sim >= SIMILAR_SUMMARY:
        return 'summary'
    ta = norm_words(a.get('title'))
    if len(ta) >= 4 and ta == norm_words(b.get('title')) and ta not in GENERIC_TITLES:
        aa, ab = norm_words(a.get('author')), norm_words(b.get('author'))
        if aa and ab:
            return 'title' if aa == ab else ''
        both = len(norm_words(a.get('summary'))) >= MIN_SUMMARY and len(norm_words(b.get('summary'))) >= MIN_SUMMARY
        return 'title' if sim >= SIMILAR_SUMMARY_LOOSE or not both else ''
    return ''


def find_similar(cand, pool, level=''):
    """The first entry of `pool` (dicts: id, title, author, summary, level 'shelf' or 'item') that `cand` repeats. -> (reason, id, title) or None."""
    for e in pool:
        if level and e.get('level') != level:
            continue
        why = similar_to(cand, e)
        if why:
            return why, str(e.get('id') or ''), str(e.get('title') or '')
    return None


def registry_pool():
    """Everything this directory has, as entries to compare with: each shelf (name, about), the items read from them, hosted posts and records."""
    pool = []
    for e in load_registry()['shelves']:
        snap = load_snapshot(e['id']) or {}
        sh = snap.get('shelf') or {}
        pool.append({'level': 'shelf', 'id': e['id'], 'title': sh.get('name') or e.get('name') or '', 'author': '', 'summary': sh.get('description') or ''})
        pool += [{'level': 'item', 'id': e['id'], 'title': i.get('title') or '', 'author': i.get('author') or '', 'summary': i.get('summary') or ''} for i in snap.get('items') or []]
    pool += [{'level': 'item', 'id': 'post:' + p['slug'], 'title': p['title'], 'author': p['author'], 'summary': clean(mdsafe.plain_text(p['body']), SUMMARY)} for p in load_posts()]
    return pool


def candidate_of(r):
    """What a read address is, as one entry to compare: a site or repository by its name and about, a single page by its title and summary. -> (entry, level)."""
    if r['entry']['kind'] in ('feed', 'repo'):
        return {'title': r['shelf'].get('name', ''), 'author': '', 'summary': r['shelf'].get('description', '')}, 'shelf'
    i = (r['items'] or [{}])[0]
    return {'title': i.get('title', ''), 'author': '', 'summary': i.get('summary', '')}, 'item'


# ======================================================================= a page that cannot be read says nothing against its address
def soft_failure(ex):
    """The reasons for not being able to read a page that do not make the link itself bad: a site that does not let programs in (401, 403, 429),
    one that is down or slow, a robots.txt that says no, a page that needs a script. A dead page (404, 410) or an unsafe address is not one."""
    code = getattr(ex, 'code', None)
    if code is not None:
        return code in (401, 403, 429, 451) or code >= 500
    s = str(ex)
    return s.startswith('连不上这个网站') or 'robots.txt' in s or s.startswith(('读不到这个页面的标题', '没有读到可以收录的内容'))


def unread_probe(raw, ex):
    """The answer for an address that could not be read: the link is listed as it is, with a title guessed from the address, and a warning for the maintainer."""
    url = normalize_input(raw)
    if not ALLOW_LOCAL:
        check_url_syntax(url)                                      # the address itself still has to be a safe one
    sub = {'doc': 'doc', 'pdf': 'pdf', 'video': 'video'}.get(classify(url)['kind'], 'page')
    entry = {'kind': 'link', 'sub': sub, 'url': url, 'source': url}
    if blocked(entry) or rejected(url_key(url)):
        raise Refused('这个网站不在收录范围内', 'This site is not accepted')
    p = urllib.parse.urlsplit(url)
    host = re.sub(r'^www\.', '', p.hostname or '')
    path = urllib.parse.unquote(p.path).strip('/')
    item = make_item(url, humanize(path)[0] if path else host)
    shelf = {'name': host, 'description': '', 'home': '%s://%s/' % (p.scheme, p.netloc), 'lang': '', 'license': '', 'tags': []}
    dup = find_duplicate(entry, [item])
    warn = ('读不到这个页面（%s），标题是按网址猜的。请维护者手动确认这个链接' % ex, 'This page could not be read (%s); its title is guessed from the address. The maintainer checks the link by hand' % ex.en)
    return {'entry': entry, 'shelf': shelf, 'items': [item], 'warnings': [warn], 'duplicate': dup, 'existing': dup[1] if dup and dup[0] == 'shelf' else '', 'unread': True, 'similar': None}


def registered_by_address(url):
    """A registered source that has this very address (compared with url_key: www, https, a trailing slash, index.html and tracking parameters do not matter).
    Looked up before anything is fetched, so that an address of a listed site is a duplicate even when its other spelling cannot be opened."""
    key = url_key(url)
    for e in load_registry()['shelves']:
        if key in (url_key(e.get('url') or ''), url_key(e.get('source') or '')):
            return e
    return None


def probe(raw):
    """Everything a maintainer needs to decide: what it is, what was found, and why not if it cannot be listed."""
    quick = registered_by_address(normalize_input(raw))
    if quick:                                                  # already listed: the answer comes from what is kept, with no request to the site
        snap = load_snapshot(quick['id']) or {}
        return {'entry': quick, 'shelf': snap.get('shelf') or {'name': quick.get('name', ''), 'lang': '', 'description': '', 'license': ''}, 'items': snap.get('items') or [],
                'warnings': [], 'duplicate': ('shelf', quick['id']), 'existing': quick['id'], 'similar': None}
    try:
        entry, pre, warnings = discover(raw)
        if blocked(entry) or rejected(url_key(entry.get('source') or entry['url'])):
            raise Refused('这个网站不在收录范围内', 'This site is not accepted')
        res = read_source(entry, None, pre)
    except Refused as ex:
        if soft_failure(ex) and classify(normalize_input(raw))['kind'] in ('web', 'doc', 'pdf', 'video'):      # only the link is judged: it is safe, so it can be listed unread
            return unread_probe(raw, ex)
        raise
    entry.setdefault('source', entry['url'])
    if entry['kind'] == 'repo':
        entry['source'] = 'github:%s/%s' % (entry['repo']['owner'], entry['repo']['repo'])
    dup = find_duplicate(entry, res['items'])
    out = {'entry': entry, 'shelf': res['shelf'], 'items': res['items'], 'warnings': warnings + res.get('warnings', []),
           'duplicate': dup, 'existing': dup[1] if dup and dup[0] == 'shelf' else '', 'similar': None}
    if not dup:                                                # nothing with the same address: is it the same thing under another one?
        cand, level = candidate_of(out)
        out['similar'] = find_similar(cand, registry_pool(), level)
    return out


def approve(raw, tags=None, issue=None):
    r = probe(raw)
    if r['duplicate']:
        zh, en = DUPLICATE_TEXT[r['duplicate'][0]]
        raise Refused(zh % r['duplicate'][1], en % r['duplicate'][1])
    reg = load_registry()
    e = r['entry']
    e['id'] = make_id(e, {x['id'] for x in reg['shelves']})
    e['name'] = r['shelf']['name']
    e['tags'] = norm_tags(tags, 5)
    e['added'] = today().isoformat()
    if issue:
        e['issue'] = issue
    if r.get('unread'):
        e['bare'] = True                                       # listed without having been read: what is kept of it is the address and a guessed title
    reg['shelves'].append({k: e[k] for k in ('id', 'kind', 'sub', 'url', 'source', 'repo', 'gist', 'name', 'tags', 'added', 'issue', 'bare') if k in e})
    write_json(reg_path(), reg)
    if r.get('unread'):
        write_json(os.path.join(snap_dir(), e['id'] + '.json'), {'id': e['id'], 'kind': e['kind'], 'parser': PARSER, 'status': 'ok', 'error': '', 'fails': 0, 'shelf': r['shelf'], 'items': r['items']})
    else:
        fetch_all(only=e['id'])
    return e, r


def remove(sid):
    """A shelf (its id), or a hosted post ('post:<slug>')."""
    if (sid or '').startswith('post:'):
        name = sid[5:]
        path = os.path.join(posts_dir(), name + '.md')
        if not re.fullmatch(r'[\w.-]+', name) or not os.path.isfile(path):
            raise Refused('没有这篇投稿：' + name, 'No such post: ' + name)
        os.remove(path)
        return
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
            if e.get('bare') and soft_failure(ex) and prev and prev.get('items'):      # listed unread, still unreadable: what is kept stays, and this is no failure
                snap.update({'status': 'ok', 'error': '', 'fails': 0})
                ok += 1
                log('keep  %-28s (still cannot be read: %s)' % (e['id'], ex))
            else:
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
    shelves, items, seen, seen_sha = {}, [], set(), set()
    sources = []
    for e in reg['shelves']:
        s = load_snapshot(e['id'])
        if s and s.get('items') and s.get('fails', 0) < DOWN_AFTER:
            sources.append((e, s))
    posts, post_pages = load_posts(), {}
    if posts and base:                                       # hosted posts are one more shelf; each has its own static page
        post_items = []
        for p in posts:
            post_items.append({'url': '%sp/%s/' % (base, urllib.parse.quote(p['slug'])), 'title': p['title'], 'summary': clean(mdsafe.plain_text(p['body']), SUMMARY), 'image': '',
                               'date': p['date'], 'tags': p['tags'], 'lang': p['lang'], 'type': 'book' if p['kind'] == 'record' else 'article', 'sha': 'post:' + p['hash'], 'hash': p['hash'],
                               'author': p['author']})
            post_pages['p/%s/index.html' % p['slug']] = post_page(p, cfg, repo)
        pname = cfg.get('posts_title') or '社区投稿'
        sources.append(({'id': 'posts', 'kind': 'post', 'name': pname, 'tags': [], 'source': ''},
                        {'shelf': {'name': pname, 'description': '读者投稿、由本站托管的文章', 'home': base, 'lang': '', 'license': ''}, 'items': post_items, 'status': 'ok'}))
    elif posts:
        print('build: %d hosted post(s) NOT published: set "base_url" in registry/config.json (or SHELF_BASE_URL) so that their addresses are known' % len(posts))
    for e, s in sources:
        info = s.get('shelf') or {}
        stags = norm_tags(list(e.get('tags') or []) + list(info.get('tags') or []), 5)
        shelves[e['id']] = {'id': e['id'], 'name': e.get('name') or info.get('name') or e['id'], 'description': info.get('description', ''), 'home': info.get('home', ''),
                            'kind': e['kind'], 'lang': info.get('lang') or '', 'license': info.get('license', ''), 'tags': stags, 'n': 0, 'updated': '',
                            'feed': e['source'] if e['kind'] == 'feed' else '', 'status': s.get('status', 'ok')}
        for i in s['items']:
            k = url_key(i['url'])
            if k in seen or (i.get('sha') and i['sha'] in seen_sha):     # the same page, or (a repository file) the same content, from an earlier shelf
                continue
            seen.add(k)
            if i.get('sha'):
                seen_sha.add(i['sha'])
            tags, keys = [], set()
            for t in list(i.get('tags') or []) + stags:
                t = clean(alias.get(tag_key(t), t))
                if tag_key(t) not in keys and t:
                    keys.add(tag_key(t))
                    tags.append(t)
            row = {'id': hashlib.sha1(i['url'].encode()).hexdigest()[:10], 'title': i['title'], 'summary': i.get('summary', ''), 'url': i['url'], 'image': i.get('image', ''),
                   'date': i.get('date', ''), 'type': i.get('type') if i.get('type') in TYPES else 'article', 'lang': i.get('lang') or 'en', 'tags': tags[:TAGS_PER_ITEM], 'shelf': e['id']}
            if i.get('hash'):                                        # a hosted post: the submit function compares new texts with this
                row['hash'] = i['hash']
            if i.get('author'):                                      # and new ones with this title + author
                row['author'] = i['author']
            items.append(row)
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
    out.update(post_pages)
    out['directory.json'] = json.dumps({'spec': 1, 'title': cfg.get('title') or 'Shelf directory', 'repo': repo, 'submit_api': str(cfg.get('submit_api') or '').strip(), 'generated': day, 'base': base, 'count': len(items), 'types': types, 'shelves': shelf_list,
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
    for generated in ('feed', 'p'):                           # feeds and post pages that no longer exist: gone. Nothing else in docs/ (a CNAME file, the admin page) is touched.
        root = os.path.join(docs, generated)
        for d, dns, fns in os.walk(root):
            for f in fns:
                rel = os.path.relpath(os.path.join(d, f), docs).replace(os.sep, '/')
                if rel not in out:
                    os.remove(os.path.join(d, f))
                    changed += 1
        for d, dns, fns in os.walk(root, topdown=False):
            if not os.listdir(d):
                os.rmdir(d)
    print('build: %d items, %d shelves, %d tags, %d files (%d changed) in docs/' % (len(items), len(shelf_list), len(tags), len(out), changed))
    return out


# ======================================================================= hosted posts: text written by readers, kept in posts/, published as static pages in docs/p/
POST_TITLE_MAX, POST_AUTHOR_MAX, POST_BODY_MAX, POST_BODY_MIN, POST_LINKS_WARN = 120, 40, 50000, 60, 15
POST_NAME = re.compile(r'^[\w.-]+\.md$')


def posts_dir():
    return os.path.join(ROOT, 'posts')


def post_hash(body):
    """The same text sent again (other spacing, other case, other Markdown marks) has the same hash."""
    return hashlib.sha1(re.sub(r'\s+', ' ', mdsafe.plain_text(body)).strip().lower().encode('utf-8')).hexdigest()


def load_posts():
    d, out = posts_dir(), []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not POST_NAME.match(name):
            continue
        try:
            with open(os.path.join(d, name), 'r', encoding='utf-8-sig') as f:
                meta, body = front_matter(f.read())
        except OSError:
            continue
        title = clean(meta.get('title'), POST_TITLE_MAX) or name[:-3]
        out.append({'slug': name[:-3], 'title': title, 'author': clean(meta.get('author'), POST_AUTHOR_MAX), 'date': parse_date(meta.get('date')),
                    'tags': norm_tags(_list_value(meta.get('tags')), 5), 'lang': norm_lang(meta.get('lang'), title + ' ' + body[:300]), 'body': body,
                    'hash': post_hash(body), 'issue': str(meta.get('issue') or ''),
                    'kind': 'record' if str(meta.get('kind') or '') == 'record' else 'post', 'url': clean(meta.get('url')) if httpish(clean(meta.get('url'))) else ''})
    return out


def blockwords():
    try:
        with open(os.path.join(ROOT, 'registry', 'blockwords.txt'), 'r', encoding='utf-8-sig') as f:
            return [l.strip().lower() for l in f if l.strip() and not l.strip().startswith('#')]
    except OSError:
        return []


def check_post(sub):
    """Hard limits raise Refused; everything else is information for the person who decides. -> dict."""
    title, author, body = clean(sub.get('title')), clean(sub.get('author')), str(sub.get('body') or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not title:
        raise Refused('请填写标题', 'A title is needed')
    if len(title) > POST_TITLE_MAX or len(author) > POST_AUTHOR_MAX:
        raise Refused('标题最多 %d 个字，作者名最多 %d 个字' % (POST_TITLE_MAX, POST_AUTHOR_MAX), 'The title may have %d characters, the author name %d' % (POST_TITLE_MAX, POST_AUTHOR_MAX))
    text = mdsafe.plain_text(body)
    if len(text) < POST_BODY_MIN:
        raise Refused('正文太短（至少 %d 个字符）' % POST_BODY_MIN, 'The text is too short (at least %d characters)' % POST_BODY_MIN)
    if len(body) > POST_BODY_MAX:
        raise Refused('正文太长（最多 %d 个字符）' % POST_BODY_MAX, 'The text is too long (at most %d characters)' % POST_BODY_MAX)
    low = (title + ' ' + body).lower()
    if any(w in low for w in blockwords()):
        raise Refused('内容里有不允许的词', 'The text contains a word that is not accepted')
    h = post_hash(body)
    if rejected(h):
        raise Refused('这篇文章暂时不能收录', 'This text cannot be accepted')
    dup = next((p['slug'] for p in load_posts() if p['hash'] == h), '')
    if dup:
        raise Refused('这篇文章已经投过了（%s）' % dup, 'This text was submitted already (%s)' % dup)
    links = re.findall(r'https?://[^\s<>)\]]+', body)
    domains = sorted({(urllib.parse.urlsplit(u).hostname or '') for u in links})
    warnings = []
    if len(links) > POST_LINKS_WARN:
        warnings.append(('链接很多（%d 个），请留意是不是广告' % len(links), '%d links: check that it is not an advertisement' % len(links)))
    if links and len(text) < 40 * len(links):
        warnings.append(('链接比文字还多，像是链接列表', 'More links than text: it looks like a list of links'))
    return {'title': title, 'author': author, 'tags': norm_tags(sub.get('tags'), 5), 'chars': len(text), 'links': len(links), 'domains': domains[:8], 'warnings': warnings,
            'hash': h, 'excerpt': clean(text, 300), 'lang': norm_lang('', title + ' ' + text), 'body': body}


def describe_post(info):
    lines = ['**识别为 / Recognised as：** 读者投稿的文章（由本站托管） · A hosted post', '', '| | |', '|---|---|', '| 标题 / Title | %s |' % md_esc(info['title']),
             '| 作者 / Author | %s |' % (md_esc(info['author']) if info['author'] else '（未填 / none）'), '| 字数 / Length | %d |' % info['chars'],
             '| 语言 / Language | %s |' % info['lang'], '| 链接 / Links | %d%s |' % (info['links'], ('：' + md_esc(', '.join(info['domains']))) if info['domains'] else '')]
    if info['tags']:
        lines.append('| 标签 / Tags | %s |' % md_esc(', '.join(info['tags'])))
    lines += ['', '开头 / Beginning:', '', '> ' + md_esc(info['excerpt'])]
    for zhw, enw in info['warnings']:
        lines += ['', '⚠️ %s / %s' % (md_esc(zhw), md_esc(enw))]
    return '\n'.join(lines)


def publish_post(sub, number):
    """Write the post into posts/ (the build turns it into a page). -> (slug, info)."""
    info = check_post(sub)
    d = posts_dir()
    os.makedirs(d, exist_ok=True)
    base_slug = 'p%s-%s' % (number or 'x', slug(info['title'], 'post')[:40])
    name, n = base_slug, 1
    while os.path.exists(os.path.join(d, name + '.md')):
        n += 1
        name = '%s-%d' % (base_slug, n)
    head = ['---', 'title: ' + json.dumps(info['title'], ensure_ascii=False), 'author: ' + json.dumps(info['author'], ensure_ascii=False), 'date: ' + today().isoformat(),
            'tags: ' + json.dumps(info['tags'], ensure_ascii=False), 'lang: ' + info['lang']] + (['issue: %s' % number] if number else []) + ['---', '']
    write_text(os.path.join(d, name + '.md'), '\n'.join(head) + info['body'] + '\n')
    return name, info


# ----------------------------------------------------------------------- records: a catalogue card for something that has no address (a book, a PDF, a tool): title, author, summary
RECORD_SUMMARY_MIN, RECORD_SUMMARY_MAX = 10, 1000


def record_key(title, author=''):
    return 'record:%s|%s' % (norm_words(title), norm_words(author))


def check_record(sub):
    """Hard limits raise Refused; everything else is information for the person who decides. -> dict. The address is optional."""
    title, author, summary = clean(sub.get('title')), clean(sub.get('author')), clean(sub.get('summary'))
    if not title:
        raise Refused('请填写标题', 'A title is needed')
    if len(title) > POST_TITLE_MAX or len(author) > POST_AUTHOR_MAX:
        raise Refused('标题最多 %d 个字，作者名最多 %d 个字' % (POST_TITLE_MAX, POST_AUTHOR_MAX), 'The title may have %d characters, the author name %d' % (POST_TITLE_MAX, POST_AUTHOR_MAX))
    if len(summary) < RECORD_SUMMARY_MIN:
        raise Refused('请写一句摘要（至少 %d 个字）' % RECORD_SUMMARY_MIN, 'Please write a summary (at least %d characters)' % RECORD_SUMMARY_MIN)
    if len(summary) > RECORD_SUMMARY_MAX:
        raise Refused('摘要太长（最多 %d 个字）' % RECORD_SUMMARY_MAX, 'The summary is too long (at most %d characters)' % RECORD_SUMMARY_MAX)
    if any(w in (title + ' ' + author + ' ' + summary).lower() for w in blockwords()):
        raise Refused('内容里有不允许的词', 'The text contains a word that is not accepted')
    url = ''
    if clean(sub.get('url')):
        raw = clean(sub.get('url'))
        url = normalize_input('https://' + raw[7:] if raw.lower().startswith('http://') else raw)      # always the secure address of the same page
        check_url_syntax(url)
        if blocked({'url': url, 'source': url}) or rejected(url_key(url)):
            raise Refused('这个网址暂时不能收录', 'This address cannot be accepted')
    key = record_key(title, author)
    if rejected(key):
        raise Refused('这张资料卡暂时不能收录', 'This card cannot be accepted')
    dup = next((p['slug'] for p in load_posts() if p['kind'] == 'record' and record_key(p['title'], p['author']) == key), '')
    if dup:
        raise Refused('这张资料卡已经提交过了（%s）' % dup, 'This card was submitted already (%s)' % dup)
    return {'title': title, 'author': author, 'summary': summary, 'url': url, 'tags': norm_tags(sub.get('tags'), 5), 'key': key, 'lang': norm_lang('', title + ' ' + summary),
            'hash': post_hash(summary), 'warnings': []}


def describe_record(info):
    lines = ['**识别为 / Recognised as：** 资料卡（没有文件、只有这几项文字，由本站托管页面） · A catalogue card', '', '| | |', '|---|---|', '| 标题 / Title | %s |' % md_esc(info['title']),
             '| 作者 / Author | %s |' % (md_esc(info['author']) if info['author'] else '（未填 / none）'), '| 网址 / Address | %s |' % (md_link(info['url'], info['url']) if info['url'] else '（没有 / none）')]
    if info['tags']:
        lines.append('| 标签 / Tags | %s |' % md_esc(', '.join(info['tags'])))
    lines += ['', '摘要 / Summary:', '', '> ' + md_esc(info['summary'])]
    return '\n'.join(lines)


def publish_record(sub, number):
    """Write the card into posts/ (the build turns it into a page; it is a post whose text is the summary). -> (slug, info)."""
    info = check_record(sub)
    d = posts_dir()
    os.makedirs(d, exist_ok=True)
    base_slug = 'r%s-%s' % (number or 'x', slug(info['title'], 'card')[:40])
    name, n = base_slug, 1
    while os.path.exists(os.path.join(d, name + '.md')):
        n += 1
        name = '%s-%d' % (base_slug, n)
    head = ['---', 'kind: record', 'title: ' + json.dumps(info['title'], ensure_ascii=False), 'author: ' + json.dumps(info['author'], ensure_ascii=False), 'date: ' + today().isoformat(),
            'tags: ' + json.dumps(info['tags'], ensure_ascii=False), 'lang: ' + info['lang']] + (['url: ' + json.dumps(info['url'])] if info['url'] else []) \
        + (['issue: %s' % number] if number else []) + ['---', '']
    write_text(os.path.join(d, name + '.md'), '\n'.join(head) + info['summary'] + '\n')
    return name, info


POST_CSS = ('body{margin:0;background:#FBFBF9;color:#1A2229;font:17px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}'
            '.doc{max-width:44em;margin:0 auto;padding:40px 22px 90px}.meta{font-size:14px;color:#5A6670;margin:0 0 6px}'
            'h1,h2,h3,h4{line-height:1.35;margin:1.7em 0 .6em;font-family:"Noto Serif SC","Songti SC",Georgia,serif}h1{font-size:2em;margin-top:.3em}'
            'a{color:#1F7A6D}blockquote{border-left:3px solid #1F7A6D;margin:0 0 1.1em;padding:2px 0 2px 16px;color:#5A6670}'
            'pre{background:#F0F2F1;border-radius:8px;padding:14px 16px;overflow-x:auto}code{background:#F0F2F1;border-radius:4px;padding:.1em .35em}pre code{background:none;padding:0}'
            'table{border-collapse:collapse;display:block;overflow-x:auto}th,td{border-bottom:1px solid #DDE1DF;padding:8px 10px;text-align:left;vertical-align:top}'
            'hr{border:0;border-top:1px solid #DDE1DF;margin:2em 0}footer{margin-top:3em;padding-top:1em;border-top:1px solid #DDE1DF;font-size:15px;color:#5A6670}'
            '@media (prefers-color-scheme:dark){body{background:#15191C;color:#E2E7E9}.meta,blockquote,footer{color:#9AA7AE}a{color:#5CC2B1}pre,code{background:#1F262B}th,td,hr,footer{border-color:#2C363C}}')


def post_page(p, cfg, repo):
    """The static page of a hosted post. The text went through mdsafe, the rest is escaped here; the page carries no script."""
    site, title = str(cfg.get('site') or '').strip(), cfg.get('title') or 'Shelf directory'
    record = p.get('kind') == 'record'
    meta = ' · '.join(x for x in ('资料卡 / A catalogue card' if record else '读者投稿 / A reader\'s post', p['author'], p['date']) if x)
    take = 'https://github.com/%s/issues/new?template=takedown.yml' % repo if repo else ''
    links = ['<a href="%s">返回目录</a>' % xml_escape(site, {'"': '&quot;'})] if httpish(site) else []
    if take:
        links.append('<a href="%s" rel="nofollow">申请下架或修改</a>' % xml_escape(take, {'"': '&quot;'}))
    return ('<!DOCTYPE html>\n<html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex, follow"><meta name="referrer" content="no-referrer"><title>%s · %s</title><meta name="description" content="%s">'
            '<style>%s</style></head><body><article class="doc"><p class="meta">%s</p><h1>%s</h1>\n%s\n<footer>%s%s</footer></article></body></html>\n'
            % ('zh-CN' if p['lang'] == 'zh' else 'en', xml_escape(p['title']), xml_escape(title), xml_escape(clean(mdsafe.plain_text(p['body']), 160), {'"': '&quot;'}),
               POST_CSS, xml_escape(meta), xml_escape(p['title']),
               '<p>%s</p>' % xml_escape(p['body']).replace('\n', '<br>') + (('\n<p><a href="%s" rel="nofollow ugc noopener noreferrer">%s</a></p>' % (xml_escape(p['url'], {'"': '&quot;'}), xml_escape(p['url']))) if p['url'] else '') if record
               else mdsafe.render(p['body']),
               '这是读者提交的资料卡，只有标题、作者和摘要，不含书籍或文件本身，版权归原作者所有。' if record else '本文由读者投稿，版权归作者所有，不代表本站观点。', ' ' + ' · '.join(links) if links else ''))


def httpish(u):
    return bool(re.match(r'^https?://[^\s"<>]+$', str(u or '')))


# ======================================================================= what was refused: only hashes are kept, the text itself is not
def rejected_path():
    return os.path.join(ROOT, 'registry', 'rejected.json')


def key_hash(key):
    return hashlib.sha1(str(key).lower().encode('utf-8')).hexdigest()


def rejected(key):
    d = read_json(rejected_path(), {})
    return key_hash(key) in set(d.get('keys', [])) if isinstance(d, dict) else False


def remember_rejected(key):
    d = read_json(rejected_path(), {})
    d = d if isinstance(d, dict) else {}
    keys = list(d.get('keys', []))
    if key_hash(key) not in keys:
        keys.append(key_hash(key))
    write_json(rejected_path(), {'keys': keys})


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
    return s.replace('@', '@​').replace('\n', ' ')


def md_link(text, url):
    for ch, rep in ((')', '%29'), ('(', '%28'), (' ', '%20'), ('<', '%3C'), ('>', '%3E'), ('"', '%22')):
        url = url.replace(ch, rep)
    return '[%s](%s)' % (md_esc(text), url)


ISSUE_FIELDS = (('url', r'^(网址|url|address|link)'), ('title', r'^(标题|title)'), ('author', r'^(作者|author)'), ('tags', r'^(标签|tags?)'),
                ('summary', r'^(摘要|summary)'), ('body', r'^(正文|body|content)'), ('rights', r'^(权利|rights?)'))


def issue_fields(body):
    """GitHub issue forms write '### Label' followed by the answer. Only the heading of a KNOWN field that has not come yet starts a field:
    a '### ' line inside the text of a post is just text."""
    fields, cur, seen = {}, None, set()
    for line in (body or '').replace('\r', '').split('\n'):
        m = re.match(r'^###\s+(.+?)\s*$', line)
        key = next((k for k, pat in ISSUE_FIELDS if k not in seen and (cur != 'body' or k == 'rights') and re.match(pat, m.group(1), re.I)), None) if m else None   # in a post's text only the confirmation ends the text
        if key:
            cur = key
            seen.add(key)
            fields[key] = []
        elif cur is not None:
            fields[cur].append(line)
    return {k: '\n'.join(v).strip() for k, v in fields.items()}


def _answer(s):
    s = (s or '').strip()
    return '' if re.match(r'^_?no response_?$', s, re.I) else s


def parse_issue(event):
    """What a submission issue says (only as data). kind is 'post' when the form has a title and a text, 'record' when it has a title and a summary, else 'shelf'."""
    issue = (event or {}).get('issue') or {}
    body = issue.get('body') or ''
    f = issue_fields(body)
    kind = 'post' if f.get('title') and f.get('body') else 'record' if f.get('title') and f.get('summary') else 'shelf'
    url_text = f.get('url') if _answer(f.get('url')) else (body if kind == 'shelf' else '')
    m = re.search(r'https?://[^\s<>"\')\]]+', url_text)
    if m:
        url = m.group(0)
    else:                                                       # "example.com/blog" typed without https://
        first = next((l.strip() for l in url_text.splitlines() if l.strip()), '')
        url = first if re.match(r'^[\w.-]+\.[A-Za-z]{2,}(/\S*)?$', first) else ''
    rights = _answer(f.get('rights'))
    ok = bool(rights) and not (re.search(r'\[[ ]\]', rights) and not re.search(r'\[[xX]\]', rights))
    tags = [t.strip() for t in re.split(r'[,，、\n]', _answer(f.get('tags'))) if t.strip()]
    zw = lambda s: s.replace('​', '')                      # the submit function puts one after "###" in a line that looks like a heading of the form
    return {'kind': kind, 'number': issue.get('number'), 'url': url.rstrip('.,;:!?，。；').strip(), 'tags': [zw(t) for t in tags], 'rights': ok,
            'title': zw(_answer(f.get('title'))), 'author': zw(_answer(f.get('author'))), 'body': zw(_answer(f.get('body'))), 'summary': zw(_answer(f.get('summary')))}


def read_submission(event):
    """-> (url, tags, rights ok, issue number) of an address submission."""
    s = parse_issue(event)
    return s['url'], s['tags'], s['rights'], s['number']


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
    """mode 'check' (opened / edited), 'approve' or 'reject' (the maintainer added that label). -> dict for the workflow."""
    sub = parse_issue(event)
    number = sub['number']
    res = {'number': number, 'ok': False, 'comment': '', 'add': [], 'remove': [], 'close': False}
    try:
        if mode == 'reject':                                   # remember what it was, so that it is not asked again; the text itself is not kept
            if sub['kind'] == 'post' and sub['body']:
                remember_rejected(post_hash(sub['body']))
            elif sub['kind'] == 'record':
                remember_rejected(record_key(sub['title'], sub['author']))
            elif sub['url']:
                remember_rejected(url_key(sub['url']))
            res.update({'ok': True, 'close': True, 'add': ['rejected'], 'remove': ['checked', 'invalid', 'approved', 'duplicate', 'similar'],
                        'comment': '🚫 已拒绝，这个 issue 将被关闭。/ Rejected; this issue is closed.', 'redact': sub['kind'] != 'shelf'})   # a refused text does not stay in the public list
            return res
        if sub['kind'] == 'shelf' and not sub['url']:
            raise Refused('没有在表单里找到网址。请用「收录申请」表单提交。', 'No address found. Please use the submission form.')
        if not sub['rights']:
            raise Refused('请在表单里确认「我有权发布这些内容」。', 'Please confirm that you have the right to publish this content in the form.')
        if sub['kind'] == 'record':
            if mode == 'approve':
                name, info = publish_record(sub, number)
                res.update({'ok': True, 'close': True, 'add': ['listed'], 'remove': ['checked', 'invalid', 'duplicate', 'similar'],
                            'comment': '✅ 已发布，页面编号 `%s`。/ Published as `%s`.\n\n%s' % (name, name, describe_record(info))})
            else:
                info = check_record(sub)
                if info['url'] and registered_by_address(info['url']):
                    sid = registered_by_address(info['url'])['id']
                    res.update({'ok': False, 'add': ['duplicate'], 'remove': ['checked', 'invalid', 'similar'],
                                'comment': describe_record(info) + '\n\nℹ️ **重复 / Duplicate：** 这个网址已经在目录里了（%s）。/ This address is in the directory already (%s).' % (md_esc(sid), md_esc(sid))})
                else:
                    sim = find_similar({'title': info['title'], 'author': info['author'], 'summary': info['summary']}, registry_pool(), 'item')
                    if sim:
                        res.update(_similar_result(sim, describe_record(info)))
                    else:
                        res.update({'ok': True, 'add': ['checked'], 'remove': ['invalid', 'duplicate', 'similar'], 'comment': describe_record(info) + '\n\n' + TAIL_CHECKED})
        elif sub['kind'] == 'post':
            if mode == 'approve':
                name, info = publish_post(sub, number)
                res.update({'ok': True, 'close': True, 'add': ['listed'], 'remove': ['checked', 'invalid', 'duplicate'],
                            'comment': '✅ 已发布，页面编号 `%s`。/ Published as `%s`.\n\n%s' % (name, name, describe_post(info))})
            else:
                info = check_post(sub)
                sim = find_similar({'title': info['title'], 'author': info['author'], 'summary': info['excerpt']}, registry_pool(), 'item')
                if sim:
                    res.update(_similar_result(sim, describe_post(info)))
                else:
                    res.update({'ok': True, 'add': ['checked'], 'remove': ['invalid', 'duplicate', 'similar'], 'comment': describe_post(info) + '\n\n' + TAIL_CHECKED})
        elif mode == 'approve':
            e, r = approve(sub['url'], sub['tags'], number)                 # the maintainer's decision stands over a "possibly the same": only the same address is refused
            res.update({'ok': True, 'close': True, 'add': ['listed'], 'remove': ['checked', 'invalid', 'duplicate', 'similar'],
                        'comment': '✅ 已收录，编号 `%s`，每天自动更新。/ Listed as `%s`; it is refreshed every day.\n\n%s' % (e['id'], e['id'], describe(r))})
        else:
            r = probe(sub['url'])
            if r['duplicate']:
                zh, en = DUPLICATE_TEXT[r['duplicate'][0]]
                res.update({'ok': False, 'add': ['duplicate'], 'remove': ['checked', 'invalid', 'similar'],
                            'comment': describe(r) + '\n\nℹ️ **重复 / Duplicate：** %s。无需重复提交，可以关闭这个 issue。\n%s' % (md_esc(zh % r['duplicate'][1]), md_esc(en % r['duplicate'][1]))})
            elif r.get('similar'):
                res.update(_similar_result(r['similar'], describe(r)))
            else:
                res.update({'ok': True, 'add': ['checked'], 'remove': ['invalid', 'duplicate', 'similar'], 'comment': describe(r) + '\n\n' + TAIL_CHECKED})
    except Refused as ex:
        res.update({'ok': False, 'add': ['invalid'], 'remove': ['checked', 'duplicate', 'similar'],
                    'comment': '❌ **暂时无法收录 / Cannot be listed:** %s\n\n%s\n\n修改上面的内容后（编辑本 issue）会自动重新检查。Edit the issue and it is checked again.' % (md_esc(str(ex)), md_esc(ex.en))})
    return res


def _similar_result(sim, described):
    """-> what handle_issue answers when what was submitted looks like something the directory has: it is marked, not refused: the maintainer decides."""
    why, sid, stitle = sim
    zh, en = SIMILAR_TEXT[why]
    return {'ok': False, 'add': ['similar'], 'remove': ['checked', 'invalid', 'duplicate'],
            'comment': described + '\n\n⚠️ **疑似重复 / Possibly the same：** 和目录里的《%s》（%s）很像：%s。如果确实不同，维护者仍然可以批准。\n%s'
                       % (md_esc(stitle), md_esc(sid), md_esc(zh), md_esc('It looks like “%s” (%s) in the directory: %s. If it is a different one the maintainer can still approve it.' % (stitle, sid, en)))}


TAIL_CHECKED = ('✅ **自动检查通过，等待维护者审核。** 审核通过后维护者会给这个 issue 加上 `approved` 标签，之后会自动进入目录。\n\n'
                '✅ **Automatic checks passed; waiting for a maintainer.** Once approved (label `approved`) it is listed automatically.')


# ======================================================================= answering in the issue (GitHub REST, with the Action's token)
BOT_MARKER = '<!-- shelf-directory-bot -->'
LABEL_COLORS = {'shelf-submission': '1F7A6D', 'post-submission': '1F7A6D', 'checked': '0E8A16', 'invalid': 'B60205', 'duplicate': 'CFD3D7', 'approved': '5319E7',
                'rejected': '6A737D', 'listed': '0052CC', 'takedown': 'FBCA04', 'similar': 'FBCA04'}


def gh_call(method, path, body=None, token=None, accept=None, limit=None):
    """One GitHub REST call -> (status, decoded JSON or None). Never raises on an HTTP error: the caller decides what it means.
    `accept` (for example the raw media type, to get a file's own JSON) and `limit` (bytes) are for the few callers that read a big file."""
    token = token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        raise Refused('没有 GITHUB_TOKEN（只能在 GitHub Action 里回复 issue）', 'No GITHUB_TOKEN (replying to an issue works inside the GitHub Action only)')
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Authorization': 'Bearer ' + token, 'Accept': accept or 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': UA}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    handlers = [urllib.request.ProxyHandler({})] if ALLOW_LOCAL else []
    req = urllib.request.Request(GITHUB_API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=TIMEOUT) as r:
            raw = r.read(limit or MAX_BYTES)
            return r.status, (json.loads(raw.decode('utf-8')) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read(4000)
        e.close()
        try:
            return e.code, json.loads(raw.decode('utf-8'))
        except ValueError:
            return e.code, {'message': raw.decode('utf-8', 'replace')}
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise Refused('连不上 GitHub：%s' % e, 'Could not reach GitHub: %s' % e)


def apply_result(res, repo):
    """Post what handle_issue found: the comment (our earlier one is edited, not repeated), the labels, and closing the issue. -> list of problems."""
    n = res.get('number')
    if not n or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo or ''):
        return ['no issue number or repository (GITHUB_REPOSITORY) to answer in']
    base, problems = '/repos/%s/issues/%s' % (repo, n), []

    def check(what, st, data):
        if st not in (200, 201, 204):
            problems.append('%s: HTTP %s %s' % (what, st, (data or {}).get('message', '') if isinstance(data, dict) else ''))
        return st in (200, 201, 204)

    body = BOT_MARKER + '\n' + (res.get('comment') or '')
    st, comments = gh_call('GET', base + '/comments?per_page=100')
    mine = next((c for c in comments if isinstance(c, dict) and BOT_MARKER in (c.get('body') or '')), None) if st == 200 and isinstance(comments, list) else None
    if mine:
        check('edit comment', *gh_call('PATCH', '/repos/%s/issues/comments/%s' % (repo, mine['id']), {'body': body}))
    else:
        check('create comment', *gh_call('POST', base + '/comments', {'body': body}))
    for name in res.get('add', []):
        st, _ = gh_call('POST', '/repos/%s/labels' % repo, {'name': name, 'color': LABEL_COLORS.get(name, 'ededed')})
        if st not in (200, 201, 422):                              # 422: it exists already
            problems.append('create label %s: HTTP %s' % (name, st))
    if res.get('add'):
        check('add labels', *gh_call('POST', base + '/labels', {'labels': res['add']}))
    for name in res.get('remove', []):
        st, data = gh_call('DELETE', base + '/labels/' + urllib.parse.quote(name, safe=''))
        if st not in (200, 204, 404):                              # 404: it was not on the issue
            check('remove label ' + name, st, data)
    if res.get('redact'):                                          # the text of a refused post is replaced (GitHub may keep the edit history)
        check('clear the text', *gh_call('PATCH', base, {'body': '（投稿内容已清除 / the text has been removed）\n\n### 权利确认\n\n已拒绝 / rejected\n'}))
    if res.get('close'):
        check('close issue', *gh_call('PATCH', base, {'state': 'closed', 'state_reason': 'completed'}))
    return problems


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
    p = sub.add_parser('reject')
    p.add_argument('--event', required=True)
    p.add_argument('--out', required=True)
    p = sub.add_parser('apply')
    p.add_argument('--result', required=True)
    p = sub.add_parser('remove')
    p.add_argument('id')
    p = sub.add_parser('fetch')
    p.add_argument('--only')
    p = sub.add_parser('build')
    p.add_argument('--base', default='', help='public address of docs/ (else registry/config.json base_url, else guessed from GITHUB_REPOSITORY)')
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
        elif a.cmd == 'reject':
            res = handle_issue(read_json(a.event, {}), 'reject')
            write_json(a.out, res)
            print(res['comment'])
        elif a.cmd == 'apply':                                   # the last step of the workflows: answer in the issue, loudly if that fails
            if not os.path.isfile(a.result):
                print('apply: %s was not written: the step before this one failed, read its log' % a.result, file=sys.stderr)
                return 1
            problems = apply_result(read_json(a.result, {}), os.environ.get('GITHUB_REPOSITORY', ''))
            for line in problems:
                print('apply: ' + line, file=sys.stderr)
            return 1 if problems else 0
        elif a.cmd == 'remove':
            remove(a.id)
            build()
            print('removed', a.id)
        elif a.cmd == 'fetch':
            fetch_all(a.only)
        elif a.cmd == 'build':
            if a.base:
                os.environ['SHELF_BASE_URL'] = a.base
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
