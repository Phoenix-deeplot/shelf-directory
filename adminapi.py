"""adminapi.py: the admin page's back end, so that the maintainer signs in with a password and no GitHub token has to be kept in a browser.

Standard library only. api/admin.py is the thin Vercel wrapper; the tests run `handle` / `respond` against a fake GitHub.

The GitHub token (ADMIN_GITHUB_TOKEN: this repository only, Issues + Actions read/write, Contents read) lives in the function's environment and never
leaves it. The browser holds a *session*: a signed, expiring value that is worth nothing without this function, and that this function only
exchanges for a short list of things the admin page does (read the applications, label one approved or rejected, comment, start the remove / set /
daily workflow, read docs/directory.json and registry/shelves.json). Everything else is refused before GitHub is asked.

Environment: SHELF_REPO (owner/repo, shared with the submit function), ADMIN_PASSWORD (at least 12 characters), ADMIN_GITHUB_TOKEN, IP_SALT (shared).
Changing the password or the GitHub token signs every session out: the signing key is made from both.
"""
import hashlib
import hmac
import json
import re
import time
import urllib.parse

import aggregate as agg
import submission
from submission import Reply

MIN_PASSWORD = 12
SESSION_SECONDS = 8 * 3600                 # signed in until the tab is closed or this long, whichever comes first
REMEMBER_SECONDS = 14 * 86400              # "remember on this device"
BODY_LIMIT = 8000                          # bytes of request body: a comment is at most COMMENT_MAX characters
COMMENT_MAX = 600
FAIL_LIMIT = (6, 900)                      # wrong passwords per sender in 15 minutes, counted per warm instance
FAIL_DELAY = 1.0                           # seconds a wrong password is made to wait
GH_LIMIT = 8 * 1024 * 1024                 # bytes of one GitHub answer (docs/directory.json)
sleep = time.sleep                         # the tests replace it
_FAILS = {}
_BRANCH = {}


def settings(env):
    return {'repo': str(env.get('SHELF_REPO') or '').strip(), 'token': str(env.get('ADMIN_GITHUB_TOKEN') or '').strip(),
            'password': str(env.get('ADMIN_PASSWORD') or '').strip(), 'salt': str(env.get('IP_SALT') or '')}


