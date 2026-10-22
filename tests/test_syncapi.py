"""The optional sync code: encrypted shelves kept without accounts. Auth, compare-and-set, quotas, expiry, limits, the HTTP side and the Upstash wire format.

    python -X utf8 -m unittest discover -s tests -v
"""
import base64
import hashlib
import http.client
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import syncapi as sa  # noqa: E402

VID = 'a' * 32
AUTH = 'x' * 43
OTHER = 'b' * 43
KEY = 'c' * 32


def b64(n, ch='A'):
    return base64.b64encode((ch * n).encode()).decode()


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = sa.MemoryStore(clock=self.clock)
        self.env = {'IP_SALT': 's', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}

    def call(self, payload, ip='1.2.3.4', now=None):
        return sa.handle(payload, ip, self.env, self.store, now=self.clock() if now is None else now)

    def pull(self, vid=VID, auth=AUTH, rev=None, **kw):
        return self.call({'action': 'pull', 'id': vid, 'auth': auth, **({'rev': rev} if rev is not None else {})}, **kw)

    def push(self, blob, base_rev, vid=VID, auth=AUTH, **kw):
        return self.call({'action': 'push', 'id': vid, 'auth': auth, 'base_rev': base_rev, 'blob': blob}, **kw)

    def put(self, key=KEY, blob=None, vid=VID, auth=AUTH, **kw):
        return self.call({'action': 'body_put', 'id': vid, 'auth': auth, 'key': key, 'blob': blob or b64(100)}, **kw)

    def make(self):
        st, body = self.push(b64(50), 0)
        self.assertEqual((st, body['rev']), (200, 1))


class TestVault(Base):
    def test_nothing_there_until_the_first_push(self):
        st, body = self.pull()
        self.assertEqual((st, body), (200, {'ok': True, 'exists': False}))

    def test_the_first_push_makes_the_vault_and_pull_gives_it_back(self):
        st, body = self.push(b64(50), 0)
        self.assertEqual((st, body['ok'], body['rev']), (200, True, 1))
        st, body = self.pull()
        self.assertEqual((st, body['exists'], body['rev'], body['blob']), (200, True, 1, b64(50)))
        self.assertEqual(body['bodies'], [])
        self.assertEqual(body['quota']['used'], len(b64(50)))

    def test_pull_with_the_current_revision_sends_nothing(self):
        self.make()
        st, body = self.pull(rev=1)
        self.assertEqual((st, body.get('unchanged'), 'blob' in body), (200, True, False))
        st, body = self.pull(rev=0)
        self.assertEqual(body['blob'], b64(50))

    def test_compare_and_set_refuses_a_stale_push_and_says_which_revision_is_current(self):
        self.make()
        self.assertEqual(self.push(b64(60), 1)[1]['rev'], 2)
        st, body = self.push(b64(70), 1)
        self.assertEqual((st, body['error'], body['rev']), (409, 'conflict', 2))
        self.assertEqual(self.pull()[1]['blob'], b64(60))                                         # the refused push changed nothing

    def test_two_devices_pushing_at_once_one_wins(self):
        self.make()
        results = []
        barrier = threading.Barrier(8)

        def go(i):
            barrier.wait()
            results.append(self.push(b64(60 + i), 1, ip='9.9.9.%d' % i)[0])

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [200] + [409] * 7)
        self.assertEqual(self.pull()[1]['rev'], 2)

    def test_the_secret_is_kept_only_as_a_hash(self):
        self.make()
        dump = json.dumps(self.store.d)
        self.assertNotIn(AUTH, dump)
        self.assertIn(hashlib.sha256(AUTH.encode()).hexdigest(), dump)

    def test_the_wrong_secret_gets_nothing_in_every_action(self):
        self.make()
        self.put()
        for payload in ({'action': 'pull'}, {'action': 'push', 'base_rev': 1, 'blob': b64(5)}, {'action': 'body_put', 'key': KEY, 'blob': b64(5)}, {'action': 'body_get', 'key': KEY},
                        {'action': 'body_del', 'keys': [KEY]}, {'action': 'destroy'}):
            st, body = self.call({**payload, 'id': VID, 'auth': OTHER})
            self.assertEqual((st, body['error']), (403, 'auth'), payload['action'])
        self.assertEqual(self.pull()[1]['rev'], 1)                                              # untouched
        self.assertEqual(self.store.run([('HLEN', 'v:%s:b' % VID)])[0], 1)

    def test_an_unknown_vault_is_not_found_for_body_actions(self):
        for payload in ({'action': 'body_put', 'key': KEY, 'blob': b64(5)}, {'action': 'body_get', 'key': KEY}, {'action': 'body_del', 'keys': [KEY]}, {'action': 'destroy'}):
            st, body = self.call({**payload, 'id': VID, 'auth': AUTH})
            self.assertEqual((st, body['error']), (404, 'novault'), payload['action'])
        self.assertEqual(self.push(b64(5), 3)[0], 404)                                          # a revision that never existed

    def test_destroy_forgets_everything(self):
        self.make()
        self.put()
        self.assertEqual(self.call({'action': 'destroy', 'id': VID, 'auth': AUTH})[0], 200)
        self.assertEqual(self.pull()[1], {'ok': True, 'exists': False})
        self.assertEqual([k for k in self.store.d if k.startswith('v:')], [])


