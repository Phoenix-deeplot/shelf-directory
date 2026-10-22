"""The public submit function: logic, limits, duplicate checks, CORS, and the Vercel handler itself. A fake GitHub and a fake directory; no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import base64
import http.client
import importlib.util
import json
import os
import socketserver
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import submission as sub  # noqa: E402
from test_aggregate import Base  # noqa: E402
from test_posts_dedupe import POST_BODY  # noqa: E402

ISSUES = '/api/repos/o/r/issues?state=open&labels=%s&per_page=100'


class SubBase(Base):
    def setUp(self):
        super().setUp()
        sub._CACHE.clear()
        self.env = {'SHELF_REPO': 'o/r', 'SUBMIT_GITHUB_TOKEN': 'tok', 'DIRECTORY_URL': self.site.url + '/dir/', 'IP_SALT': 'salt', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}
        self.directory({})
        self.open_issues([], [])
        self.site.on('POST', '/api/repos/o/r/issues', 201, {'number': 41})
        self.site.put('/api/repos/o/r/contents/registry/rejected.json', json.dumps({'message': 'Not Found'}), 'application/json', status=404)
        self.site.put('/api/repos/o/r/contents/registry/shelves.json', json.dumps({'message': 'Not Found'}), 'application/json', status=404)

    def registry(self, *shelves):
        content = base64.b64encode(json.dumps({'shelves': list(shelves)}).encode()).decode()
        self.site.put('/api/repos/o/r/contents/registry/shelves.json', json.dumps({'content': content}), 'application/json')
        sub._CACHE.clear()

    def directory(self, d):
        self.site.put('/dir/directory.json', json.dumps({'spec': 1, **d}), 'application/json')
        sub._CACHE.clear()

    def open_issues(self, shelf, post):
        self.site.put(ISSUES % 'shelf-submission', json.dumps(shelf), 'application/json')
        self.site.put(ISSUES % 'post-submission', json.dumps(post), 'application/json')

    def rejected(self, *keys):
        content = base64.b64encode(json.dumps({'keys': [agg.key_hash(k) for k in keys]}).encode()).decode()
        self.site.put('/api/repos/o/r/contents/registry/rejected.json', json.dumps({'content': content}), 'application/json')
        sub._CACHE.clear()

    def created(self):
        return [b for m, p, b, h in self.site.calls if (m, p) == ('POST', '/api/repos/o/r/issues')]

    def send(self, payload, ip='1.2.3.4'):
        return sub.handle(payload, ip, self.env)


URL = {'kind': 'url', 'url': 'https://blog.example.org/', 'rights': True}


class TestUrl(SubBase):
    def test_a_good_address_becomes_an_issue_the_pipeline_understands(self):
        st, body = self.send({**URL, 'tags': '供应链， 采购'})
        self.assertEqual((st, body['ok'], body['status']), (200, True, 'queued'))
        issue, = self.created()
        self.assertEqual(issue['labels'], ['shelf-submission'])
        self.assertEqual(issue['title'], '[收录] blog.example.org/')
        parsed = agg.parse_issue({'issue': {'number': 41, 'body': issue['body']}})
        self.assertEqual((parsed['kind'], parsed['url'], parsed['tags'], parsed['rights']), ('shelf', 'https://blog.example.org/', ['供应链', '采购'], True))
        self.assertTrue(all(h.get('Authorization') == 'Bearer tok' for m, p, b, h in self.site.calls))

    def test_the_senders_address_is_stored_only_as_a_salted_hash(self):
        self.send(URL, ip='203.0.113.7')
        body = self.created()[0]['body']
        self.assertNotIn('203.0.113.7', body)
        self.assertIn('ip:' + sub.ip_hash('203.0.113.7', 'salt'), body)
        self.assertNotEqual(sub.ip_hash('203.0.113.7', 'salt'), sub.ip_hash('203.0.113.7', 'other'))

    def test_the_tick_is_needed(self):
        for payload in ({**URL, 'rights': False}, {k: v for k, v in URL.items() if k != 'rights'}, {**URL, 'rights': 'yes'}):
            st, body = self.send(payload)
            self.assertEqual((st, body['error']), (400, 'rights'))
        self.assertEqual(self.created(), [])

    def test_a_robot_that_fills_the_hidden_field_is_told_it_worked(self):
        st, body = self.send({**URL, 'website': 'http://spam.example'})
        self.assertEqual((st, body['ok']), (200, True))
        self.assertEqual(self.created(), [])

    def test_bad_addresses(self):
        for url in ('', 'not a url', 'https://127.0.0.1/', 'https://localhost/', 'https://192.168.0.5/x', 'https://[::1]/', 'https://user:pw@blog.example.org/', 'https://blog.example.org:8443/',
                    'https://github.com/someone', 'https://intranet/', 'https://db.internal/', 'ftp://blog.example.org/'):
            st, body = self.send({**URL, 'url': url})
            self.assertEqual((st, body['error']), (400, 'url'), url)
        self.assertEqual(self.created(), [])

    def test_the_address_bar_of_a_reader_is_not_a_public_address(self):
        self.directory({'shelves': [{'id': 'books', 'home': 'https://books.example.org/', 'kind': 'feed'}]})
        for url in ('https://books.example.org/#read/local%3Amv9c3hossw0sd', 'https://elsewhere.example.org/#read/x'):
            st, body = self.send({**URL, 'url': url})
            self.assertEqual((st, body['error']), (400, 'reader'), url)                                   # not "already listed", which is what the home page it falls back to would say
            self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': url})[1]['error'], 'reader')
        self.assertEqual(self.created(), [])

    def test_nothing_is_fetched_from_the_address(self):
        self.send({**URL, 'url': 'https://blog.example.org/post'})
        self.assertFalse(any('blog.example.org' in p for p, h in self.site.hits))

    def test_already_in_the_directory(self):
        self.directory({'shelves': [{'id': 'blog', 'home': 'https://www.blog.example.org/', 'feed': 'https://blog.example.org/feed.xml', 'kind': 'feed'}],
                        'items': [{'url': 'https://other.example.org/post/1', 'shelf': 'blog'}]})
        for url in ('https://blog.example.org', 'http://blog.example.org/?utm_source=x', 'https://blog.example.org/feed.xml', 'https://other.example.org/post/1/index.html'):
            st, body = self.send({**URL, 'url': url})
            self.assertEqual((st, body['error']), (409, 'listed'), url)
        st, body = self.send({**URL, 'url': 'https://blog.example.org/a-new-post'})
        self.assertEqual(st, 200)                                                          # a page of a listed site is not a duplicate

    def test_a_repository_or_a_folder_of_a_listed_one(self):
        self.directory({'shelves': [{'id': 'gh-o-r', 'home': 'https://github.com/o/r', 'kind': 'repo'}]})
        for url in ('https://github.com/o/r', 'https://github.com/o/r/tree/main/docs', 'https://github.com/O/R/tree/dev/a/b'):
            self.assertEqual(self.send({**URL, 'url': url})[1]['error'], 'listed', url)
        self.assertEqual(self.send({**URL, 'url': 'https://github.com/o/other'})[0], 200)

    def test_listed_in_the_repository_but_not_yet_in_the_published_directory(self):
        """Approved a minute ago: registry/shelves.json has it, the published directory.json (rebuilt later, cached by the CDN) does not."""
        self.registry({'id': 'blog', 'kind': 'feed', 'url': 'https://blog.example.org/', 'source': 'https://blog.example.org/feed.xml'},
                      {'id': 'gh-o-r', 'kind': 'repo', 'url': 'https://github.com/o/r/tree/main/docs', 'source': 'github:o/r/docs', 'repo': {'owner': 'o', 'repo': 'r', 'ref': '', 'path': 'docs'}})
        for url in ('https://blog.example.org', 'http://www.blog.example.org/?utm_source=x', 'https://blog.example.org/feed.xml', 'https://github.com/o/r/tree/main/docs/a'):
            st, body = self.send({**URL, 'url': url})
            self.assertEqual((st, body['error']), (409, 'listed'), url)
        self.assertEqual(self.send({**URL, 'url': 'https://github.com/o/r'})[0], 200)      # the whole repository is more than the folder that is listed
        self.assertEqual(self.created().__len__(), 1)

    def test_the_repository_cannot_be_read_is_not_an_answer_of_not_listed(self):
        self.site.put('/api/repos/o/r/contents/registry/shelves.json', json.dumps({'message': 'Server Error'}), 'application/json', status=500)
        sub._CACHE.clear()
        st, body = self.send(URL)
        self.assertEqual((st, body['error']), (502, 'github'))
        self.assertEqual(self.created(), [])

    def test_a_token_that_may_not_read_files_falls_back_to_the_published_directory(self):
        self.site.put('/api/repos/o/r/contents/registry/shelves.json', json.dumps({'message': 'Resource not accessible by personal access token'}), 'application/json', status=403)
        self.directory({'shelves': [{'id': 'blog', 'home': 'https://blog.example.org/', 'kind': 'feed'}]})
        self.assertEqual(self.send(URL)[1]['error'], 'listed')                              # still refused, from directory.json
        self.assertEqual(self.send({**URL, 'url': 'https://other.example.org/'})[0], 200)   # and the others still go through

    def test_already_waiting(self):
        self.open_issues([{'number': 3, 'body': sub.url_issue('https://blog.example.org/', [], 'abc'), 'created_at': '2026-10-14T00:00:00Z'}], [])
        st, body = self.send(URL)
        self.assertEqual((st, body['error']), (409, 'pending'))
        self.assertEqual(self.send({**URL, 'url': 'https://www.blog.example.org?utm_medium=x'})[1]['error'], 'pending')

    def test_rejected_before(self):
        self.rejected(agg.url_key('https://blog.example.org/'))
        st, body = self.send(URL)
        self.assertEqual((st, body['error']), (409, 'rejected'))
        self.assertNotIn('原因', body['message'])                                           # the reason is never told

    def test_rate_limit_per_sender(self):
        mine = sub.ip_hash('9.9.9.9', 'salt')
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        old = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 3 * 86400))
        recent = [{'number': n, 'body': sub.url_issue('https://s%d.example.org/' % n, [], mine), 'created_at': now} for n in range(sub.PER_IP_PER_DAY)]
        self.open_issues(recent, [])
        self.assertEqual(self.send(URL, ip='9.9.9.9')[1]['error'], 'rate')
        self.assertEqual(self.send(URL, ip='8.8.8.8')[0], 200)                              # somebody else
        self.open_issues([{**i, 'created_at': old} for i in recent], [])
        self.assertEqual(self.send(URL, ip='9.9.9.9')[0], 200)                              # three days ago does not count

    def test_the_owner_can_raise_the_limits_and_nobody_can_break_them(self):
        mine = sub.ip_hash('9.9.9.9', 'salt')
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        recent = [{'number': n, 'body': sub.url_issue('https://s%d.example.org/' % n, [], mine), 'created_at': now} for n in range(sub.PER_IP_PER_DAY)]
        self.open_issues(recent, [])
        self.assertEqual(self.send(URL, ip='9.9.9.9')[1]['error'], 'rate')                  # the default
        self.env['SUBMIT_PER_DAY'] = '60'
        self.assertEqual(self.send(URL, ip='9.9.9.9')[0], 200)                              # raised by the owner (bulk-listing the recommended sources)
        self.assertEqual(sub.settings({**self.env, 'SUBMIT_PER_DAY': 'many'})['per_day'], sub.PER_IP_PER_DAY)       # not a number: the default
        self.assertEqual(sub.settings({**self.env, 'SUBMIT_PER_DAY': '0'})['per_day'], 1)                          # never zero
        self.assertEqual(sub.settings({**self.env, 'SUBMIT_PER_DAY': '99999'})['per_day'], 500)                    # never unbounded
        self.assertEqual(sub.settings({**self.env, 'SUBMIT_MAX_OPEN': '99999'})['max_open'], 1000)
        self.open_issues([{'number': n, 'body': sub.url_issue('https://s%d.example.org/' % n, [], 'x%d' % n), 'created_at': '2026-10-14T00:00:00Z'} for n in range(sub.MAX_OPEN)], [])
        self.env['SUBMIT_MAX_OPEN'] = '150'
        self.assertEqual(self.send(URL)[0], 200)                                           # 100 are waiting, the owner allows 150

    def test_the_queue_can_be_full(self):
        self.open_issues([{'number': n, 'body': sub.url_issue('https://s%d.example.org/' % n, [], 'x%d' % n), 'created_at': '2026-10-14T00:00:00Z'} for n in range(sub.MAX_OPEN)], [])
        st, body = self.send(URL)
        self.assertEqual((st, body['error']), (429, 'full'))

    def test_pull_requests_in_the_issue_list_are_ignored(self):
        self.open_issues([{'number': 3, 'pull_request': {}, 'body': sub.url_issue('https://blog.example.org/', [], 'abc'), 'created_at': '2026-10-14T00:00:00Z'}], [])
        self.assertEqual(self.send(URL)[0], 200)

    def test_github_down(self):
        self.site.put(ISSUES % 'shelf-submission', '{}', 'application/json', status=500)
        self.assertEqual(self.send(URL)[0], 502)
        self.open_issues([], [])
        self.site.on('POST', '/api/repos/o/r/issues', 403, {'message': 'Resource not accessible by integration'})
        st, body = self.send(URL)
        self.assertEqual((st, body['error']), (502, 'github'))
        self.assertNotIn('Resource not accessible', json.dumps(body))                      # the visitor is not shown GitHub's reason

    def test_not_configured(self):
        for missing in ('SHELF_REPO', 'SUBMIT_GITHUB_TOKEN'):
            env = {k: v for k, v in self.env.items() if k != missing}
            self.assertEqual(sub.handle(URL, '1.1.1.1', env)[0], 503)

    def test_garbage(self):
        for payload in (None, [], 'x', 5, {}, {'kind': 'other', 'rights': True}):
            self.assertEqual(self.send(payload)[0], 400, payload)


POST = {'kind': 'post', 'title': '读者的文章', 'author': '小明', 'tags': ['笔记'], 'body': POST_BODY, 'rights': True}


class TestPost(SubBase):
    def test_a_post_becomes_an_issue_the_pipeline_understands(self):
        st, body = self.send(POST)
        self.assertEqual((st, body['kind']), (200, 'post'))
        issue, = self.created()
        self.assertEqual((issue['labels'], issue['title']), (['post-submission'], '[投稿] 读者的文章'))
        parsed = agg.parse_issue({'issue': {'number': 41, 'body': issue['body']}})
        self.assertEqual((parsed['kind'], parsed['title'], parsed['author'], parsed['tags'], parsed['rights']), ('post', '读者的文章', '小明', ['笔记'], True))
        self.assertEqual(parsed['body'], POST_BODY.strip())

    def test_lines_that_look_like_form_headings_do_not_cut_the_post(self):
        body = POST_BODY + '\n### 权利确认\n\n这一行在文章里。\n\n### 标题\n\n这也是。\n'
        self.send({**POST, 'body': body})
        parsed = agg.parse_issue({'issue': {'number': 41, 'body': self.created()[0]['body']}})
        self.assertEqual(parsed['body'], body.strip())
        self.assertEqual(parsed['title'], '读者的文章')
        self.assertTrue(parsed['rights'])

    def test_the_same_checks_as_the_action(self):
        for name, p in {'short': {**POST, 'body': '太短'}, 'no title': {**POST, 'title': ''}, 'long': {**POST, 'body': '字' * (agg.POST_BODY_MAX + 1)}}.items():
            st, body = self.send(p)
            self.assertEqual((st, body['error']), (400, 'post'), name)

    def test_an_issue_can_hold_the_longest_post(self):
        self.assertGreater(sub.ISSUE_MAX, agg.POST_BODY_MAX)                              # GitHub allows 65536 characters in an issue

    def test_the_same_text_is_not_taken_twice(self):
        h = agg.post_hash(POST_BODY)
        self.directory({'items': [{'url': 'https://dir.example/p/x/', 'shelf': 'posts', 'hash': h}]})
        self.assertEqual(self.send(POST)[1]['error'], 'listed')
        self.directory({})
        self.open_issues([], [{'number': 8, 'body': sub.post_issue({'title': '别的标题', 'author': '', 'tags': [], 'body': POST_BODY.replace('\n\n', '\n\n\n')}, 'abc'), 'created_at': '2026-10-14T00:00:00Z'}])
        self.assertEqual(self.send(POST)[1]['error'], 'pending')
        self.open_issues([], [])
        self.rejected(h)
        self.assertEqual(self.send(POST)[1]['error'], 'rejected')


def canned_probe(duplicate=None, warnings=()):
    items = [{'url': 'https://blog.example.org/p/%d' % n, 'title': 'Post %d' % n, 'summary': '', 'image': '', 'date': '2026-10-0%d' % n, 'tags': ['供应链'] if n % 2 else [], 'lang': 'zh', 'type': 'article'} for n in range(1, 8)]
    return {'entry': {'kind': 'feed', 'url': 'https://blog.example.org/', 'source': 'https://blog.example.org/feed.xml'}, 'shelf': {'name': 'Example blog', 'lang': 'zh', 'description': 'About it', 'license': 'CC BY'},
            'items': items, 'warnings': list(warnings), 'duplicate': duplicate, 'existing': ''}


class TestCheck(SubBase):
    """The "check" step of the dialog: what will become of this address / text? Nothing is created."""

    def setUp(self):
        super().setUp()
        self.probed = []
        self.real_probe = sub.probe_for_check
        sub.probe_for_check = lambda url: (self.probed.append(url), canned_probe())[1]
        self.addCleanup(setattr, sub, 'probe_for_check', self.real_probe)
        sub._CHECKS.clear()

    def test_an_address_is_read_and_described_without_creating_anything(self):
        st, body = self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})                    # no tick is needed to look
        self.assertEqual((st, body['status']), (200, 'checked'))
        r = body['result']
        self.assertEqual((r['kind_zh'], r['name'], r['found'], r['lang'], r['license']), ('订阅源书架（整个网站 / 博客 / 频道）', 'Example blog', 7, 'zh', 'CC BY'))
        self.assertEqual([i['title'] for i in r['latest']], ['Post 7', 'Post 6', 'Post 5', 'Post 4', 'Post 3'])      # five, newest first
        self.assertEqual(r['tags'], ['供应链'])
        self.assertEqual(self.probed, ['https://blog.example.org/'])
        self.assertEqual(self.created(), [])

    def test_the_cheap_checks_come_first_and_nothing_is_read_for_them(self):
        self.directory({'shelves': [{'id': 'blog', 'home': 'https://blog.example.org/', 'feed': '', 'kind': 'feed'}]})
        self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': 'http://www.blog.example.org?utm_source=x'})[1]['error'], 'listed')
        self.directory({})
        for bad in ('https://127.0.0.1/x', 'https://localhost/', 'not a url', 'https://github.com/someone'):
            self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': bad})[1]['error'], 'url', bad)
        self.open_issues([{'number': 3, 'body': sub.url_issue('https://blog.example.org/', [], 'abc'), 'created_at': '2026-10-14T00:00:00Z'}], [])
        self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})[1]['error'], 'pending')
        self.assertEqual(self.probed, [])                                                                              # no request to any of these sites

    def test_a_listed_address_found_only_by_the_registry_that_came_with_the_deployment(self):
        sub.probe_for_check = lambda url: canned_probe(duplicate=('shelf', 'blog-example-org'))
        st, body = self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})
        self.assertEqual((st, body['error']), (409, 'listed'))

    def test_an_address_that_cannot_be_read(self):
        sub.probe_for_check = self.real_probe
        real = agg.probe
        agg.probe = lambda url: (_ for _ in ()).throw(agg.Refused('网站返回了错误（HTTP 404）', 'The site answered with HTTP 404', 404))
        self.addCleanup(setattr, agg, 'probe', real)
        old = (agg.TIMEOUT, agg.REPO_READ)
        st, body = self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/nothing'})
        self.assertEqual((st, body['error'], body['message']), (422, 'unreachable', '网站返回了错误（HTTP 404）'))
        self.assertEqual((agg.TIMEOUT, agg.REPO_READ), old)                                                           # the tighter limits are for this call only

    def test_the_read_runs_with_tighter_limits(self):
        sub.probe_for_check = self.real_probe
        seen = []
        real = agg.probe
        agg.probe = lambda url: (seen.append((agg.TIMEOUT, agg.REPO_READ)), canned_probe())[1]
        self.addCleanup(setattr, agg, 'probe', real)
        self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})
        self.assertEqual(seen, [(7, 6)])

    def test_the_whole_read_has_a_deadline_and_it_is_given_back(self):
        sub.probe_for_check = self.real_probe
        seen = []
        real = agg.probe
        agg.probe = lambda url: (seen.append(agg.DEADLINE - time.monotonic()), canned_probe())[1]
        self.addCleanup(setattr, agg, 'probe', real)
        self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})
        self.assertTrue(0 < seen[0] <= sub.CHECK_SECONDS)
        self.assertIsNone(agg.DEADLINE)
        agg.DEADLINE = time.monotonic() - 1                                                                           # past the deadline: no request is made at all
        self.addCleanup(setattr, agg, 'DEADLINE', None)
        with self.assertRaises(agg.Refused):
            agg.fetch(self.site.url + '/dir/directory.json')

    def test_a_post_is_checked_with_the_same_rules(self):
        st, body = self.send({'kind': 'check', 'what': 'post', 'title': '读者的文章', 'author': '小明', 'tags': ['笔记'], 'body': POST_BODY})
        self.assertEqual((st, body['result']['what'], body['result']['title'], body['result']['links'], body['result']['domains']), (200, 'post', '读者的文章', 1, ['example.com']))
        self.assertEqual(self.created(), [])
        for p in ({'title': '', 'body': POST_BODY}, {'title': 'x', 'body': '太短'}):
            self.assertEqual(self.send({'kind': 'check', 'what': 'post', **p})[1]['error'], 'post')
        self.directory({'items': [{'url': 'https://dir.example/p/x/', 'shelf': 'posts', 'hash': agg.post_hash(POST_BODY)}]})
        self.assertEqual(self.send({'kind': 'check', 'what': 'post', 'title': '另一个标题', 'body': POST_BODY})[1]['error'], 'listed')

    def test_checks_are_limited_per_sender(self):
        for n in range(sub.CHECK_LIMIT[0]):
            self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'}, ip='5.5.5.5')[0], 200)
        st, body = self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'}, ip='5.5.5.5')
        self.assertEqual((st, body['error']), (429, 'rate'))
        self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'}, ip='6.6.6.6')[0], 200)
        later = time.time() + sub.CHECK_LIMIT[1] + 1
        self.assertEqual(sub.handle({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'}, '5.5.5.5', self.env, now=later)[0], 200)

    def test_robots_and_unknown_requests(self):
        self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/', 'website': 'spam'})[0], 200)         # told it worked
        self.assertEqual(self.probed, [])
        self.assertEqual(self.send({'kind': 'check', 'what': 'other'})[1]['error'], 'request')
        self.assertEqual(sub.handle({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'}, '1.1.1.1', {})[0], 503)


class Srv(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = '127.0.0.1', self.server_address[1]


class TestHttp(SubBase):
    def serve(self, handler_class):
        srv = Srv(('127.0.0.1', 0), handler_class)
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv.server_address[1]

    def request(self, port, method, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
        c.request(method, '/api/submit', body=json.dumps(body) if isinstance(body, (dict, list)) else body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        out = (r.status, dict(r.getheaders()), json.loads(data) if data else None)
        c.close()
        return out

    def handler(self):
        env = self.env

        class H(BaseHTTPRequestHandler):
            def do_OPTIONS(self): sub.respond(self, env, 'OPTIONS')
            def do_GET(self): sub.respond(self, env, 'GET')
            def do_POST(self): sub.respond(self, env, 'POST')
            def log_message(self, *a): pass
        return H

    def test_cors_and_the_origin_rule(self):
        port = self.serve(self.handler())
        ok = {'Origin': 'https://books.zhitree.cn', 'Content-Type': 'application/json'}
        st, h, _ = self.request(port, 'OPTIONS', headers=ok)
        self.assertEqual((st, h.get('Access-Control-Allow-Origin'), 'POST' in h.get('Access-Control-Allow-Methods', '')), (204, 'https://books.zhitree.cn', True))
        st, h, body = self.request(port, 'POST', URL, ok)
        self.assertEqual((st, h.get('Access-Control-Allow-Origin'), body['ok']), (200, 'https://books.zhitree.cn', True))
        st, h, body = self.request(port, 'POST', URL, {'Origin': 'https://evil.example', 'Content-Type': 'application/json'})
        self.assertEqual((st, body['error'], 'Access-Control-Allow-Origin' in h), (403, 'origin', False))
        st, h, _ = self.request(port, 'OPTIONS', headers={'Origin': 'https://evil.example'})
        self.assertNotIn('Access-Control-Allow-Origin', h)
        self.assertEqual(len(self.created()), 1)                                           # only the allowed browser page created an issue

    def test_a_program_without_an_origin_goes_through_the_same_limits(self):
        port = self.serve(self.handler())
        self.assertEqual(self.request(port, 'POST', URL, {'Content-Type': 'application/json'})[0], 200)

    def test_sender_address_comes_from_the_proxy_header(self):
        port = self.serve(self.handler())
        self.request(port, 'POST', URL, {'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.9, 10.0.0.1'})
        self.assertIn('ip:' + sub.ip_hash('203.0.113.9', 'salt'), self.created()[0]['body'])

    def test_size_and_json(self):
        port = self.serve(self.handler())
        try:                                                  # the server answers 413 without reading the body: the sender sees 413, or a reset if it is still sending
            st, _, body = self.request(port, 'POST', '{"x": "' + 'a' * (sub.BODY_LIMIT + 10) + '"}', {'Content-Type': 'application/json'})
            self.assertEqual((st, body['error']), (413, 'size'))
        except (ConnectionError, OSError):
            pass
        self.assertEqual(self.created(), [])
        self.assertEqual(self.request(port, 'POST', 'not json', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request(port, 'POST', '', {'Content-Type': 'application/json'})[0], 400)

    def test_health_shows_no_secret(self):
        port = self.serve(self.handler())
        st, _, body = self.request(port, 'GET')
        self.assertEqual((st, body), (200, {'ok': True, 'service': 'shelf-submit', 'configured': True, 'repo': 'o/r', 'directory': self.site.url + '/dir/'}))
        self.assertNotIn('tok', json.dumps(body))                                          # the token is never shown

    def test_local_origins_only_when_asked_for(self):
        port = self.serve(self.handler())
        local = {'Origin': 'http://127.0.0.1:8801', 'Content-Type': 'application/json'}
        self.assertEqual(self.request(port, 'POST', URL, local)[0], 403)
        self.env['ALLOW_LOCAL_ORIGIN'] = '1'
        self.assertEqual(self.request(port, 'POST', URL, local)[0], 200)

    def test_the_vercel_entry_point(self):
        spec = importlib.util.spec_from_file_location('vercel_submit', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'api', 'submit.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(issubclass(mod.handler, BaseHTTPRequestHandler))
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        try:
            port = self.serve(mod.handler)
            st, h, body = self.request(port, 'POST', URL, {'Origin': 'https://books.zhitree.cn', 'Content-Type': 'application/json'})
            self.assertEqual((st, body['ok']), (200, True))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


if __name__ == '__main__':
    unittest.main()
