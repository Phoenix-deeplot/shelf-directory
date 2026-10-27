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
REGISTRY_SECONDS = 20         # the registry in the repository changes when something is approved: short, so that the next visitor already sees it
_CACHE = {}

RIGHTS_URL = '我有权发布这些内容 / I have the right to publish this content'
RIGHTS_POST = '我是作者并同意公开发布 / I am the author and agree to publish'
RIGHTS_RECORD = '我有权公开这些信息 / I have the right to publish this information'


class Reply(Exception):
    def __init__(self, status, code, zh, en=''):
        super().__init__(zh)
        self.status, self.code, self.zh, self.en = status, code, zh, en or zh

    def body(self):
        return {'ok': False, 'error': self.code, 'message': self.zh, 'message_en': self.en}


def _number(raw, default, top):
    """An optional whole number from the environment (SUBMIT_PER_DAY, SUBMIT_MAX_OPEN): the default when it is missing or not a number, never above `top`."""
    try:
        n = int(str(raw).strip())
    except ValueError:
        return default
    return max(1, min(top, n))


def settings(env):
    repo = str(env.get('SHELF_REPO') or '').strip()
    directory = str(env.get('DIRECTORY_URL') or '').strip()
    if not directory and '/' in repo:
        owner, name = repo.split('/', 1)
        directory = 'https://%s.github.io/%s/' % (owner.lower(), name) if name.lower() != owner.lower() + '.github.io' else 'https://%s.github.io/' % owner.lower()
    origins = [o.strip().rstrip('/') for o in str(env.get('ALLOWED_ORIGINS') or 'https://books.zhitree.cn').split(',') if o.strip()]
    return {'repo': repo, 'token': str(env.get('SUBMIT_GITHUB_TOKEN') or '').strip(), 'directory': directory.rstrip('/') + '/' if directory else '',
            'origins': origins, 'salt': str(env.get('IP_SALT') or ''), 'allow_local': str(env.get('ALLOW_LOCAL_ORIGIN') or '') == '1',
            'per_day': _number(env.get('SUBMIT_PER_DAY'), PER_IP_PER_DAY, 500), 'max_open': _number(env.get('SUBMIT_MAX_OPEN'), MAX_OPEN, 1000)}


def _cached(key, fn, seconds=CACHE_SECONDS):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < seconds:
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


def load_registry_live(cfg):
    """registry/shelves.json as it is in the repository right now (read with the token): an approval counts at once, without waiting for the published
    directory to be rebuilt and for its caches to expire. [] when the file does not exist yet, None when it cannot be read this way (the token has no read
    access to files, or the file is too big): the published directory is then all there is. Raises Reply(502) when GitHub cannot be asked at all:
    better no answer than a wrong "not listed"."""
    def read():
        st, d = agg.gh_call('GET', '/repos/%s/contents/registry/shelves.json' % cfg['repo'], token=cfg['token'])
        if st == 404:
            return []
        if st in (401, 403):                                                # this token may not read the repository's files: the published directory has to do
            return None
        if st != 200 or not isinstance(d, dict):
            raise Reply(502, 'github', '暂时连不上审核系统，请稍后再试', 'The review system cannot be reached; try again later')
        try:
            shelves = json.loads(base64.b64decode(d.get('content') or '').decode('utf-8')).get('shelves') if d.get('content') else None
        except (ValueError, TypeError, AttributeError):
            shelves = None
        return [e for e in shelves if isinstance(e, dict)] if isinstance(shelves, list) else None
    return _cached('reg:' + cfg['repo'], read, REGISTRY_SECONDS)


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
    return re.sub(r'(?im)^(###)(\s+(?:标题|作者|标签|摘要|正文|权利|网址|title|author|tags?|summary|body|content|rights?|url|address|link))', '\\1\u200b\\2', str(text))


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


def record_issue(sub, who):
    return ('### 标题\n\n%s\n\n### 作者（可选）\n\n%s\n\n### 标签（可选）\n\n%s\n\n### 摘要\n\n%s\n\n### 网址（可选）\n\n%s\n\n### 权利确认\n\n%s\n\n<!-- via:web ip:%s -->\n'
            % (neutralize(sub['title']), neutralize(sub['author']) or '_No response_', neutralize(', '.join(sub['tags'])) or '_No response_', neutralize(sub['summary']),
               sub['url'] or '_No response_', RIGHTS_RECORD, who))