def problem(cfg):
    """What is missing, as a short code ('' when all is set): the admin page says it, so that nobody has to guess which of three environment variables is wrong.
    Only the *name* of what is missing is told, never a value."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', cfg['repo']):
        return 'repo'
    if not cfg['password']:
        return 'password'
    if len(cfg['password']) < MIN_PASSWORD:
        return 'short'
    if not cfg['token']:
        return 'token'
    return ''


def configured(cfg):
    return not problem(cfg)


# ---------------------------------------------------------------- sessions
def _key(cfg):
    """From the password *and* the GitHub token: somebody holding a session cannot test password guesses against it offline."""
    return hashlib.sha256(('shelf-admin-session-v1\0%s\0%s\0%s' % (cfg['repo'], cfg['password'], cfg['token'])).encode('utf-8')).digest()


def make_session(cfg, now, remember):
    exp = int(now) + (REMEMBER_SECONDS if remember else SESSION_SECONDS)
    msg = 'v1.%d' % exp
    return msg + '.' + hmac.new(_key(cfg), msg.encode('ascii'), hashlib.sha256).hexdigest(), exp


def check_session(value, cfg, now):
    m = re.fullmatch(r'v1\.(\d{1,12})\.([0-9a-f]{64})', str(value or ''))
    if not m:
        return False
    good = hmac.new(_key(cfg), ('v1.' + m.group(1)).encode('ascii'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, m.group(2)) and int(m.group(1)) > now


def same_password(given, expected):
    return hmac.compare_digest(hashlib.sha256(given.encode('utf-8')).digest(), hashlib.sha256(expected.encode('utf-8')).digest())


def _recent(who, now):
    return [t for t in _FAILS.get(who, []) if now - t < FAIL_LIMIT[1]]


def login(payload, cfg, who, now):
    if len(_recent(who, now)) >= FAIL_LIMIT[0]:
        raise Reply(429, 'locked', '密码输错太多次了，请 15 分钟后再试', 'Too many wrong passwords; try again in 15 minutes')
    if not same_password(str(payload.get('password') or '').strip(), cfg['password']):
        _FAILS[who] = _recent(who, now) + [now]
        if len(_FAILS) > 2000:                                              # never grows without bound
            for k in [k for k in _FAILS if not _recent(k, now)]:
                _FAILS.pop(k, None)
        sleep(FAIL_DELAY)
        raise Reply(401, 'password', '密码不对', 'Wrong password')
    _FAILS.pop(who, None)
    session, exp = make_session(cfg, now, payload.get('remember') is True)
    return 200, {'ok': True, 'session': session, 'expires': exp}


# ---------------------------------------------------------------- the short list of what a session may ask GitHub for
LABELS = ('approved', 'rejected')
WORKFLOWS = {'remove': True, 'daily': False}                               # workflow -> needs a target
TARGET = re.compile(r'^(post:)?[A-Za-z0-9][A-Za-z0-9._-]{0,120}$')
BATCH_NUMBERS = re.compile(r'^\d{1,7}(,\d{1,7}){0,59}$')                      # the batch workflow: up to 60 issue numbers
SHELF_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,100}$')
SET_INPUTS = ('id', 'status', 'rights', 'topics', 'platform', 'note')           # the set workflow: all six, always (an empty topics / platform / note takes the value away)
SET_TOPICS = re.compile(r'^([a-z0-9][a-z0-9-]{0,19}(,[a-z0-9][a-z0-9-]{0,19}){0,29})?$')


def _forbidden():
    return Reply(400, 'forbidden', '这个操作管理页用不到，已拒绝', 'Not something the admin page does')


def _source_inputs(inputs):
    """The inputs of the set workflow, checked and tidied. The workflow's own command checks them once more (the topics against the topics this directory has)."""
    if not (isinstance(inputs, dict) and set(inputs) == set(SET_INPUTS) and all(isinstance(v, str) for v in inputs.values())):
        raise _forbidden()
    note = re.sub(r'\s+', ' ', inputs['note']).strip()
    if not (SHELF_ID.fullmatch(inputs['id']) and inputs['status'] in agg.STATUSES and inputs['rights'] in agg.RIGHTS and SET_TOPICS.fullmatch(inputs['topics'])
            and (inputs['platform'] == '' or agg.PLATFORM_ID.fullmatch(inputs['platform'])) and len(note) <= agg.NOTE_MAX and not re.search(r'[\x00-\x1f\x7f]', note)):
        raise _forbidden()
    return {**{k: inputs[k] for k in SET_INPUTS}, 'note': note}