class TestTexts(Base):
    def test_put_get_replace_delete_and_the_room_used(self):
        self.make()
        used0 = self.pull()[1]['quota']['used']
        st, body = self.put(blob=b64(1000))
        self.assertEqual((st, body['quota']['used']), (200, used0 + len(b64(1000))))
        self.assertEqual(self.call({'action': 'body_get', 'id': VID, 'auth': AUTH, 'key': KEY})[1]['blob'], b64(1000))
        self.assertEqual(self.pull()[1]['bodies'], [KEY])
        self.assertEqual(self.put(blob=b64(400))[1]['quota']['used'], used0 + len(b64(400)))     # the same key: replaced, not added
        st, body = self.call({'action': 'body_del', 'id': VID, 'auth': AUTH, 'keys': [KEY, 'd' * 32]})
        self.assertEqual((st, body['removed']), (200, 1))
        self.assertEqual(self.pull()[1]['quota']['used'], used0)
        st, body = self.call({'action': 'body_get', 'id': VID, 'auth': AUTH, 'key': KEY})
        self.assertEqual((st, body['error']), (404, 'nobody'))

    def test_one_text_is_limited_and_so_is_the_whole_vault(self):
        self.make()
        self.assertEqual(self.put(blob='A' * (sa.ITEM_MAX + 4))[0], 413)
        env_quota = 5000
        self.env['SYNC_QUOTA'] = str(env_quota)
        st, body = self.put(key='1' * 32, blob=b64(3000))
        self.assertEqual(st, 200)
        st, body = self.put(key='2' * 32, blob=b64(3000))
        self.assertEqual((st, body['error']), (413, 'quota'))
        st, body = self.push(b64(9000), 1)                                                      # the list counts too
        self.assertEqual((st, body['error']), (413, 'quota'))
        self.assertEqual(self.put(key='1' * 32, blob=b64(3300))[0], 200)                        # replacing by something a little bigger still fits

    def test_the_number_of_texts_is_limited(self):
        self.make()
        old = sa.MAX_BODIES
        sa.MAX_BODIES = 3
        try:
            for i in range(3):
                self.assertEqual(self.put(key=('%d' % i) * 32, blob=b64(10))[0], 200)
            st, body = self.put(key='9' * 32, blob=b64(10))
            self.assertEqual((st, body['error']), (413, 'quota'))
            self.assertEqual(self.put(key='1' * 32, blob=b64(20))[0], 200)                       # replacing one is fine
        finally:
            sa.MAX_BODIES = old

    def test_the_index_is_limited(self):
        self.assertEqual(self.push('A' * (sa.INDEX_MAX + 4), 0)[0], 413)