def directory_pool(directory, issues=()):
    """What the directory has, as entries to compare a new one with: from the published directory.json, and from the submissions that are waiting
    (a waiting one has the id '#<number>')."""
    pool = [{'level': 'shelf', 'id': str(s.get('id')), 'title': s.get('name') or '', 'author': '', 'summary': s.get('description') or ''}
            for s in directory.get('shelves') or [] if isinstance(s, dict)]
    pool += [{'level': 'item', 'id': str(i.get('shelf') or ''), 'title': i.get('title') or '', 'author': i.get('author') or '', 'summary': i.get('summary') or ''}
             for i in directory.get('items') or [] if isinstance(i, dict)]
    for issue in issues:
        sub = agg.parse_issue({'issue': issue})
        if sub['kind'] in ('post', 'record'):
            text = sub['summary'] if sub['kind'] == 'record' else agg.clean(agg.mdsafe.plain_text(sub['body']), agg.SUMMARY)
            pool.append({'level': 'item', 'id': '#%s' % issue.get('number'), 'title': sub['title'], 'author': sub['author'], 'summary': text})
    return pool


def refuse_similar(cand, pool, level, what):
    """409 when the directory (or the waiting submissions) already has what `cand` looks like the same as."""
    found = agg.find_similar(cand, pool, level)
    if not found:
        return
    why, sid, stitle = found
    zh, en = agg.SIMILAR_TEXT[why]
    if sid.startswith('#'):
        raise Reply(409, 'pending', '有一个正在审核的申请和这个很像（%s）' % zh, 'A submission that is waiting for review looks like this one (%s)' % en)
    raise Reply(409, 'similar', '目录里已经有很像的了：《%s》（%s）' % (stitle, zh), 'The directory has something very like it: “%s” (%s)' % (stitle, en))


def listed_in(directory, entry_url, classified, registry=None):
    """Is this address in the directory already (the registry in the repository, which is the fresh truth, and the published directory.json)? -> shelf id or ''."""
    key = agg.url_key(entry_url)
    for e in registry or []:
        if key in {agg.url_key(e.get('url') or ''), agg.url_key(e.get('source') or '')} - {agg.url_key('')}:
            return str(e.get('id'))
        if classified.get('kind') == 'repo' and e.get('kind') == 'repo' and isinstance(e.get('repo'), dict):
            have_spec, want = agg._repo_key(e['repo']), agg._repo_key(classified)
            if have_spec[:2] == want[:2] and (not have_spec[3] or want[3] == have_spec[3] or want[3].startswith(have_spec[3] + '/')):
                return str(e.get('id'))
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
    raw = str(payload.get('url') or '').strip()
    if '#read/' in raw:                                                     # the address bar of someone's reader: the part after "#" never reaches a server, so it would only be read as the site's home page
        raise Reply(400, 'reader', '这是阅读器里的地址，别人打不开，不是公开网址。请用书架卡片上的地球图标申请，或贴这个内容真正的网址',
                    'This is a reader address, not a public one. Use the globe on the card of the item, or give the real address of the content')
    try:
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
    if listed_in(load_directory(cfg), url, c, load_registry_live(cfg)):
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
    refuse_similar({'title': info['title'], 'author': info['author'], 'summary': info['excerpt']}, directory_pool(load_directory(cfg), issues), 'item', 'post')
    return sub, info, issues


def submit_record(payload, cfg):
    """A catalogue card (title, author, summary; an address if there is one). -> (sub, info, open issues)."""
    sub = {'title': agg.clean(payload.get('title')), 'author': agg.clean(payload.get('author')), 'summary': agg.clean(payload.get('summary')), 'url': agg.clean(payload.get('url')),
           'tags': agg.norm_tags(payload.get('tags') if isinstance(payload.get('tags'), list) else str(payload.get('tags') or '').replace('，', ',').split(','), 5)}
    if '#read/' in sub['url']:
        raise Reply(400, 'reader', '这是阅读器里的地址，不是公开网址：这一栏可以留空', 'This is a reader address, not a public one: leave the address empty')
    try:
        info = agg.check_record(sub)                                          # sizes, blocked words, a safe address, and the rules the Action applies
    except agg.Refused as e:
        raise Reply(400, 'record', str(e), e.en)
    if agg.key_hash(info['key']) in load_rejected(cfg):
        raise Reply(409, 'rejected', '这张资料卡暂时不能收录', 'This card cannot be accepted')
    directory, issues = load_directory(cfg), open_submissions(cfg)
    refuse_similar({'title': info['title'], 'author': info['author'], 'summary': info['summary']}, directory_pool(directory, issues), 'item', 'record')
    if info['url']:                                                           # with an address of its own it is also a question of that address
        key = agg.url_key(info['url'])
        if listed_in(directory, info['url'], agg.classify(info['url']), load_registry_live(cfg)):
            raise Reply(409, 'listed', '这个网址已经在目录里了', 'This address is in the directory already')
        for i in issues:
            other = agg.parse_issue({'issue': i})
            if other['url'] and agg.url_key(other['url']) == key:
                raise Reply(409, 'pending', '这个网址已经提交过了，正在审核', 'This address is waiting for review already')
    return sub, info, issues


