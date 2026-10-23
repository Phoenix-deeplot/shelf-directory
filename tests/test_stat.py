"""The anonymous counters of the public directory (statapi.py): once a day per reader per entry, a cap per reader, the 7-day heat, nothing that identifies anybody, the HTTP side.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import statapi as st  # noqa: E402
import syncapi as sa  # noqa: E402
from submission import Reply  # noqa: E402

DAY = 86400
T0 = 1_792_000_000.0               # a moment in 2026
ENV = {'IP_SALT': 'salt', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}


def ev(t='open', i='abc123', k='item'):
    return {'t': t, 'k': k, 'id': i}


class Base(unittest.TestCase):
    def setUp(self):
        self.store = sa.MemoryStore(clock=lambda: self.now)
        self.now = T0

    def count(self, events, ip='1.2.3.4', now=None, env=ENV):
        self.now = T0 if now is None else now
        return st.count(self.store, {'events': events}, ip, env, now=self.now)

    def read(self, now=None):
        self.now = T0 if now is None else now
        return st.read(self.store, now=self.now)


class TestCounting(Base):
    def test_events_are_counted_and_the_same_one_twice_in_a_day_is_not(self):
        self.assertEqual(self.count([ev(), ev('save'), ev('sub', k='shelf', i='my-shelf')]), {'ok': True, 'counted': 3})
        self.assertEqual(self.count([ev(), ev('save')]), {'ok': True, 'counted': 0})               # the same reader, the same day
        self.assertEqual(self.count([ev(), ev('save')], ip='5.6.7.8')['counted'], 2)               # another reader
        r = self.read()
        self.assertEqual(r['items']['abc123'], {'o': 2, 's': 2, 'u': 0, 'h': 2 + 4 * 2})
        self.assertEqual(r['shelves']['my-shelf'], {'o': 0, 's': 0, 'u': 1, 'h': 6})

    def test_the_next_day_counts_again(self):
        self.count([ev()])
        self.assertEqual(self.count([ev()], now=T0 + DAY)['counted'], 1)
        self.assertEqual(self.read(now=T0 + DAY)['items']['abc123']['o'], 2)

    def test_one_batch_with_the_same_event_twice_counts_it_once(self):
        self.assertEqual(self.count([ev(), ev(), ev()])['counted'], 1)

    def test_items_and_shelves_do_not_mix_even_with_the_same_id(self):
        self.count([ev(i='same'), ev(i='same', k='shelf')])
        r = self.read()
        self.assertEqual((r['items']['same']['o'], r['shelves']['same']['o']), (1, 1))

    def test_only_what_is_asked_for_is_accepted(self):
        bad = [[], None, 'x', {}, [ev(t='click')], [ev(k='user')], [ev(i='')], [ev(i='a b')], [ev(i='../x')], [ev(i='x' * 65)], [ev(i=5)], [{'t': 'open'}], ['open'],
               [ev() for _ in range(st.PER_REQUEST + 1)], [ev(), {'t': 'bogus', 'k': 'item', 'id': 'x'}]]
        for events in bad:
            with self.assertRaises(Reply) as cm:
                st.count(self.store, {'events': events} if not isinstance(events, dict) and events is not None else events, '1.1.1.1', ENV, now=T0)
            self.assertEqual(cm.exception.status, 400, events)
        self.assertEqual(self.read()['items'], {})                                                # nothing of it was counted, not even the valid half of a mixed batch
        self.assertEqual(self.count([ev(i='a.b_c-d')])['counted'], 1)

    def test_one_reader_may_send_only_so_many_in_a_day(self):
        env = {**ENV, 'STAT_DAILY_EVENTS': '5'}
        self.count([ev(i='e%d' % i) for i in range(5)], env=env)
        with self.assertRaises(Reply) as cm:
            self.count([ev(i='e5')], env=env)
        self.assertEqual(cm.exception.status, 429)
        self.assertNotIn('e5', self.read()['items'])
        self.assertEqual(self.count([ev(i='e5')], ip='9.9.9.9', env=env)['counted'], 1)           # somebody else
        self.assertEqual(self.count([ev(i='e5')], now=T0 + DAY, env=env)['counted'], 1)           # tomorrow
        for n, bad in enumerate(('x', '0', '-3', '', '99999')):                                    # a value that is not a number, zero, negative, empty, huge: no crash, a sane limit
            self.assertEqual(st.count(self.store, {'events': [ev(i='z')]}, '3.3.3.%d' % n, {**ENV, 'STAT_DAILY_EVENTS': bad}, now=T0 + 5 * DAY)['counted'], 1)


class TestHeat(Base):
    def test_heat_is_the_last_seven_days_and_weighs_a_subscription_over_a_save_over_an_open(self):
        self.count([ev('open', 'old')], ip='1.1.1.1', now=T0 - 9 * DAY)                           # outside the window: counted for good, but cold
        self.count([ev('open', 'old')], ip='2.2.2.2', now=T0 - 9 * DAY)
        self.count([ev('open', 'hot'), ev('save', 'hot'), ev('sub', 'hot')], ip='1.1.1.1', now=T0 - 2 * DAY)
        self.count([ev('open', 'warm')], ip='1.1.1.1', now=T0 - 6 * DAY)
        self.count([ev('open', 'warm')], ip='2.2.2.2', now=T0 - 1 * DAY)
        r = self.read()['items']
        self.assertEqual(r['hot'], {'o': 1, 's': 1, 'u': 1, 'h': 1 + 4 + 6})
        self.assertEqual(r['warm']['h'], 2)
        self.assertEqual((r['old']['h'], r['old']['o']), (0, 2))
        self.assertEqual(list(r), ['hot', 'warm', 'old'])                                          # hottest first, then by all-time numbers

    def test_only_the_busiest_are_told(self):
        for i in range(st.TOP + 25):
            self.count([ev(i='e%03d' % i)], ip='10.0.%d.%d' % (i // 250, i % 250))
        self.count([ev(i='e000', t='save')], ip='99.9.9.9')
        r = self.read()['items']
        self.assertEqual(len(r), st.TOP)
        self.assertEqual(next(iter(r)), 'e000')

    def test_the_counters_are_forgotten_after_forty_days_but_the_totals_stay(self):
        self.count([ev(i='long')])
        self.now = T0 + (st.KEEP_DAYS + 2) * DAY
        r = st.read(self.store, now=self.now)['items']['long']
        self.assertEqual((r['o'], r['h']), (1, 0))


class TestNothingIdentifiesAnybody(Base):
    def test_no_address_and_no_text_of_the_reader_is_kept(self):
        self.count([ev(), ev('save')], ip='203.0.113.77')
        everything = json.dumps({k: v for k, v in self.store.d.items()}, ensure_ascii=False)
        for secret in ('203.0.113.77', '203.0', 'Mozilla'):
            self.assertNotIn(secret, everything)
        for k in self.store.d:
            self.assertTrue(k.startswith(('stat:q:', 'stat:s:', 'stat:d:', 'stat:all:')), k)

    def test_the_once_a_day_keys_change_every_day_and_expire(self):
        self.count([ev()])
        today = {k for k in self.store.d if k.startswith('stat:s:')}
        self.count([ev()], now=T0 + DAY)
        both = {k for k in self.store.d if k.startswith('stat:s:')}
        self.assertEqual(len(both - today), 1)
        self.assertTrue(today.isdisjoint(both - today))                                             # tomorrow's key is another one: nobody is followed from one day to the next
        self.now = T0 + 3 * DAY
        self.assertEqual([k for k in list(self.store.d) if k.startswith('stat:s:') and self.store._live(k)], [])                       # forgotten after two days


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
        self.method = method

    def send_response(self, s):
        self.out['status'] = s

    def send_header(self, k, v):
        self.out['headers'][k] = v

    def end_headers(self):
        pass


class TestHttp(Base):
    def go(self, method, body=None, env=ENV, store='mine', **kw):
        h = FakeHandler(method, body, **kw)
        st.respond(h, env, method, self.store if store == 'mine' else store)
        o = h.out
        return o['status'], (json.loads(o['body']) if o['body'] else None), o['headers']

    def test_post_counts_and_the_answer_is_never_cached(self):
        s, b, h = self.go('POST', {'events': [ev()]}, origin='https://books.zhitree.cn')
        self.assertEqual((s, b), (200, {'ok': True, 'counted': 1}))
        self.assertEqual(h['Cache-Control'], 'no-store')
        self.assertEqual(h['Access-Control-Allow-Origin'], 'https://books.zhitree.cn')
        self.assertEqual(h['Vary'], 'Origin')

    def test_get_is_cacheable_and_carries_the_heat(self):
        self.go('POST', {'events': [ev('open', 'a1'), ev('save', 'a1')]})
        s, b, h = self.go('GET', origin='https://books.zhitree.cn')
        self.assertEqual((s, b['configured'], b['window'], b['items']['a1']['h']), (200, True, 7, 5))
        self.assertIn('s-maxage=600', h['Cache-Control'])
        self.assertEqual(h['Vary'], 'Origin')

    def test_a_page_of_another_site_is_refused(self):
        for m, body in (('POST', {'events': [ev()]}), ('GET', None)):
            s, b, h = self.go(m, body, origin='https://evil.example')
            self.assertEqual((s, b['error']), (403, 'origin'))
            self.assertNotIn('Access-Control-Allow-Origin', h)
        self.assertEqual(self.read()['items'], {})
        self.assertEqual(self.go('OPTIONS', origin='https://evil.example')[0], 204)               # (a preflight gets no permission)
        self.assertNotIn('Access-Control-Allow-Origin', self.go('OPTIONS', origin='https://evil.example')[2])
        self.assertIn('Access-Control-Allow-Methods', self.go('OPTIONS', origin='https://books.zhitree.cn')[2])

    def test_not_set_up_means_no_heat_and_no_counting_and_no_error_for_the_page(self):
        s, b, h = self.go('GET', store=None, env={**ENV})
        self.assertEqual((s, b['configured']), (200, False))
        self.assertEqual(h['Cache-Control'], 'no-store')
        s, b, h = self.go('POST', {'events': [ev()]}, store=None, env={**ENV})
        self.assertEqual((s, b['error']), (503, 'not_configured'))

    def test_bad_requests(self):
        self.assertEqual(self.go('POST', b'not json')[0], 400)
        self.assertEqual(self.go('POST', {'events': 'x'})[0], 400)
        self.assertEqual(self.go('POST', b'x' * 10, length=st.BODY_LIMIT + 1)[0], 413)
        self.assertEqual(self.go('POST', b'x', length=-1)[0], 413)

    def test_a_broken_store_is_a_503_not_a_crash(self):
        class Broken:
            def run(self, commands):
                raise sa.StoreError('down')
        self.assertEqual(self.go('GET', store=Broken())[0], 503)
        self.assertEqual(self.go('POST', {'events': [ev()]}, store=Broken())[0], 503)

    def test_the_reader_is_taken_from_the_forwarded_address_and_counted_once_a_day(self):
        self.assertEqual(self.go('POST', {'events': [ev()]}, ip='8.8.8.8')[1]['counted'], 1)
        self.assertEqual(self.go('POST', {'events': [ev()]}, ip='8.8.8.8')[1]['counted'], 0)
        self.assertEqual(self.go('POST', {'events': [ev()]}, ip='8.8.4.4')[1]['counted'], 1)


class TestOverUpstash(unittest.TestCase):
    """HGETALL over the wire is a flat list: both the memory store and Upstash answer it so."""

    def test_flat_lists_become_numbers(self):
        self.assertEqual(st._map(['a', '3', 'b', '10']), {'a': 3, 'b': 10})
        self.assertEqual(st._map(None), {})
        self.assertEqual(st._map([]), {})


if __name__ == '__main__':
    unittest.main()