class TestInputs(Base):
    def test_malformed_requests(self):
        bad = [None, [], 'x', {}, {'action': 'nope'}, {'action': 'pull'}, {'action': 'pull', 'id': 'short', 'auth': AUTH}, {'action': 'pull', 'id': VID, 'auth': 'short'},
               {'action': 'pull', 'id': VID.upper(), 'auth': AUTH}, {'action': 'pull', 'id': VID, 'auth': 5}, {'action': 'pull', 'id': ['x'], 'auth': AUTH},
               {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0}, {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': -1, 'blob': b64(5)},
               {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': '0', 'blob': b64(5)}, {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0, 'blob': 'not base64!'},
               {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0, 'blob': ''}, {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': True, 'blob': 'AAAA'},
               {'action': 'body_put', 'id': VID, 'auth': AUTH, 'key': 'xyz', 'blob': b64(5)}, {'action': 'body_put', 'id': VID, 'auth': AUTH, 'key': KEY},
               {'action': 'body_del', 'id': VID, 'auth': AUTH, 'keys': []}, {'action': 'body_del', 'id': VID, 'auth': AUTH, 'keys': ['x']},
               {'action': 'body_del', 'id': VID, 'auth': AUTH, 'keys': [KEY] * 101}, {'action': 'poll', 'answer': 'maybe'}, {'action': 'poll'}]
        for payload in bad:
            st, body = self.call(payload)
            self.assertEqual(st, 400, payload)
            self.assertEqual(body['error'], 'request')
        self.assertEqual(self.store.d, {})                                                       # nothing was created by a bad request

    def test_not_configured_says_so(self):
        st, body = sa.handle({'action': 'pull', 'id': VID, 'auth': AUTH}, '1.1.1.1', self.env, None)
        self.assertEqual((st, body['error']), (503, 'not_configured'))


class TestTimeAndLimits(Base):
    def test_a_vault_is_forgotten_after_the_days_it_is_not_used(self):
        self.make()
        self.put()
        self.clock.t += 179 * 86400
        self.assertTrue(self.pull()[1]['exists'])                                                # using it renews all of it
        self.clock.t += 179 * 86400
        st, body = self.pull()
        self.assertTrue(body['exists'])
        self.assertEqual(body['bodies'], [KEY])
        self.clock.t += 181 * 86400
        self.assertFalse(self.pull()[1]['exists'])
        self.assertEqual([k for k in self.store.d if k.startswith('v:') and self.store._live(k)], [])

    def test_the_answer_says_when_it_expires(self):
        self.make()
        self.assertEqual(self.pull()[1]['expires'], int(self.clock() + 180 * 86400))
        self.env['SYNC_TTL_DAYS'] = '30'
        self.assertEqual(self.pull()[1]['expires'], int(self.clock() + 30 * 86400))

    def test_a_sender_can_ask_only_so_often(self):
        self.make()
        for _ in range(sa.LIMITS['pull'][0] - 1):                                                # (one pull went to make()'s own count: none; the make was a push)
            self.assertEqual(self.pull()[0], 200)
        self.assertEqual(self.pull()[0], 200)
        st, body = self.pull()
        self.assertEqual((st, body['error']), (429, 'rate'))
        self.assertEqual(self.pull(ip='9.9.9.9')[0], 200)                                        # somebody else is not held back
        self.clock.t += 61
        self.assertEqual(self.pull()[0], 200)                                                    # and the minute passes

    def test_new_vaults_per_sender_per_day(self):
        for i in range(sa.CREATE_LIMIT[0]):
            self.assertEqual(self.push(b64(5), 0, vid='%032x' % (i + 1))[0], 200)
        st, body = self.push(b64(5), 0, vid='%032x' % 99)
        self.assertEqual((st, body['error']), (429, 'rate'))
        self.assertEqual(self.push(b64(5), 0, vid='%032x' % 99, ip='8.8.8.8')[0], 200)
        self.clock.t += 86400 + 1
        self.assertEqual(self.push(b64(5), 0, vid='%032x' % 100)[0], 200)

    def test_no_new_vault_when_the_database_is_full(self):
        self.env['SYNC_MAX_KEYS'] = '3'
        self.make()                                                                              # makes 3 keys (a, r, i) plus the counters of the limits
        st, body = self.push(b64(5), 0, vid='f' * 32, ip='7.7.7.7')
        self.assertEqual((st, body['error']), (503, 'full'))
        self.assertEqual(self.push(b64(7), 1)[0], 200)                                           # the one who has a vault goes on

    def test_a_secret_is_checked_before_anything_else_on_an_existing_vault(self):
        self.make()
        st, body = self.push(b64(5), 0, auth=OTHER)
        self.assertEqual((st, body['error']), (403, 'auth'))

    def test_the_poll_counts_one_answer_per_sender_per_day(self):
        self.assertEqual(self.call({'action': 'poll', 'answer': 'yes'})[0], 200)
        self.assertEqual(self.call({'action': 'poll', 'answer': 'no'})[0], 429)
        self.assertEqual(self.call({'action': 'poll', 'answer': 'later'}, ip='5.5.5.5')[0], 200)
        self.assertEqual(self.call({'action': 'poll', 'answer': 'yes'}, ip='6.6.6.6')[0], 200)
        self.assertEqual(sa.poll_counts(self.store), {'yes': 2, 'no': 0, 'later': 1})
        self.clock.t += 86400 + 1
        self.assertEqual(self.call({'action': 'poll', 'answer': 'no'})[0], 200)
        self.assertEqual(sa.poll_counts(self.store)['no'], 1)


class TestStoreFailure(Base):
    def test_a_broken_database_is_a_clean_503(self):
        class Broken:
            def run(self, commands):
                raise sa.StoreError('down')
        st, body = sa.handle({'action': 'pull', 'id': VID, 'auth': AUTH}, '1.1.1.1', self.env, Broken())
        self.assertEqual((st, body['error']), (503, 'storage'))


# ---------------------------------------------------------------- the HTTP side
class Handler(BaseHTTPRequestHandler):
    env = {}
    store = None

    def do_OPTIONS(self):
        sa.respond(self, self.env, 'OPTIONS', self.store)

    def do_GET(self):
        sa.respond(self, self.env, 'GET', self.store)

    def do_POST(self):
        sa.respond(self, self.env, 'POST', self.store)

    def log_message(self, *a):
        pass


class TestHttp(unittest.TestCase):
    def setUp(self):
        self.store = sa.MemoryStore()
        Handler.env = {'IP_SALT': 's', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}
        Handler.store = self.store
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
        c.request(method, '/api/sync', body=data, headers={**({'Content-Type': 'application/json'} if data else {}), **(headers or {})})
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, dict(r.getheaders()), (json.loads(raw) if raw else None)

    def test_get_says_whether_it_is_set_up_and_shows_the_poll(self):
        st, h, body = self.req('GET')
        self.assertEqual((st, body['service'], body['configured']), (200, 'shelf-sync', True))
        self.assertEqual(body['poll'], {'yes': 0, 'no': 0, 'later': 0})
        Handler.store = None
        st, h, body = self.req('GET')
        self.assertEqual((body['configured'], body['poll']), (False, None))

    def test_the_site_is_allowed_and_another_page_is_not(self):
        st, h, body = self.req('POST', {'action': 'pull', 'id': VID, 'auth': AUTH}, {'Origin': 'https://books.zhitree.cn'})
        self.assertEqual((st, h.get('Access-Control-Allow-Origin'), body['exists']), (200, 'https://books.zhitree.cn', False))
        st, h, body = self.req('POST', {'action': 'pull', 'id': VID, 'auth': AUTH}, {'Origin': 'https://evil.example'})
        self.assertEqual((st, body['error']), (403, 'origin'))
        st, h, _ = self.req('OPTIONS', None, {'Origin': 'https://books.zhitree.cn'})
        self.assertEqual((st, h.get('Access-Control-Allow-Methods')), (204, 'POST, OPTIONS'))

    def test_too_large_a_request_is_refused_before_it_is_read(self):
        st, h, body = self.req('POST', b'x' * (sa.BODY_LIMIT + 10), {'Content-Type': 'application/json'})
        self.assertEqual((st, body['error']), (413, 'size'))

    def test_a_round_trip_over_http_and_no_cache(self):
        st, h, body = self.req('POST', {'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0, 'blob': b64(100)})
        self.assertEqual((st, body['rev'], h.get('Cache-Control')), (200, 1, 'no-store'))
        st, h, body = self.req('POST', {'action': 'pull', 'id': VID, 'auth': AUTH, 'rev': 0})
        self.assertEqual(body['blob'], b64(100))
        st, h, body = self.req('POST', b'not json', {'Content-Type': 'application/json'})
        self.assertEqual((st, body['error']), (400, 'request'))

    def test_the_sender_address_is_what_is_limited_not_the_origin_header(self):
        for i in range(sa.LIMITS['pull'][0]):
            self.assertEqual(self.req('POST', {'action': 'pull', 'id': VID, 'auth': AUTH})[0], 200)
        self.assertEqual(self.req('POST', {'action': 'pull', 'id': VID, 'auth': AUTH})[0], 429)
        st, h, body = self.req('POST', {'action': 'pull', 'id': VID, 'auth': AUTH}, {'X-Forwarded-For': '5.6.7.8'})
        self.assertEqual(st, 200)


# ---------------------------------------------------------------- Upstash over HTTP: what goes over the wire
class FakeUpstash(BaseHTTPRequestHandler):
    store = sa.MemoryStore()
    seen = []
    fail = False

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        cmds = json.loads(self.rfile.read(n))
        FakeUpstash.seen.append((self.path, self.headers.get('Authorization'), cmds))
        if self.headers.get('Authorization') != 'Bearer secret-token':
            body, status = {'error': 'Unauthorized'}, 401
        elif FakeUpstash.fail:
            body, status = [{'error': 'ERR boom'} for _ in cmds], 200
        else:
            body, status = [{'result': r} for r in FakeUpstash.store.run(cmds)], 200
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class TestUpstash(unittest.TestCase):
    def setUp(self):
        FakeUpstash.store, FakeUpstash.seen, FakeUpstash.fail = sa.MemoryStore(), [], False
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeUpstash)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.srv.server_address[1]
        self.env = {'IP_SALT': 's'}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def store(self, token='secret-token'):
        return sa.UpstashStore(self.url, token)

    def test_the_whole_flow_goes_through_the_rest_pipeline(self):
        st = self.store()
        status, body = sa.handle({'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0, 'blob': b64(100)}, '1.1.1.1', self.env, st, now=1_000_000)
        self.assertEqual((status, body['rev']), (200, 1))
        status, body = sa.handle({'action': 'body_put', 'id': VID, 'auth': AUTH, 'key': KEY, 'blob': b64(500)}, '1.1.1.1', self.env, st, now=1_000_000)
        self.assertEqual(status, 200)
        status, body = sa.handle({'action': 'pull', 'id': VID, 'auth': AUTH}, '1.1.1.1', self.env, st, now=1_000_000)
        self.assertEqual((body['blob'], body['bodies'], body['quota']['used']), (b64(100), [KEY], len(b64(100)) + len(b64(500))))
        status, body = sa.handle({'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0, 'blob': b64(100)}, '1.1.1.1', self.env, st, now=1_000_001)
        self.assertEqual((status, body['error'], body['rev']), (409, 'conflict', 1))             # the compare-and-set script ran in the store
        self.assertTrue(all(path == '/pipeline' and auth == 'Bearer secret-token' for path, auth, _ in FakeUpstash.seen))
        evals = [c for _, _, cmds in FakeUpstash.seen for c in cmds if c[0] == 'EVAL']
        self.assertTrue(evals and all(c[1] == sa.CAS_SCRIPT and c[2] == '2' for c in evals))
        self.assertTrue(all(isinstance(x, str) for _, _, cmds in FakeUpstash.seen for c in cmds for x in c))      # numbers go as text

    def test_a_pull_needs_few_round_trips(self):
        st = self.store()
        sa.handle({'action': 'push', 'id': VID, 'auth': AUTH, 'base_rev': 0, 'blob': b64(100)}, '1.1.1.1', self.env, st, now=1_000_000)
        FakeUpstash.seen.clear()
        sa.handle({'action': 'pull', 'id': VID, 'auth': AUTH, 'rev': 1}, '1.1.1.1', self.env, st, now=1_000_001)
        self.assertLessEqual(len(FakeUpstash.seen), 4)

    def test_a_wrong_token_or_an_error_or_a_dead_server_is_a_clean_503(self):
        for st in (self.store('wrong'), sa.UpstashStore('http://127.0.0.1:1', 'x', timeout=1)):
            status, body = sa.handle({'action': 'pull', 'id': VID, 'auth': AUTH}, '1.1.1.1', self.env, st)
            self.assertEqual((status, body['error']), (503, 'storage'))
        FakeUpstash.fail = True
        status, body = sa.handle({'action': 'pull', 'id': VID, 'auth': AUTH}, '1.1.1.1', self.env, self.store())
        self.assertEqual((status, body['error']), (503, 'storage'))

    def test_the_store_is_read_from_either_set_of_names(self):
        self.assertIsNone(sa.store_from_env({}))
        self.assertIsNone(sa.store_from_env({'UPSTASH_REDIS_REST_URL': 'http://insecure.example', 'UPSTASH_REDIS_REST_TOKEN': 't'}))
        self.assertIsNone(sa.store_from_env({'UPSTASH_REDIS_REST_URL': 'https://x.upstash.io'}))
        a = sa.store_from_env({'UPSTASH_REDIS_REST_URL': 'https://x.upstash.io/', 'UPSTASH_REDIS_REST_TOKEN': 't'})
        b = sa.store_from_env({'KV_REST_API_URL': 'https://y.upstash.io', 'KV_REST_API_TOKEN': 'u'})
        self.assertEqual((a.url, a.token, b.url, b.token), ('https://x.upstash.io', 't', 'https://y.upstash.io', 'u'))


if __name__ == '__main__':
    unittest.main()
