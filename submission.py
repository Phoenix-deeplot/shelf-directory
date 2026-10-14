"""submission.py: what the public submit function does, so that nobody needs a GitHub account to propose an address or a post.

Standard library only. api/submit.py is the thin Vercel wrapper; the tests run `respond` on a local server with a fake GitHub.

A submission becomes an issue in the directory repository, written by a token that can only write issues. From there the existing
pipeline takes over (the Action checks it and comments, the maintainer approves or rejects it in the admin page). This function therefore
never fetches the address it is given (nothing an outsider names is ever requested from here) and keeps no database: the queue is the list
of open issues, and rate limiting counts a salted hash of the sender's address that is written into each issue.

Environment: SHELF_REPO (owner/repo), SUBMIT_GITHUB_TOKEN (issues: write on that repository only), ALLOWED_ORIGINS (comma separated, default
https://books.zhitree.cn), IP_SALT (any long random text), DIRECTORY_URL (where directory.json is published; default derived from SHELF_REPO).
"""
import base64
import calendar
import hashlib
import json
import re
import time
import urllib.parse

import aggregate as agg

MAX_OPEN = 100                # open submissions waiting for a decision: more are refused until some are decided
PER_IP_PER_DAY = 5            # submissions per sender in 24 hours (counted from the open ones)
BODY_LIMIT = 120_000          # bytes of request body
ISSUE_MAX = 60_000            # characters of an issue (GitHub allows 65536)
CACHE_SECONDS = 300
_CACHE = {}

RIGHTS_URL = '我有权发布这些内容 / I have the right to publish this content'
RIGHTS_POST = '我是作者并同意公开发布 / I am the author and agree to publish'


class Reply(Exception):
    def __init__(self, status, code, zh, en=''):
        super().__init__(zh)
        self.status, self.code, self.zh, self.en = status, code, zh, en or zh

    def body(self):
        return {'ok': False, 'error': self.code, 'message': self.zh, 'message_en': self.en}


def settings(env):
    repo = str(env.get('SHELF_REPO') or '').strip()
    directory = str(env.get('DIRECTORY_URL') or '').strip()
    if not directory and '/' in repo:
        owner, name = repo.split('/', 1)
        directory = 'https://%s.github.io/%s/' % (owner.lower(), name) if name.lower() != owner.lower() + '.github.io' else 'https://%s.github.io/' % owner.lower()
    origins = [o.strip().rstrip('/') for o in str(env.get('ALLOWED_ORIGINS') or 'https://books.zhitree.cn').split(',') if o.strip()]
    return {'repo': repo, 'token': str(env.get('SUBMIT_GITHUB_TOKEN') or '').strip(), 'directory': directory.rstrip('/') + '/' if directory else '',
            'origins': origins, 'salt': str(env.get('IP_SALT') or ''), 'allow_local': str(env.get('ALLOW_LOCAL_ORIGIN') or '') == '1'}