def plan(method, path, body, cfg, branch):
    """-> (kind, GitHub method, GitHub path, GitHub body, accept). The GitHub request is *built* from the parts that matched, never forwarded as it came."""
    r = '/repos/' + cfg['repo']
    method, path = str(method or ''), str(path or '')
    e = re.escape(r)
    if method == 'GET':
        if path == r:
            return 'repo', 'GET', r, None, None
        m = re.fullmatch(e + r'/issues\?state=(open|closed)&labels=(shelf-submission|post-submission|listed|rejected)&per_page=(\d{1,3})', path)
        if m and 1 <= int(m.group(3)) <= 100:
            return 'plain', 'GET', '%s/issues?state=%s&labels=%s&per_page=%d' % (r, m.group(1), m.group(2), int(m.group(3))), None, None
        m = re.fullmatch(e + r'/issues/(\d{1,7})', path)
        if m:
            return 'plain', 'GET', '%s/issues/%d' % (r, int(m.group(1))), None, None
        m = re.fullmatch(e + r'/issues/(\d{1,7})/comments\?per_page=100', path)
        if m:
            return 'plain', 'GET', '%s/issues/%d/comments?per_page=100' % (r, int(m.group(1))), None, None
        if path in (r + '/contents/docs/directory.json', r + '/contents/registry/shelves.json'):      # the second: the sources with what is said about them, a paused one included
            return 'file', 'GET', path, None, 'application/vnd.github.raw+json'
    elif method == 'POST':
        m = re.fullmatch(e + r'/issues/(\d{1,7})/labels', path)
        if m:
            labels = body.get('labels') if isinstance(body, dict) else None
            if not (isinstance(labels, list) and len(labels) == 1 and labels[0] in LABELS):
                raise _forbidden()
            return 'plain', 'POST', '%s/issues/%d/labels' % (r, int(m.group(1))), {'labels': [labels[0]]}, None
        m = re.fullmatch(e + r'/issues/(\d{1,7})/comments', path)
        if m:
            text = body.get('body') if isinstance(body, dict) else None
            if not (isinstance(text, str) and 0 < len(text) <= COMMENT_MAX):
                raise _forbidden()
            return 'plain', 'POST', '%s/issues/%d/comments' % (r, int(m.group(1))), {'body': text}, None
        if re.fullmatch(e + r'/actions/workflows/batch\.yml/dispatches', path):          # decide several submissions in one run
            inputs = body.get('inputs') if isinstance(body, dict) else None
            if not (isinstance(inputs, dict) and {'mode', 'numbers'} <= set(inputs) <= {'mode', 'numbers', 'reason'}):
                raise _forbidden()
            mode, numbers, reason = inputs['mode'], inputs['numbers'], inputs.get('reason', '')
            if mode not in ('approve', 'reject') or not (isinstance(numbers, str) and BATCH_NUMBERS.match(numbers)) or not isinstance(reason, str) or len(reason) > COMMENT_MAX:
                raise _forbidden()
            clean = {'mode': mode, 'numbers': numbers}
            if mode == 'reject' and reason.strip():
                clean['reason'] = re.sub(r'\s+', ' ', reason).strip()
            return 'plain', 'POST', '%s/actions/workflows/batch.yml/dispatches' % r, {'ref': branch(), 'inputs': clean}, None
        if re.fullmatch(e + r'/actions/workflows/set\.yml/dispatches', path):             # say something about one source
            inputs = body.get('inputs') if isinstance(body, dict) else None
            return 'plain', 'POST', '%s/actions/workflows/set.yml/dispatches' % r, {'ref': branch(), 'inputs': _source_inputs(inputs)}, None
        m = re.fullmatch(e + r'/actions/workflows/(remove|daily)\.yml/dispatches', path)
        if m:
            inputs = body.get('inputs') if isinstance(body, dict) else None
            if WORKFLOWS[m.group(1)]:
                target = inputs.get('target') if isinstance(inputs, dict) and set(inputs) == {'target'} else None
                if not (isinstance(target, str) and TARGET.match(target)):
                    raise _forbidden()
                return 'plain', 'POST', '%s/actions/workflows/%s.yml/dispatches' % (r, m.group(1)), {'ref': branch(), 'inputs': {'target': target}}, None
            if inputs:
                raise _forbidden()
            return 'plain', 'POST', '%s/actions/workflows/%s.yml/dispatches' % (r, m.group(1)), {'ref': branch()}, None
    raise _forbidden()


def default_branch(cfg, now):
    hit = _BRANCH.get(cfg['repo'])
    if hit and now - hit[0] < 600:
        return hit[1]
    st, d = agg.gh_call('GET', '/repos/' + cfg['repo'], token=cfg['token'])
    name = d.get('default_branch') if st == 200 and isinstance(d, dict) else None
    name = name if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9._/-]{1,60}', name) else 'main'
    _BRANCH[cfg['repo']] = (now, name)
    return name