CHECK_SECONDS = 20          # leave time for GitHub and the response
CHECK_LIMIT = (20, 600)           # checks (they fetch) per sender in 10 minutes, counted per warm instance: a soft limit, the real ones are Vercel's
_CHECKS = {}


def soft_limit(who, now):
    recent = [t for t in _CHECKS.get(who, []) if now - t < CHECK_LIMIT[1]]
    if len(recent) >= CHECK_LIMIT[0]:
        raise Reply(429, 'rate', '检查得太频繁了，请稍等几分钟', 'Too many checks; wait a few minutes')
    recent.append(now)
    _CHECKS[who] = recent
    if len(_CHECKS) > 2000:                                                  # never grows without bound
        for k in [k for k, v in _CHECKS.items() if not v or now - v[-1] > CHECK_LIMIT[1]]:
            _CHECKS.pop(k, None)


def probe_for_check(url):
    """Read the address with the same code the Action uses (https only, public addresses only, pinned connection, size and time limits), a little tighter."""
    old = (agg.TIMEOUT, agg.REPO_READ, agg.DEADLINE)
    agg.TIMEOUT, agg.REPO_READ = 7, 6                                       # a function has seconds, not minutes
    agg.DEADLINE = time.monotonic() + CHECK_SECONDS                         # and all of the reading together must end before the platform cuts the function off
    try:
        return agg.probe(url)
    except agg.Refused as e:
        raise Reply(422, 'unreachable', str(e), e.en)
    finally:
        agg.TIMEOUT, agg.REPO_READ, agg.DEADLINE = old


def summarize_probe(r):
    e, s = r['entry'], r['shelf']
    zh, en = agg.KIND_NAMES.get((e['kind'], e.get('sub', '')), (e['kind'], e['kind']))
    tags = agg.norm_tags(sorted({t for i in r['items'] for t in i['tags']}, key=lambda t: -sum(t in i['tags'] for i in r['items'])), 6)
    return {'what': 'url', 'kind': e['kind'], 'kind_zh': zh, 'kind_en': en, 'name': s.get('name', ''), 'source': e.get('source') or e.get('url', ''), 'found': len(r['items']), 'lang': s.get('lang', ''),
            'description': s.get('description', ''), 'license': s.get('license', ''), 'tags': tags,
            'latest': [{'title': i['title'], 'url': i['url'], 'date': i['date'], 'type': i['type']} for i in sorted(r['items'], key=lambda i: i['date'], reverse=True)[:5]],
            'warnings': [list(w) for w in r.get('warnings', [])], 'unread': bool(r.get('unread'))}


def summarize_record(info):
    return {'what': 'record', 'title': info['title'], 'author': info['author'], 'tags': info['tags'], 'summary': info['summary'], 'url': info['url'], 'lang': info['lang'], 'warnings': []}


def summarize_post(info):
    return {'what': 'post', 'title': info['title'], 'author': info['author'], 'tags': info['tags'], 'chars': info['chars'], 'links': info['links'], 'domains': info['domains'],
            'excerpt': info['excerpt'], 'lang': info['lang'], 'warnings': [list(w) for w in info['warnings']]}


