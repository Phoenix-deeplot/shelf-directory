"""Anonymous feedback (feedbackapi.py): what is kept and what not, a few a day per reader, the same words once a day, a honeypot, the newest 2000 and 180 days, the admin list and delete, the HTTP side.

    python -X utf8 -m unittest tests.test_feedback -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import adminapi as adm  # noqa: E402
import feedbackapi as fb  # noqa: E402
import syncapi as sa  # noqa: E402
from submission import Reply  # noqa: E402

DAY = 86400
T0 = 1_792_000_000.0               # a moment in 2026
ENV = {'IP_SALT': 'salt', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}
PASSWORD = 'correct horse battery'
ADMIN_ENV = {'SHELF_REPO': 'o/r', 'ADMIN_PASSWORD': PASSWORD, 'ADMIN_GITHUB_TOKEN': 'ghp_x', 'IP_SALT': 'salt'}


def say(msg='网页打不开', **kw):
    return {'msg': msg, 'kind': 'bug', 'where': 'guide', 'lang': 'zh', 'ver': '2026-10-22', **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.now = T0
        self.store = sa.MemoryStore(clock=lambda: self.now)

    def send(self, payload=None, ip='1.2.3.4', now=None, env=ENV):
        self.now = T0 if now is None else now
        return fb.submit(self.store, payload if payload is not None else say(), ip, env, now=self.now)

    def items(self, now=None, **kw):
        self.now = T0 if now is None else now
        return fb.list_items(self.store, now=self.now, **kw)


class TestKeeping(Base):
    def test_what_a_reader_writes_is_kept_with_the_day_and_nothing_that_says_who(self):
        self.assertEqual(self.send(say('第一条', contact='me@example.org')), {'ok': True})
        r = self.items()
        self.assertEqual((r['total'], r['next']), (1, None))
        e = r['items'][0]
        self.assertEqual(e, {'id': 1, 'd': e['d'], 'k': 'bug', 'm': '第一条', 'c': 'me@example.org', 'w': 'guide', 'l': 'zh', 'v': '2026-10-22'})
        self.assertRegex(e['d'], r'^\d{8}$')
        stored = json.dumps([v for k, v in self.store.d.items() if isinstance(v, dict)], ensure_ascii=False)
        self.assertNotIn('1.2.3.4', stored)                                                          # no address anywhere in the entry
        self.assertEqual(sorted(e), ['c', 'd', 'id', 'k', 'l', 'm', 'v', 'w'])                       # no browser, no time of day

    def test_the_newest_come_first_and_a_page_says_where_the_next_one_begins(self):
        for i in range(7):
            self.send(say('words %d' % i), ip='10.0.0.%d' % i)
        a = self.items(limit=3)
        self.assertEqual([e['m'] for e in a['items']], ['words 6', 'words 5', 'words 4'])
        self.assertEqual(a['next'], 5)
        b = self.items(before=a['next'], limit=3)
        self.assertEqual([e['m'] for e in b['items']], ['words 3', 'words 2', 'words 1'])
        c = self.items(before=b['next'], limit=3)
        self.assertEqual(([e['m'] for e in c['items']], c['next']), (['words 0'], None))
        self.assertEqual(self.items(before=1)['items'], [])
        for bad in ('x', [], {}):
            with self.assertRaises(Reply):
                self.items(before=bad)

    def test_an_empty_box_lists_nothing(self):
        self.assertEqual(self.items(), {'total': 0, 'items': [], 'next': None})

    def test_only_what_is_asked_for_is_accepted(self):
        bad = [None, 'x', [], {}, say(''), say('   '), say(None), say(5), say('x' * 1001), say(kind='shout'), say(kind=7), say(contact='c' * 81), say(**{'where': 'a b'}),
               say(**{'where': 'x' * 25}), say(**{'where': '<script>'}), say(ver='v 1'), say(ver='1' * 21), {'kind': 'bug'}]
        for p in bad:
            with self.assertRaises(Reply) as cm:
                fb.submit(self.store, p, '1.2.3.4', ENV, now=T0)                                      # (not self.send: that one puts a good message in place of None)
            self.assertEqual(cm.exception.status, 400, p)
        self.assertEqual(self.items()['total'], 0)                                                   # none of them used up a try or left a trace

    def test_a_missing_kind_is_other_and_a_language_that_is_not_known_is_zh_and_words_are_cleaned(self):
        self.send({'msg': '  a\x00b\x07c\r\nd  '})
        e = self.items()['items'][0]
        self.assertEqual((e['k'], e['l'], e['m'], e['c'], e['w'], e['v']), ('other', 'zh', 'abc\nd', '', '', ''))
        self.send(say('English', lang='en'), ip='2.2.2.2')
        self.send(say('French', lang='fr'), ip='3.3.3.3')
        got = {e['m']: e['l'] for e in self.items()['items']}
        self.assertEqual((got['English'], got['French']), ('en', 'zh'))
        self.send(say('x' * 1000), ip='4.4.4.4')                                                     # exactly the limit is fine
        self.assertEqual(len(self.items()['items'][0]['m']), 1000)
        self.send(say('contact that is no text', contact=5), ip='5.5.5.5')                           # (a contact that is not text is no contact)
        self.assertEqual(self.items()['items'][0]['c'], '')

    def test_the_words_are_kept_as_written_and_never_taken_for_markup(self):
        self.send(say('<b>hi</b> & "quotes" ‮'))
        self.assertEqual(self.items()['items'][0]['m'], '<b>hi</b> & "quotes" ‮')               # (stored as text: the admin page shows it as text)


class TestLimits(Base):
    def test_one_reader_may_send_only_so_many_a_day(self):
        for i in range(3):
            self.send(say('w%d' % i))
        with self.assertRaises(Reply) as cm:
            self.send(say('w3'))
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(self.items()['total'], 3)
        self.assertEqual(self.send(say('w3'), ip='9.9.9.9'), {'ok': True})                          # somebody else
        self.assertEqual(self.send(say('w4'), now=T0 + DAY), {'ok': True})                          # tomorrow
        self.assertEqual(self.items(now=T0 + DAY)['total'], 5)

    def test_the_number_a_day_can_be_set_and_a_bad_value_gives_a_sane_one(self):
        env = {**ENV, 'FEEDBACK_PER_DAY': '1'}
        self.send(say('a'), env=env)
        with self.assertRaises(Reply):
            self.send(say('b'), env=env)
        for n, v in enumerate(('x', '0', '-2', '', '9999')):
            self.send(say('z'), ip='5.5.5.%d' % n, env={**ENV, 'FEEDBACK_PER_DAY': v}, now=T0 + (n + 2) * DAY)

    def test_the_same_words_twice_in_a_day_are_kept_once_and_the_reader_is_not_told(self):
        self.assertEqual(self.send(say('same')), {'ok': True})
        self.assertEqual(self.send(say('same'), ip='7.7.7.7'), {'ok': True})
        self.assertEqual(self.items()['total'], 1)
        self.send(say('same'), now=T0 + DAY)                                                          # tomorrow it counts again
        self.assertEqual(self.items(now=T0 + DAY)['total'], 2)

    def test_a_honeypot_that_is_filled_in_is_told_all_is_well_and_nothing_is_kept_or_counted(self):
        for n in range(6):
            self.assertEqual(self.send(say('spam %d' % n, website='http://spam.example')), {'ok': True})
        self.assertEqual(self.items()['total'], 0)
        for n in range(3):                                                                           # and the sender has all their tries left
            self.send(say('real %d' % n))
        self.assertEqual(self.items()['total'], 3)

    def test_the_counters_are_forgotten_after_two_days(self):
        self.send(say('x'))
        self.now = T0 + 3 * DAY
        self.assertEqual([k for k in list(self.store.d) if k.startswith(('fb:q:', 'fb:dup:')) and self.store._live(k)], [])


class TestKeeping2(Base):
    def test_only_the_newest_are_kept(self):
        env = {**ENV, 'FEEDBACK_KEEP': '50'}
        for i in range(57):
            self.send(say('n%d' % i), ip='10.1.%d.%d' % (i // 200, i % 200), env=env)
        r = fb.list_items(self.store, limit=100, now=T0, env=env)
        self.assertEqual((r['total'], r['items'][0]['m'], r['items'][-1]['m']), (50, 'n56', 'n7'))     # the seven oldest are gone

    def test_nothing_is_kept_past_180_days(self):
        self.send(say('old'))
        self.send(say('middle'), ip='2.2.2.2', now=T0 + 100 * DAY)
        self.send(say('new'), ip='3.3.3.3', now=T0 + 179 * DAY)
        r = self.items(now=T0 + 181 * DAY)
        self.assertEqual([e['m'] for e in r['items']], ['new', 'middle'])                            # the first one is 181 days old
        r = self.items(now=T0 + 290 * DAY)
        self.assertEqual([e['m'] for e in r['items']], ['new'])
        self.assertEqual(r['total'], 1)
        self.send(say('later'), ip='4.4.4.4', now=T0 + 400 * DAY)
        self.assertEqual([e['m'] for e in self.items(now=T0 + 400 * DAY)['items']], ['later'])

    def test_delete_takes_those_ids_and_only_those(self):
        for i in range(5):
            self.send(say('d%d' % i), ip='6.6.6.%d' % i)
        self.assertEqual(fb.delete_items(self.store, [2, 4, 4, 99]), 2)
        self.assertEqual(sorted(e['id'] for e in self.items()['items']), [1, 3, 5])
        self.assertEqual(self.items()['total'], 3)
        for bad in (None, [], 'x', ['1'], [1.5], [True], [0], [-1], list(range(1, 52)), [10 ** 10], {}):
            with self.assertRaises(Reply) as cm:
                fb.delete_items(self.store, bad)
            self.assertEqual(cm.exception.status, 400, bad)
        self.assertEqual(self.items()['total'], 3)

    def test_deleted_ids_in_the_middle_do_not_break_a_page(self):
        for i in range(8):
            self.send(say('p%d' % i), ip='8.8.8.%d' % i)
        fb.delete_items(self.store, [3, 4, 5, 6])
        a = self.items(limit=2)
        self.assertEqual([e['id'] for e in a['items']], [8, 7])
        b = self.items(before=a['next'], limit=2)
        self.assertEqual([e['id'] for e in b['items']], [2, 1])
        self.assertIsNone(b['next'])


class FakeHandler:
    def __init__(self, method, body=None, origin='', ip='1.2.3.4', length=None):
        raw = json.dumps(body).encode('utf-8') if body is not None and not isinstance(body, bytes) else (body or b'')
        self.headers = {'Origin': origin, 'Content-Length': str(len(raw) if length is None else length), 'X-Forwarded-For': ip}
        self.headers = {k: v for k, v in self.headers.items() if v != ''}

        class R:
            def read(_, n=-1):
                return raw[:n] if n >= 0 else raw
        self.rfile = R()
        self.out = {'status': None, 'headers': {}, 'body': b''}
        self.client_address = ('127.0.0.1', 1)

        class W:
            def write(_, b):
                self.out['body'] += b
        self.wfile = W()

    def send_response(self, s):
        self.out['status'] = s

    def send_header(self, k, v):
        self.out['headers'][k] = v

    def end_headers(self):
        pass


class TestHttp(Base):
    def go(self, method, body=None, env=ENV, store='mine', **kw):
        h = FakeHandler(method, body, **kw)
        fb.respond(h, env, method, self.store if store == 'mine' else store)
        o = h.out
        return o['status'], (json.loads(o['body']) if o['body'] else None), o['headers']

    def test_post_keeps_it_and_the_answer_is_never_cached(self):
        s, b, h = self.go('POST', say('via http'), origin='https://books.zhitree.cn')
        self.assertEqual((s, b), (200, {'ok': True}))
        self.assertEqual(h['Cache-Control'], 'no-store')
        self.assertEqual(h['Access-Control-Allow-Origin'], 'https://books.zhitree.cn')
        self.assertEqual(self.items()['items'][0]['m'], 'via http')

    def test_a_wrong_request_is_a_400_with_both_languages_and_too_much_is_a_413(self):
        s, b, _ = self.go('POST', {'msg': ''})
        self.assertEqual((s, b['error']), (400, 'request'))
        self.assertTrue(b['message'] and b['message_en'])
        s, b, _ = self.go('POST', b'not json')
        self.assertEqual(s, 400)
        s, b, _ = self.go('POST', say('x' * 7000))
        self.assertEqual((s, b['error']), (413, 'size'))
        self.assertEqual(self.go('POST', say('a'), length=-1)[0], 413)

    def test_the_fourth_a_day_is_a_429(self):
        for i in range(3):
            self.assertEqual(self.go('POST', say('t%d' % i))[0], 200)
        s, b, _ = self.go('POST', say('t3'))
        self.assertEqual((s, b['error']), (429, 'rate'))

    def test_get_says_whether_it_is_set_up_and_a_page_of_another_site_is_refused(self):
        s, b, h = self.go('GET', origin='https://books.zhitree.cn')
        self.assertEqual((s, b['configured'], b['service']), (200, True, 'shelf-feedback'))
        self.assertEqual(h['Cache-Control'], 'no-store')
        s, b, _ = self.go('GET', store=None, env={**ENV})
        self.assertEqual((s, b['configured']), (200, False))
        for m, body in (('POST', say()), ('GET', None)):
            s, b, h = self.go(m, body, origin='https://evil.example')
            self.assertEqual((s, b['error']), (403, 'origin'))
            self.assertNotIn('Access-Control-Allow-Origin', h)
        self.assertEqual(self.items()['total'], 0)
        self.assertNotIn('Access-Control-Allow-Origin', self.go('OPTIONS', origin='https://evil.example')[2])
        self.assertIn('Access-Control-Allow-Methods', self.go('OPTIONS', origin='https://books.zhitree.cn')[2])

    def test_not_set_up_is_a_503_for_a_post(self):
        s, b, _ = self.go('POST', say(), store=None, env={**ENV})
        self.assertEqual((s, b['error']), (503, 'not_configured'))

    def test_a_store_that_cannot_be_reached_is_a_503_and_not_a_crash(self):
        class Broken:
            def run(self, commands):
                raise sa.StoreError('down')
        s, b, _ = self.go('POST', say(), store=Broken())
        self.assertEqual((s, b['error']), (503, 'storage'))


class TestAdmin(Base):
    def session(self):
        st, body = adm.handle({'action': 'login', 'password': PASSWORD}, '', '1.1.1.1', ADMIN_ENV, now=1_000_000)
        self.assertEqual(st, 200)
        return body['session']

    def ask(self, payload, session, store='mine'):
        return adm.handle(payload, session, '1.1.1.1', ADMIN_ENV, now=1_000_100, store=self.store if store == 'mine' else store)

    def test_the_admin_lists_and_deletes_behind_the_session_and_not_without_it(self):
        for i in range(4):
            self.send(say('a%d' % i), ip='3.3.3.%d' % i)
        for who in ('', 'v1.1.' + '0' * 64, self.session()[:-1] + '0'):
            for act in ({'action': 'feedback_list'}, {'action': 'feedback_delete', 'ids': [1]}):
                st, body = self.ask(act, who)
                self.assertEqual((st, body['error']), (401, 'session'), (who, act))
        self.assertEqual(self.items()['total'], 4)                                                    # nothing was listed or deleted without the session
        s = self.session()
        st, body = self.ask({'action': 'feedback_list', 'limit': 2}, s)
        self.assertEqual((st, body['ok'], body['total'], [e['m'] for e in body['items']], body['next']), (200, True, 4, ['a3', 'a2'], 3))
        st, body = self.ask({'action': 'feedback_list', 'before': 3}, s)
        self.assertEqual([e['m'] for e in body['items']], ['a1', 'a0'])
        st, body = self.ask({'action': 'feedback_delete', 'ids': [2, 3]}, s)
        self.assertEqual((st, body), (200, {'ok': True, 'deleted': 2}))
        self.assertEqual(sorted(e['id'] for e in self.items()['items']), [1, 4])

    def test_a_wrong_request_is_a_400_and_no_store_is_a_503(self):
        s = self.session()
        self.assertEqual(self.ask({'action': 'feedback_delete', 'ids': 'all'}, s)[0], 400)
        self.assertEqual(self.ask({'action': 'feedback_list', 'before': 'x'}, s)[0], 400)
        st, body = self.ask({'action': 'feedback_list'}, s, store=None)
        self.assertEqual((st, body['error']), (503, 'not_configured'))

    def test_the_other_actions_are_as_they_were(self):
        self.assertEqual(adm.handle({'action': 'nothing'}, self.session(), '1.1.1.1', ADMIN_ENV, now=1_000_100)[0], 400)


if __name__ == '__main__':
    unittest.main()