def proxy(payload, cfg, now):
    kind, method, path, body, accept = plan(payload.get('method'), payload.get('path'), payload.get('body'), cfg, lambda: default_branch(cfg, now))
    st, data = agg.gh_call(method, path, body, token=cfg['token'], accept=accept, limit=GH_LIMIT if kind == 'file' else None)
    if kind == 'repo' and st == 200 and isinstance(data, dict):             # only what the page needs
        data = {'default_branch': data.get('default_branch'), 'permissions': {'push': bool((data.get('permissions') or {}).get('push'))}}
    return 200, {'ok': True, 'status': st, 'data': data}


def handle(payload, bearer, ip, env, now=None):
    """-> (HTTP status, JSON-able dict). `bearer` is the session value from the Authorization header."""
    now = now if now is not None else time.time()
    cfg = settings(env)
    try:
        if not configured(cfg):
            raise Reply(503, 'not_configured', '管理密码还没有配置好（Vercel 环境变量 ADMIN_PASSWORD、ADMIN_GITHUB_TOKEN）', 'The admin password is not set up yet')
        if not isinstance(payload, dict):
            raise Reply(400, 'request', '请求格式不对', 'Bad request')
        action = payload.get('action')
        if action == 'login':
            return login(payload, cfg, submission.ip_hash(ip, cfg['salt']), now)
        if action == 'gh':
            if not check_session(bearer, cfg, now):
                raise Reply(401, 'session', '登录已过期，请重新输入密码', 'Signed out; enter the password again')
            return proxy(payload, cfg, now)
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    except Reply as r:
        return r.status, r.body()
    except agg.Refused as e:                                                 # GitHub could not be reached at all
        return 502, {'ok': False, 'error': 'github', 'message': '暂时连不上 GitHub，请稍后再试', 'message_en': str(e.en)}


# ---------------------------------------------------------------- the HTTP side (used by api/admin.py and by the tests)
def respond(h, env, method):
    """Answer one request on a BaseHTTPRequestHandler `h`. The page and this function are on the same site, so there is no CORS at all: another site's page is refused."""
    cfg = settings(env)

    def send(status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        h.send_response(status)
        h.send_header('Content-Type', 'application/json; charset=utf-8')
        h.send_header('Cache-Control', 'no-store')
        h.send_header('Content-Length', str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    if method == 'GET':                                                      # no secret in here: the admin page asks whether to show the password form
        why = problem(cfg)
        return send(200, {'ok': True, 'service': 'shelf-admin', 'configured': not why, **({'problem': why} if why else {})})
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        length = -1

    def drain():
        try:
            h.rfile.read(max(0, min(length, 2 * BODY_LIMIT)))
        except OSError:
            pass

    if method != 'POST':
        drain()
        return send(405, {'ok': False, 'error': 'method', 'message': '不支持', 'message_en': 'Not supported'})
    origin = h.headers.get('Origin') or ''
    host = (h.headers.get('X-Forwarded-Host') or h.headers.get('Host') or '').split(',')[0].strip().lower()
    if origin and urllib.parse.urlsplit(origin).netloc.lower() != host:     # a page of another site
        drain()
        return send(403, {'ok': False, 'error': 'origin', 'message': '这个来源不允许', 'message_en': 'This origin is not allowed'})
    if length < 0 or length > BODY_LIMIT:
        drain()
        return send(413, {'ok': False, 'error': 'size', 'message': '内容太大', 'message_en': 'Too large'})
    try:
        payload = json.loads(h.rfile.read(length).decode('utf-8')) if length else None
    except ValueError:
        payload = None
    auth = h.headers.get('Authorization') or ''
    bearer = auth[7:].strip() if auth[:7].lower() == 'bearer ' else ''
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    status, body = handle(payload, bearer, ip, env)
    return send(status, body)