def handle(payload, ip, env, now=None):
    """-> (HTTP status, JSON-able dict)."""
    now = now if now is not None else time.time()
    cfg = settings(env)
    try:
        if not isinstance(payload, dict):
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        if str(payload.get('website') or '').strip():                       # the hidden field of the form: only a program fills it. It is told it worked.
            return 200, {'ok': True, 'status': 'queued'}
        who = ip_hash(ip, cfg['salt'])
        if payload.get('kind') == 'check':                                  # "what will become of this?": every cheap check, then the address is read; nothing is created
            if not cfg['repo'] or not cfg['token']:
                raise Reply(503, 'not_configured', '提交功能还没有配置好', 'Submissions are not set up yet')
            soft_limit(who, now)
            if payload.get('what') == 'url':
                url, c, tags, issues = submit_url(payload, cfg, who, now)
                r = probe_for_check(url)
                if r.get('duplicate'):                                      # the copy of the registry that came with this deployment knows it too
                    zh, en = agg.DUPLICATE_TEXT[r['duplicate'][0]]
                    raise Reply(409, 'listed', zh % r['duplicate'][1], en % r['duplicate'][1])
                if not r.get('unread'):                                     # what was read is compared with what the directory has (an address that could not be read has only its guessed title)
                    cand, level = agg.candidate_of(r)
                    refuse_similar(cand, directory_pool(load_directory(cfg), issues), level, 'url')
                return 200, {'ok': True, 'status': 'checked', 'result': summarize_probe(r)}
            if payload.get('what') == 'post':
                sub, info, issues = submit_post(payload, cfg)
                return 200, {'ok': True, 'status': 'checked', 'result': summarize_post(info)}
            if payload.get('what') == 'record':
                sub, info, issues = submit_record(payload, cfg)
                return 200, {'ok': True, 'status': 'checked', 'result': summarize_record(info)}
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        check_common(payload, cfg)
        kind = payload.get('kind')
        if kind == 'url':
            url, c, tags, issues = submit_url(payload, cfg, who, now)
        elif kind == 'post':
            sub, info, issues = submit_post(payload, cfg)
        elif kind == 'record':
            sub, info, issues = submit_record(payload, cfg)
        else:
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        if len(issues) >= cfg['max_open']:
            raise Reply(429, 'full', '待审核的申请太多了，请过几天再来', 'Too many submissions are waiting; please come back in a few days')
        if sum(1 for i in issues if ('ip:' + who) in (i.get('body') or '') and _created_within(i, 86400, now)) >= cfg['per_day']:
            raise Reply(429, 'rate', '今天提交得太多了，请明天再试', 'Too many submissions today; please try again tomorrow')
        if kind == 'url':
            title, body, labels = ('[收录] ' + urllib.parse.urlsplit(url).hostname + urllib.parse.urlsplit(url).path)[:80], url_issue(url, tags, who), ['shelf-submission']
        elif kind == 'record':
            title, body, labels = ('[资料卡] ' + sub['title'])[:80], record_issue(info, who), ['post-submission']
        else:
            title, body, labels = ('[投稿] ' + sub['title'])[:80], post_issue(sub, who), ['post-submission']
        if len(body) > ISSUE_MAX:
            raise Reply(400, 'post', '内容太长', 'Too long')
        st, data = agg.gh_call('POST', '/repos/%s/issues' % cfg['repo'], {'title': title, 'body': body, 'labels': labels}, token=cfg['token'])
        if st not in (200, 201):
            raise Reply(502, 'github', '暂时连不上审核系统，请稍后再试', 'The review system cannot be reached; try again later')
        return 200, {'ok': True, 'status': 'queued', 'kind': c['kind'] if kind == 'url' else kind}
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
    if method == 'GET':                                                      # no secret in here: the admin page reads the repository and the data address from it
        return send(200, {'ok': True, 'service': 'shelf-submit', 'configured': bool(cfg['repo'] and cfg['token']), 'repo': cfg['repo'], 'directory': cfg['directory']})
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        length = -1

    def drain():                                                              # read (a bounded amount of) what was sent, so that the sender gets the answer, not a reset
        try:
            h.rfile.read(max(0, min(length, 2 * BODY_LIMIT)))
        except OSError:
            pass

    if origin and not cors:                                                  # a browser page of another site: refused (programs send no Origin and meet the rate limit)
        drain()
        return send(403, {'ok': False, 'error': 'origin', 'message': '这个来源不允许提交', 'message_en': 'This origin is not allowed'})
    if length < 0 or length > BODY_LIMIT:
        drain()
        return send(413, {'ok': False, 'error': 'size', 'message': '内容太大', 'message_en': 'Too large'})
    try:
        payload = json.loads(h.rfile.read(length).decode('utf-8')) if length else None
    except ValueError:
        payload = None
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    status, body = handle(payload, ip, env)
    return send(status, body)
