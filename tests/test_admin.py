"""The admin back end: password sign-in, sessions, and the short list of things a session may ask GitHub for. A fake GitHub; no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import http.client
import json
import os
import socketserver
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import adminapi as adm  # noqa: E402
from test_aggregate import Base  # noqa: E402

PASSWORD = 'correct horse battery'
GHTOKEN = 'ghp_admin_secret_value'


class AdminBase(Base):
    def setUp(self):
        super().setUp()
        adm._FAILS.clear()
        adm._BRANCH.clear()
        self.slept = []
        self.old_sleep = adm.sleep
        adm.sleep = self.slept.append
        self.env = {'SHELF_REPO': 'o/r', 'ADMIN_PASSWORD': PASSWORD, 'ADMIN_GITHUB_TOKEN': GHTOKEN, 'IP_SALT': 'salt'}
        self.site.put('/api/repos/o/r', json.dumps({'default_branch': 'trunk', 'permissions': {'push': True, 'admin': True}, 'private': False, 'owner': {'login': 'o'}}), 'application/json')

    def tearDown(self):
        adm.sleep = self.old_sleep
        super().tearDown()

    def login(self, password=PASSWORD, ip='1.2.3.4', remember=False, now=1_000_000):
        return adm.handle({'action': 'login', 'password': password, 'remember': remember}, '', ip, self.env, now=now)

    def session(self, now=1_000_000, remember=False):
        st, body = self.login(now=now, remember=remember)
        self.assertEqual(st, 200)
        return body['session']

    def gh(self, session, method, path, body=None, now=1_000_100):
        return adm.handle({'action': 'gh', 'method': method, 'path': path, 'body': body}, session, '1.2.3.4', self.env, now=now)

    def untouched(self):
        self.assertEqual((self.site.hits, self.site.calls), ([], []))


class TestLogin(AdminBase):
    def test_the_right_password_gives_a_session_that_expires(self):
        st, body = self.login(now=1_000_000)
        self.assertEqual((st, body['ok']), (200, True))
        self.assertEqual(body['expires'], 1_000_000 + adm.SESSION_SECONDS)
        cfg = adm.settings(self.env)
        self.assertTrue(adm.check_session(body['session'], cfg, 1_000_001))
        self.assertTrue(adm.check_session(body['session'], cfg, 1_000_000 + adm.SESSION_SECONDS - 1))
        self.assertFalse(adm.check_session(body['session'], cfg, 1_000_000 + adm.SESSION_SECONDS))

    def test_remember_lasts_longer(self):
        _, body = self.login(remember=True)
        self.assertEqual(body['expires'], 1_000_000 + adm.REMEMBER_SECONDS)
        _, body = self.login(remember='yes')                                  # only a real true counts
        self.assertEqual(body['expires'], 1_000_000 + adm.SESSION_SECONDS)

    def test_a_wrong_password_waits_and_says_nothing_else(self):
        for wrong in ('', 'x', PASSWORD + 'x', PASSWORD.upper(), None):
            st, body = self.login(wrong)
            self.assertEqual((st, body['error']), (401, 'password'))
        self.assertEqual(len(self.slept), 5)
        self.assertTrue(all(s >= 1 for s in self.slept))

    def test_spaces_around_the_password_do_not_matter(self):
        self.assertEqual(self.login('  ' + PASSWORD + '\n')[0], 200)
        self.env['ADMIN_PASSWORD'] = PASSWORD + '\n'                           # `echo pw | vercel env add` leaves a newline behind
        self.assertEqual(self.login()[0], 200)

    def test_too_many_wrong_passwords_lock_that_sender_out_even_for_the_right_one(self):
        for _ in range(adm.FAIL_LIMIT[0]):
            self.assertEqual(self.login('no', now=1_000_000)[0], 401)
        st, body = self.login(PASSWORD, now=1_000_100)
        self.assertEqual((st, body['error']), (429, 'locked'))
        self.assertEqual(self.login(PASSWORD, ip='9.9.9.9', now=1_000_100)[0], 200)      # somebody else is not locked out by it
        self.assertEqual(self.login(PASSWORD, now=1_000_000 + adm.FAIL_LIMIT[1] + 1)[0], 200)   # and it ends

    def test_a_right_password_clears_the_count(self):
        for _ in range(adm.FAIL_LIMIT[0] - 1):
            self.login('no')
        self.assertEqual(self.login()[0], 200)
        for _ in range(adm.FAIL_LIMIT[0] - 1):
            self.assertEqual(self.login('no')[0], 401)
        self.assertEqual(self.login()[0], 200)

    def test_not_set_up_means_nobody_gets_in(self):
        for env in ({}, {**self.env, 'ADMIN_PASSWORD': 'short'}, {**self.env, 'ADMIN_PASSWORD': ''}, {**self.env, 'ADMIN_GITHUB_TOKEN': ''}, {**self.env, 'SHELF_REPO': 'not a repo'}):
            self.assertFalse(adm.configured(adm.settings(env)))
            st, body = adm.handle({'action': 'login', 'password': PASSWORD}, '', '1.1.1.1', env)
            self.assertEqual((st, body['error']), (503, 'not_configured'))
        self.assertTrue(adm.configured(adm.settings(self.env)))

    def test_what_is_missing_is_named_never_shown(self):
        self.assertEqual(adm.problem(adm.settings(self.env)), '')
        for change, code in (({'ADMIN_PASSWORD': ''}, 'password'), ({'ADMIN_PASSWORD': 'short'}, 'short'), ({'ADMIN_PASSWORD': '12345678901'}, 'short'), ({'ADMIN_GITHUB_TOKEN': ''}, 'token'),
                             ({'SHELF_REPO': ''}, 'repo'), ({'SHELF_REPO': 'no slash'}, 'repo')):
            self.assertEqual(adm.problem(adm.settings({**self.env, **change})), code, change)
        self.assertEqual(adm.problem(adm.settings({**self.env, 'ADMIN_PASSWORD': '123456789012'})), '')           # exactly 12 is enough

    def test_nothing_secret_is_ever_in_an_answer(self):
        everything = [self.login(), self.login('no'), self.gh(self.session(), 'GET', '/repos/o/r')]
        text = json.dumps(everything)
        self.assertNotIn(GHTOKEN, text)
        self.assertNotIn(PASSWORD, text)


class TestSession(AdminBase):
    def test_a_made_up_or_changed_session_is_refused(self):
        s = self.session()
        exp = s.split('.')[1]
        for bad in ('', 'x', s[:-1] + ('0' if s[-1] != '0' else '1'), s.replace(exp, str(int(exp) + 99999)), 'v1.9999999999.' + '0' * 64, s + 'a', 'Bearer ' + s):
            st, body = self.gh(bad, 'GET', '/repos/o/r')
            self.assertEqual((st, body['error']), (401, 'session'), bad)
        self.untouched()

    def test_an_expired_session_is_refused(self):
        s = self.session(now=1_000_000)
        self.assertEqual(self.gh(s, 'GET', '/repos/o/r', now=1_000_000 + adm.SESSION_SECONDS + 1)[1]['error'], 'session')

    def test_changing_the_password_or_the_token_signs_everybody_out(self):
        s = self.session()
        self.assertEqual(self.gh(s, 'GET', '/repos/o/r')[0], 200)
        for change in ({'ADMIN_PASSWORD': PASSWORD + '2'}, {'ADMIN_GITHUB_TOKEN': GHTOKEN + '2'}, {'SHELF_REPO': 'o/other'}):
            old = dict(self.env)
            self.env.update(change)
            self.assertEqual(self.gh(s, 'GET', '/repos/o/r')[1]['error'], 'session', change)
            self.env = old

    def test_a_session_does_not_reveal_the_password_to_a_guesser(self):
        s = self.session()
        cfg = adm.settings(self.env)
        key_from_password_alone = adm.hashlib.sha256(('shelf-admin-session-v1\0%s\0%s\0%s' % (cfg['repo'], PASSWORD, '')).encode()).digest()
        msg = ('v1.' + s.split('.')[1]).encode()
        self.assertNotEqual(adm.hmac.new(key_from_password_alone, msg, adm.hashlib.sha256).hexdigest(), s.split('.')[2])

    def test_no_login_no_github(self):
        self.assertEqual(adm.handle({'action': 'gh', 'method': 'GET', 'path': '/repos/o/r'}, '', '1.1.1.1', self.env)[0], 401)
        self.untouched()


class TestWhatASessionMayDo(AdminBase):
    def setUp(self):
        super().setUp()
        self.s = self.session()

    def refused(self, method, path, body=None):
        st, answer = self.gh(self.s, method, path, body)
        self.assertEqual((st, answer.get('error')), (400, 'forbidden'), (method, path, body))

    def test_the_repository_answer_is_trimmed(self):
        st, body = self.gh(self.s, 'GET', '/repos/o/r')
        self.assertEqual((st, body['status'], body['data']), (200, 200, {'default_branch': 'trunk', 'permissions': {'push': True}}))
        self.assertEqual(self.site.hits[0][1].get('Authorization'), 'Bearer ' + GHTOKEN)      # GitHub gets the maintainer token, not the session

    def test_the_lists_the_page_reads(self):
        issues = [{'number': 5, 'title': 't', 'labels': [{'name': 'shelf-submission'}], 'body': 'b'}]
        self.site.put('/api/repos/o/r/issues?state=open&labels=shelf-submission&per_page=100', json.dumps(issues), 'application/json')
        self.site.put('/api/repos/o/r/issues?state=closed&labels=listed&per_page=30', '[]', 'application/json')
        self.site.put('/api/repos/o/r/issues/5', json.dumps(issues[0]), 'application/json')
        self.site.put('/api/repos/o/r/issues/5/comments?per_page=100', '[]', 'application/json')
        self.assertEqual(self.gh(self.s, 'GET', '/repos/o/r/issues?state=open&labels=shelf-submission&per_page=100')[1]['data'], issues)
        self.assertEqual(self.gh(self.s, 'GET', '/repos/o/r/issues?state=closed&labels=listed&per_page=30')[1]['data'], [])
        self.assertEqual(self.gh(self.s, 'GET', '/repos/o/r/issues/5')[1]['data']['number'], 5)
        self.assertEqual(self.gh(self.s, 'GET', '/repos/o/r/issues/5/comments?per_page=100')[1]['data'], [])

    def test_a_github_error_is_passed_on_as_it_is(self):
        st, body = self.gh(self.s, 'GET', '/repos/o/r/issues/77')
        self.assertEqual((st, body['ok'], body['status']), (200, True, 404))

    def test_approve_reject_and_the_reason(self):
        self.site.on('POST', '/api/repos/o/r/issues/5/labels', 200, [])
        self.site.on('POST', '/api/repos/o/r/issues/5/comments', 201, {'id': 1})
        self.assertEqual(self.gh(self.s, 'POST', '/repos/o/r/issues/5/labels', {'labels': ['approved']})[1]['status'], 200)
        self.assertEqual(self.gh(self.s, 'POST', '/repos/o/r/issues/5/labels', {'labels': ['rejected']})[1]['status'], 200)
        self.assertEqual(self.gh(self.s, 'POST', '/repos/o/r/issues/5/comments', {'body': '已拒绝：重复'})[1]['status'], 201)
        sent = [(p, b) for m, p, b, h in self.site.calls]
        self.assertEqual(sent, [('/api/repos/o/r/issues/5/labels', {'labels': ['approved']}), ('/api/repos/o/r/issues/5/labels', {'labels': ['rejected']}),
                                ('/api/repos/o/r/issues/5/comments', {'body': '已拒绝：重复'})])
        self.assertTrue(all(h.get('Authorization') == 'Bearer ' + GHTOKEN for m, p, b, h in self.site.calls))

    def test_only_these_two_labels_one_at_a_time(self):
        for labels in (['listed'], ['approved', 'rejected'], [], 'approved', ['takedown'], ['Approved'], None, [['approved']]):
            self.refused('POST', '/repos/o/r/issues/5/labels', {'labels': labels})
        self.refused('POST', '/repos/o/r/issues/5/labels', {})
        self.refused('POST', '/repos/o/r/issues/5/labels', None)
        self.untouched()

    def test_a_comment_has_a_size_and_is_text(self):
        for body in ({'body': ''}, {'body': 'x' * (adm.COMMENT_MAX + 1)}, {'body': 5}, {'body': ['a']}, {}, None):
            self.refused('POST', '/repos/o/r/issues/5/comments', body)
        self.untouched()

    def test_remove_and_daily_start_the_workflow_on_the_default_branch(self):
        self.site.on('POST', '/api/repos/o/r/actions/workflows/remove.yml/dispatches', 204, {})
        self.site.on('POST', '/api/repos/o/r/actions/workflows/daily.yml/dispatches', 204, {})
        self.assertEqual(self.gh(self.s, 'POST', '/repos/o/r/actions/workflows/remove.yml/dispatches', {'ref': 'evil', 'inputs': {'target': 'books-zhitree-cn'}})[1]['status'], 204)
        self.assertEqual(self.gh(self.s, 'POST', '/repos/o/r/actions/workflows/remove.yml/dispatches', {'ref': 'main', 'inputs': {'target': 'post:my-first_post.2'}})[1]['status'], 204)
        self.assertEqual(self.gh(self.s, 'POST', '/repos/o/r/actions/workflows/daily.yml/dispatches', {'ref': 'main'})[1]['status'], 204)
        sent = [(p.rsplit('/', 2)[1], b) for m, p, b, h in self.site.calls]
        self.assertEqual(sent, [('remove.yml', {'ref': 'trunk', 'inputs': {'target': 'books-zhitree-cn'}}), ('remove.yml', {'ref': 'trunk', 'inputs': {'target': 'post:my-first_post.2'}}),
                                ('daily.yml', {'ref': 'trunk'})])

    def test_no_other_workflow_and_no_odd_target(self):
        for wf in ('submissions', 'other', '../remove', 'remove.yml%2F..'):
            self.refused('POST', '/repos/o/r/actions/workflows/%s.yml/dispatches' % wf, {'ref': 'main'})
        self.refused('POST', '/repos/o/r/actions/workflows/remove.yml/dispatches', {'ref': 'main'})
        for target in ('', ' ', '-x', '../x', 'a b', 'a;rm -rf', 'post:', 'x' * 130, 5, None, ['a'], '$(id)', 'a\nb'):
            self.refused('POST', '/repos/o/r/actions/workflows/remove.yml/dispatches', {'ref': 'main', 'inputs': {'target': target}})
        self.refused('POST', '/repos/o/r/actions/workflows/remove.yml/dispatches', {'inputs': {'target': 'x', 'more': 'y'}})
        self.refused('POST', '/repos/o/r/actions/workflows/daily.yml/dispatches', {'ref': 'main', 'inputs': {'target': 'x'}})
        self.assertEqual(self.site.calls, [])

    def test_a_batch_is_one_workflow_run_with_checked_inputs(self):
        wf = '/repos/o/r/actions/workflows/batch.yml/dispatches'
        self.site.on('POST', '/api' + wf, 204, {})
        self.assertEqual(self.gh(self.s, 'POST', wf, {'ref': 'evil', 'inputs': {'mode': 'approve', 'numbers': '12,13,40'}})[1]['status'], 204)
        self.assertEqual(self.gh(self.s, 'POST', wf, {'inputs': {'mode': 'reject', 'numbers': '7', 'reason': '  太像已有的了\n请换一个  '}})[1]['status'], 204)
        self.assertEqual(self.gh(self.s, 'POST', wf, {'inputs': {'mode': 'approve', 'numbers': '7', 'reason': 'ignored for an approval'}})[1]['status'], 204)
        sent = [b for m, p, b, h in self.site.calls]
        self.assertEqual(sent, [{'ref': 'trunk', 'inputs': {'mode': 'approve', 'numbers': '12,13,40'}},
                                {'ref': 'trunk', 'inputs': {'mode': 'reject', 'numbers': '7', 'reason': '太像已有的了 请换一个'}},
                                {'ref': 'trunk', 'inputs': {'mode': 'approve', 'numbers': '7'}}])

    def test_a_batch_takes_only_numbers_and_a_mode(self):
        wf = '/repos/o/r/actions/workflows/batch.yml/dispatches'
        for inputs in ({'mode': 'list', 'numbers': '1'}, {'mode': 'approve'}, {'numbers': '1'}, {'mode': 'approve', 'numbers': ''}, {'mode': 'approve', 'numbers': '1,'},
                       {'mode': 'approve', 'numbers': '1, 2'}, {'mode': 'approve', 'numbers': '1;id'}, {'mode': 'approve', 'numbers': 'a'}, {'mode': 'approve', 'numbers': 5},
                       {'mode': 'approve', 'numbers': ','.join(str(i) for i in range(1, 62))}, {'mode': 'approve', 'numbers': '12345678'},
                       {'mode': 'reject', 'numbers': '1', 'reason': 'x' * (adm.COMMENT_MAX + 1)}, {'mode': 'reject', 'numbers': '1', 'reason': 5},
                       {'mode': 'approve', 'numbers': '1', 'target': 'x'}, None, []):
            self.refused('POST', wf, {'ref': 'main', 'inputs': inputs})
        self.refused('POST', wf, {'ref': 'main'})
        self.assertEqual(self.site.calls, [])
        self.site.on('POST', '/api' + wf, 204, {})
        self.assertEqual(self.gh(self.s, 'POST', wf, {'inputs': {'mode': 'approve', 'numbers': ','.join(str(i) for i in range(1, 61))}})[1]['status'], 204)     # sixty is the most

    def test_the_directory_file_comes_back_as_the_document(self):
        doc = {'spec': 1, 'shelves': [{'id': 'a'}], 'items': []}
        self.site.put('/api/repos/o/r/contents/docs/directory.json', json.dumps(doc), 'application/json')
        st, body = self.gh(self.s, 'GET', '/repos/o/r/contents/docs/directory.json')
        self.assertEqual((st, body['data']), (200, doc))
        self.assertEqual(self.site.hits[0][1].get('Accept'), 'application/vnd.github.raw+json')

    def test_everything_else_is_refused_before_github_is_asked(self):
        for method, path in [('GET', '/repos/o/r/contents/registry/shelves.json'), ('GET', '/repos/o/r/contents/docs/directory.json/../../.env'), ('GET', '/user'), ('GET', '/repos/x/y'),
                             ('GET', '/repos/o/r/issues'), ('GET', '/repos/o/r/issues?state=all&labels=listed&per_page=30'), ('GET', '/repos/o/r/issues?state=open&labels=secret&per_page=30'),
                             ('GET', '/repos/o/r/issues?state=open&labels=listed&per_page=101'), ('GET', '/repos/o/r/issues?state=open&labels=listed&per_page=0'),
                             ('GET', '/repos/o/r/issues/1/../../contents/x'), ('GET', '/repos/o/r/issues/abc'), ('GET', '/repos/o/r/issues/12345678'), ('GET', '/repos/o/r/issues/5?x=1'),
                             ('GET', '/repos/o/r/actions/secrets'), ('GET', '/repos/o/r/hooks'), ('GET', '/repos/o/R'), ('GET', 'https://evil.example/repos/o/r'), ('GET', '//evil.example/x'),
                             ('GET', '/repos/o/r\n/issues/5'), ('GET', ''), ('GET', None), ('POST', '/repos/o/r'), ('POST', '/repos/o/r/issues'), ('POST', '/repos/o/r/hooks'),
                             ('POST', '/repos/o/r/contents/docs/x.md'), ('PUT', '/repos/o/r/contents/docs/x.md'), ('PATCH', '/repos/o/r/issues/5'), ('DELETE', '/repos/o/r'),
                             ('DELETE', '/repos/o/r/issues/5/labels/approved'), ('GET ', '/repos/o/r'), (None, None), ('POST', '/repos/o/r/git/refs')]:
            self.refused(method, path, {'title': 'x', 'labels': ['approved']})
        self.untouched()

    def test_the_site_cannot_be_pointed_at_another_repository(self):
        self.env['SHELF_REPO'] = 'o/r'
        s = self.session()
        self.assertEqual(self.gh(s, 'GET', '/repos/o/r2')[1]['error'], 'forbidden')
        self.assertEqual(self.gh(s, 'POST', '/repos/other/r/issues/5/labels', {'labels': ['approved']})[1]['error'], 'forbidden')
        self.untouched()

    def test_github_down_is_a_502(self):
        self.site.close()
        st, body = self.gh(self.s, 'GET', '/repos/o/r/issues/5')
        self.assertEqual((st, body['error']), (502, 'github'))
        self.assertNotIn(GHTOKEN, json.dumps(body))


class Srv(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = '127.0.0.1', self.server_address[1]


class TestHttp(AdminBase):
    def serve(self):
        env = self.env

        class H(BaseHTTPRequestHandler):
            def do_GET(self): adm.respond(self, env, 'GET')
            def do_POST(self): adm.respond(self, env, 'POST')
            def do_OPTIONS(self): adm.respond(self, env, 'OPTIONS')
            def do_PUT(self): adm.respond(self, env, 'PUT')
            def log_message(self, *a): pass
        srv = Srv(('127.0.0.1', 0), H)
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv.server_address[1]

    def request(self, port, method, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
        c.request(method, '/api/admin', body=json.dumps(body) if isinstance(body, (dict, list)) else body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        out = (r.status, dict(r.getheaders()), json.loads(data) if data else None)
        c.close()
        return out

    def test_the_page_asks_whether_there_is_a_password(self):
        port = self.serve()
        st, h, body = self.request(port, 'GET')
        self.assertEqual((st, body), (200, {'ok': True, 'service': 'shelf-admin', 'configured': True}))
        self.assertEqual(h.get('Cache-Control'), 'no-store')
        self.env['ADMIN_PASSWORD'] = 'short'
        st, _, body = self.request(port, 'GET')
        self.assertEqual((st, body), (200, {'ok': True, 'service': 'shelf-admin', 'configured': False, 'problem': 'short'}))       # says what is wrong, not the value
        self.env['ADMIN_PASSWORD'] = ''
        self.assertEqual(self.request(port, 'GET')[2]['problem'], 'password')
        self.env['ADMIN_PASSWORD'] = PASSWORD
        self.env['ADMIN_GITHUB_TOKEN'] = ''
        self.assertEqual(self.request(port, 'GET')[2]['problem'], 'token')

    def test_sign_in_and_use_over_http(self):
        port = self.serve()
        st, h, body = self.request(port, 'POST', {'action': 'login', 'password': PASSWORD}, {'Content-Type': 'application/json'})
        self.assertEqual((st, body['ok']), (200, True))
        self.assertFalse([k for k in h if k.lower().startswith('access-control')])           # no CORS, ever
        st, _, body = self.request(port, 'POST', {'action': 'gh', 'method': 'GET', 'path': '/repos/o/r'}, {'Authorization': 'Bearer ' + body['session'], 'Content-Type': 'application/json'})
        self.assertEqual((st, body['data']['default_branch']), (200, 'trunk'))
        st, _, body = self.request(port, 'POST', {'action': 'gh', 'method': 'GET', 'path': '/repos/o/r'}, {'Content-Type': 'application/json'})
        self.assertEqual((st, body['error']), (401, 'session'))

    def test_a_page_of_another_site_is_refused(self):
        port = self.serve()
        mine = {'Content-Type': 'application/json', 'Origin': 'http://127.0.0.1:%d' % port}
        self.assertEqual(self.request(port, 'POST', {'action': 'login', 'password': PASSWORD}, mine)[0], 200)
        for origin in ('https://evil.example', 'http://127.0.0.1:1', 'null', 'https://127.0.0.1.evil.example'):
            st, h, body = self.request(port, 'POST', {'action': 'login', 'password': PASSWORD}, {'Content-Type': 'application/json', 'Origin': origin})
            self.assertEqual((st, body['error']), (403, 'origin'), origin)
            self.assertNotIn('Access-Control-Allow-Origin', h)
        self.assertEqual(self.request(port, 'OPTIONS', None, {'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'})[0], 405)
        self.assertEqual(self.slept, [])                                                      # the refused ones never got as far as the password

    def test_the_forwarded_host_counts_behind_the_platform(self):
        port = self.serve()
        h = {'Content-Type': 'application/json', 'Origin': 'https://adminbook.zhitree.cn', 'X-Forwarded-Host': 'adminbook.zhitree.cn'}
        self.assertEqual(self.request(port, 'POST', {'action': 'login', 'password': PASSWORD}, h)[0], 200)

    def test_size_json_and_methods(self):
        port = self.serve()
        try:
            st, _, body = self.request(port, 'POST', '{"x": "' + 'a' * (adm.BODY_LIMIT + 10) + '"}', {'Content-Type': 'application/json'})
            self.assertEqual((st, body['error']), (413, 'size'))
        except (ConnectionError, OSError):
            pass
        self.assertEqual(self.request(port, 'POST', 'not json', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request(port, 'POST', '', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request(port, 'POST', '[1]', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request(port, 'PUT', '{}', {'Content-Type': 'application/json'})[0], 405)

    def test_the_sender_for_the_limit_comes_from_the_proxy_header(self):
        port = self.serve()
        for _ in range(adm.FAIL_LIMIT[0]):
            self.request(port, 'POST', {'action': 'login', 'password': 'no'}, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.9, 10.0.0.1'})
        st, _, body = self.request(port, 'POST', {'action': 'login', 'password': PASSWORD}, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual((st, body['error']), (429, 'locked'))
        self.assertEqual(self.request(port, 'POST', {'action': 'login', 'password': PASSWORD}, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.10'})[0], 200)


if __name__ == '__main__':
    unittest.main()