def _cached(key, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    _CACHE[key] = (time.time(), value)
    return value


def load_directory(cfg):
    """The published directory.json, or {} when it cannot be read (the duplicate check is then weaker, the submission still works)."""
    def read():
        try:
            _, h, body = agg.fetch(cfg['directory'] + 'directory.json', 'application/json', 8 * 1024 * 1024)
            d = json.loads(agg.decode(body, h))
            return d if isinstance(d, dict) else {}
        except (agg.Refused, ValueError):
            return {}
    return _cached('dir:' + cfg['directory'], read) if cfg['directory'] else {}


def load_rejected(cfg):
    """The hashes in registry/rejected.json (read with the token, the repository may be private)."""
    def read():
        st, d = agg.gh_call('GET', '/repos/%s/contents/registry/rejected.json' % cfg['repo'], token=cfg['token'])
        try:
            if st == 200 and isinstance(d, dict) and d.get('content'):
                return set(json.loads(base64.b64decode(d['content']).decode('utf-8')).get('keys', []))
        except (ValueError, TypeError):
            pass
        return set()
    return _cached('rej:' + cfg['repo'], read)


def open_submissions(cfg):
    """Open issues of both kinds. -> list of issue dicts. Raises Reply(502) when GitHub cannot be asked."""
    found = []
    for label in ('shelf-submission', 'post-submission'):
        st, data = agg.gh_call('GET', '/repos/%s/issues?state=open&labels=%s&per_page=100' % (cfg['repo'], label), token=cfg['token'])
        if st != 200 or not isinstance(data, list):
            raise Reply(502, 'github', '暂时连不上审核系统，请稍后再试', 'The review system cannot be reached; try again later')
        found += [i for i in data if isinstance(i, dict) and 'pull_request' not in i]
    return found


def ip_hash(ip, salt):
    return hashlib.sha256((salt + '|' + str(ip)).encode('utf-8')).hexdigest()[:12]


def neutralize(text):
    """A line of the visitor's text that looks like a heading of the issue form would cut the form in the wrong place."""
    return re.sub(r'(?im)^(###)(\s+(?:标题|作者|标签|正文|权利|网址|title|author|tags?|body|content|rights?|url|address|link))', '\\1\u200b\\2', str(text))


def _created_within(issue, seconds, now):
    try:
        t = calendar.timegm(time.strptime(issue.get('created_at', ''), '%Y-%m-%dT%H:%M:%SZ'))
    except ValueError:
        return False
    return now - t < seconds


def url_issue(url, tags, who):
    return '### 网址\n\n%s\n\n### 标签（可选）\n\n%s\n\n### 权利确认\n\n%s\n\n<!-- via:web ip:%s -->\n' % (url, ', '.join(tags) or '_No response_', RIGHTS_URL, who)


def post_issue(sub, who):
    return ('### 标题\n\n%s\n\n### 作者（可选）\n\n%s\n\n### 标签（可选）\n\n%s\n\n### 正文\n\n%s\n\n### 权利确认\n\n%s\n\n<!-- via:web ip:%s -->\n'
            % (neutralize(sub['title']), neutralize(sub['author']) or '_No response_', neutralize(', '.join(sub['tags'])) or '_No response_', neutralize(sub['body']), RIGHTS_POST, who))


def listed_in(directory, entry_url, classified):
    """Is this address in the published directory already? -> shelf id or ''."""
    key = agg.url_key(entry_url)
    for s in directory.get('shelves') or []:
        if isinstance(s, dict) and key in {agg.url_key(s.get('home') or ''), agg.url_key(s.get('feed') or '')} - {agg.url_key('')}:
            return str(s.get('id'))
        if classified.get('kind') == 'repo' and s.get('kind') == 'repo':
            c = agg.classify(s.get('home') or '')
            if c.get('kind') == 'repo' and agg._repo_key(c)[:2] == agg._repo_key(classified)[:2]:
                have, want = agg._repo_key(c)[3], agg._repo_key(classified)[3]
                if not have or want == have or want.startswith(have + '/'):
                    return str(s.get('id'))
    for i in directory.get('items') or []:
        if isinstance(i, dict) and agg.url_key(i.get('url') or '') == key:
            return str(i.get('shelf') or '')
    return ''


def check_common(payload, cfg):
    if not cfg['repo'] or not cfg['token']:
        raise Reply(503, 'not_configured', '提交功能还没有配置好', 'Submissions are not set up yet')
    if payload.get('rights') is not True:
        raise Reply(400, 'rights', '请勾选“我有权发布这些内容”', 'Please confirm that you have the right to publish this')


def submit_url(payload, cfg, who, now):
    try:
        raw = str(payload.get('url') or '').strip()
        url = agg.normalize_input('https://' + raw[7:] if raw.lower().startswith('http://') else raw)      # always the secure address of the same page
        agg.check_url_syntax(url)
    except agg.Refused as e:
        raise Reply(400, 'url', str(e), e.en)
    c = agg.classify(url)
    if c['kind'] == 'unsupported':
        raise Reply(400, 'url', c['zh'], c['en'])
    key = agg.url_key(url)
    if agg.key_hash(key) in load_rejected(cfg):
        raise Reply(409, 'rejected', '这个网址暂时不能收录', 'This address cannot be accepted')
    if listed_in(load_directory(cfg), url, c):
        raise Reply(409, 'listed', '这个网址已经在目录里了', 'This address is in the directory already')
    tags = agg.norm_tags(payload.get('tags') if isinstance(payload.get('tags'), list) else str(payload.get('tags') or '').replace('，', ',').split(','), 5)
    issues = open_submissions(cfg)
    for i in issues:
        sub = agg.parse_issue({'issue': i})
        if sub['kind'] == 'shelf' and sub['url'] and agg.url_key(sub['url']) == key:
            raise Reply(409, 'pending', '这个网址已经提交过了，正在审核', 'This address is waiting for review already')
    return url, c, tags, issues


def submit_post(payload, cfg):
    sub = {'title': agg.clean(payload.get('title')), 'author': agg.clean(payload.get('author')), 'body': str(payload.get('body') or '').replace('\r\n', '\n').replace('\r', '\n').strip(),
           'tags': agg.norm_tags(payload.get('tags') if isinstance(payload.get('tags'), list) else str(payload.get('tags') or '').replace('，', ',').split(','), 5)}
    try:                                                                  # sizes, blocked words and the content hash: the same rules the Action applies
        info = agg.check_post(sub)
    except agg.Refused as e:
        raise Reply(400, 'post', str(e), e.en)
    h = info['hash']
    if agg.key_hash(h) in load_rejected(cfg):
        raise Reply(409, 'rejected', '这篇文章暂时不能收录', 'This text cannot be accepted')
    if any(isinstance(i, dict) and i.get('hash') == h for i in load_directory(cfg).get('items') or []):
        raise Reply(409, 'listed', '这篇文章已经发布过了', 'This text is published already')
    issues = open_submissions(cfg)
    for i in issues:
        other = agg.parse_issue({'issue': i})
        if other['kind'] == 'post' and other['body'] and agg.post_hash(other['body']) == h:
            raise Reply(409, 'pending', '这篇文章已经提交过了，正在审核', 'This text is waiting for review already')
    return sub, info, issues


def handle(payload, ip, env, now=None):
    """-> (HTTP status, JSON-able dict)."""
    now = now if now is not None else time.time()
    cfg = settings(env)
    try:
        if not isinstance(payload, dict):
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        if str(payload.get('website') or '').strip():                       # the hidden field of the form: only a program fills it. It is told it worked.
            return 200, {'ok': True, 'status': 'queued'}
        check_common(payload, cfg)
        who = ip_hash(ip, cfg['salt'])
        kind = payload.get('kind')
        if kind == 'url':
            url, c, tags, issues = submit_url(payload, cfg, who, now)
        elif kind == 'post':
            sub, info, issues = submit_post(payload, cfg)
        else:
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        if len(issues) >= MAX_OPEN:
            raise Reply(429, 'full', '待审核的申请太多了，请过几天再来', 'Too many submissions are waiting; please come back in a few days')
        if sum(1 for i in issues if ('ip:' + who) in (i.get('body') or '') and _created_within(i, 86400, now)) >= PER_IP_PER_DAY:
            raise Reply(429, 'rate', '今天提交得太多了，请明天再试', 'Too many submissions today; please try again tomorrow')
        if kind == 'url':
            title, body, labels = ('[收录] ' + urllib.parse.urlsplit(url).hostname + urllib.parse.urlsplit(url).path)[:80], url_issue(url, tags, who), ['shelf-submission']
        else:
            title, body, labels = ('[投稿] ' + sub['title'])[:80], post_issue(sub, who), ['post-submission']
        if len(body) > ISSUE_MAX:
            raise Reply(400, 'post', '内容太长', 'Too long')
        st, data = agg.gh_call('POST', '/repos/%s/issues' % cfg['repo'], {'title': title, 'body': body, 'labels': labels}, token=cfg['token'])
        if st not in (200, 201):
            raise Reply(502, 'github', '暂时连不上审核系统，请稍后再试', 'The review system cannot be reached; try again later')
        return 200, {'ok': True, 'status': 'queued', 'kind': c['kind'] if kind == 'url' else 'post'}
    except Reply as r:
        return r.status, r.body()
    except agg.Refused as e:                                                 # GitHub could not be reached at all
        return 502, {'ok': False, 'error': 'github', 'message': '暂时连不上审核系统，请稍后再试', 'message_en': str(e.en)}


# ---------------------------------------------------------------- the HTTP side (used by api/submit.py and by the tests)
def cors_headers(origin, cfg):
    ok = origin in cfg['origins'] or (cfg['allow_local'] and re.match(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?$', origin or ''))
    return {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'} if ok else {}


def respond(h, env, method):
    """Answer one request on a BaseHTTPRequestHandler `h`."""
    cfg = settings(env)
    origin = h.headers.get('Origin') or ''
    cors = cors_headers(origin, cfg)

    def send(status, payload, extra=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else b''
        h.send_response(status)
        h.send_header('Content-Type', 'application/json; charset=utf-8')
        h.send_header('Cache-Control', 'no-store')
        for k, v in {**cors, **(extra or {})}.items():
            h.send_header(k, v)
        h.send_header('Content-Length', str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    if method == 'OPTIONS':
        return send(204, None, {'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400'} if cors else {})
    if method == 'GET':
        return send(200, {'ok': True, 'service': 'shelf-submit', 'configured': bool(cfg['repo'] and cfg['token'])})
    if origin and not cors:                                                  # a browser page of another site: refused (programs send no Origin and meet the rate limit)
        return send(403, {'ok': False, 'error': 'origin', 'message': '这个来源不允许提交', 'message_en': 'This origin is not allowed'})
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        length = -1
    if length < 0 or length > BODY_LIMIT:
        return send(413, {'ok': False, 'error': 'size', 'message': '内容太大', 'message_en': 'Too large'})
    try:
        payload = json.loads(h.rfile.read(length).decode('utf-8')) if length else None
    except ValueError:
        payload = None
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    status, body = handle(payload, ip, env)
    return send(status, body)
