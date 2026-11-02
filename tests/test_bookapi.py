"""The shared copy of the chapters of the picked books (bookapi.py): what may be kept, the layers (Redis, PostgreSQL) and how a chapter moves between them, the locks and limits, the HTTP side.
The original pages are fake here (a function that answers the article); the PostgreSQL layer is a fake with the same two methods, and its SQL is checked with a fake cursor; no internet, no database.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import bookapi  # noqa: E402
import readable  # noqa: E402
import syncapi as sa  # noqa: E402
from submission import Reply  # noqa: E402
from test_stat import FakeHandler  # noqa: E402

T0 = 1_792_000_000.0
ENV = {'IP_SALT': 'salt', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}


def entry(id, **kw):
    b = {'id': id, 'title': id.title(), 'author': 'A. Writer', 'lang': 'en', 'kind': 'novel', 'tags': ['小说'], 'summary': 'A made-up book.', 'license': 'public-domain', 'source': 'en.wikisource.org',
         'read': 'https://en.wikisource.org/wiki/' + id, 'ch': {'s': 'en.wikisource.org', 'p': id.title() + '/', 'n': ['Chapter 1', 'Chapter 2', 'Chapter 3']}}
    b.update(kw)
    return b


BOOKS = [
    entry('tale', keep=True),                                                                                        # (kept for good)
    entry('side', license='cc-by-sa'),                                                                              # (shared, but not kept: it goes to the long-term layer when it is read often)
    {**{k: v for k, v in entry('page', license='cc0').items() if k != 'ch'}, 'readable': True},                     # (one page)
    entry('closed', license='cc-by-nc'),                                                                            # (a licence that does not let anybody copy)
    entry('free', license='free-to-read'),
    entry('hidden', share=False, keep=True),                                                                        # (the list says no)
]


class FakeDurable:
    """The two methods of bookapi.Durable, in a dict."""

    def __init__(self):
        self.rows, self.gets, self.puts = {}, 0, []

    def get(self, b, c):
        self.gets += 1
        return self.rows.get((b, c))

    def has(self, b, c):
        return (b, c) in self.rows

    def put(self, b, c, rec, license):
        self.rows[(b, c)] = rec
        self.puts.append((b, c, license))
        return True


class Fetch:
    """The original page: what is asked is counted, what is answered is a small article."""

    def __init__(self, text='<p>It was a bright cold day in April.</p>', raises=None):
        self.calls, self.text, self.raises = [], text, raises

    def __call__(self, url):
        self.calls.append(url)
        if self.raises:
            raise self.raises
        return {'title': 'Title of ' + url.rsplit('/', 1)[-1], 'content': self.text, 'lang': 'en', 'url': url, 'cut': False}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = agg.ROOT
        agg.ROOT = self.tmp
        os.makedirs(os.path.join(self.tmp, 'registry'))
        with open(os.path.join(self.tmp, 'registry', 'books.json'), 'w', encoding='utf-8') as f:
            json.dump(BOOKS, f, ensure_ascii=False)
        bookapi._LOCAL_USED.clear()
        self.now = [T0]
        self.store = sa.MemoryStore(clock=lambda: self.now[0])
        self.durable = FakeDurable()
        self.fetch = Fetch()

    def tearDown(self):
        agg.ROOT = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, b, c=0, ip='1.2.3.4', env=ENV, store='mine', durable='mine', fetch=None, sleep=None, now=None):
        """-> the answer dict (or the Reply that was raised) of one request."""
        hot = bookapi.Hot(self.store if store == 'mine' else store)
        dur = self.durable if durable == 'mine' else durable
        try:
            return bookapi.chapter({'b': [b], 'c': [str(c)]}, ip, env, hot, dur, now=self.now[0] if now is None else now, fetch=fetch or self.fetch, sleep=sleep or (lambda s: None))[0]
        except Reply as r:
            return r


class TestTheList(Base):
    def test_which_books_may_be_kept(self):
        books = bookapi.catalog()
        self.assertEqual(sorted(k for k, b in books.items() if bookapi.shared(b)), ['page', 'side', 'tale'])
        for closed in ('closed', 'free', 'hidden'):                                                                 # (a licence that does not say yes, a list that says no)
            self.assertFalse(bookapi.shared(books[closed]), closed)

    def test_the_address_of_a_chapter_is_made_from_the_list_and_nothing_else(self):
        books = bookapi.catalog()
        self.assertEqual(bookapi.unit(books['tale'], 1), ('https://en.wikisource.org/wiki/Tale/Chapter_2', 'Chapter 2'))     # (a space is written _)
        self.assertEqual(bookapi.unit(books['page'], 0), ('https://en.wikisource.org/wiki/page', ''))
        for c in (3, 99, -1):
            self.assertIsNone(bookapi.unit(books['tale'], c))
        self.assertIsNone(bookapi.unit(books['page'], 1))
        self.assertEqual(bookapi.chapter_url({'s': 'zh.wikisource.org', 'p': '紅樓夢/'}, '第001回'), 'https://zh.wikisource.org/wiki/%E7%B4%85%E6%A8%93%E5%A4%A2/%E7%AC%AC001%E5%9B%9E')

    def test_keep_and_share_must_be_true_or_false(self):
        for flag in ('keep', 'share'):
            it, why = agg.check_book(entry('x', **{flag: 'yes'}))
            self.assertIsNone(it)
            self.assertIn(flag, why)
            self.assertIsNotNone(agg.check_book(entry('x', **{flag: True}))[0])
            self.assertIsNotNone(agg.check_book(entry('x', **{flag: False}))[0])

    def test_a_list_that_changes_is_read_again_and_a_list_that_is_not_there_is_nothing(self):
        self.assertEqual(len(bookapi.catalog()), 6)
        path = agg.books_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(BOOKS[:2], f)
        os.utime(path, (T0 + 100, T0 + 100))
        self.assertEqual(sorted(bookapi.catalog()), ['side', 'tale'])
        os.remove(path)
        self.assertEqual(bookapi.catalog(), {})


class TestTheLayers(Base):
    def test_the_first_reader_makes_the_service_read_the_original_and_everybody_after_gets_the_copy(self):
        a = self.ask('tale', 0)
        self.assertEqual((a['ok'], a['tier'], a['title'], a['license'], a['source'], a['by'], a['v']), (True, 'source', 'Title of Chapter_1', 'public-domain', 'https://en.wikisource.org/wiki/Tale/Chapter_1', 'en.wikisource.org', 1))
        self.assertIn('bright cold day', a['content'])
        b = self.ask('tale', 0, ip='9.9.9.9')
        self.assertEqual((b['tier'], b['content']), ('hot', a['content']))
        self.assertEqual(len(self.fetch.calls), 1)                                                                  # (the original was asked once)

    def test_a_kept_book_goes_to_the_long_term_layer_at_once_the_others_do_not(self):
        self.ask('tale', 0)
        self.ask('side', 0)
        self.assertEqual(self.durable.puts, [('tale', 0, 'public-domain')])

    def test_what_the_hot_layer_forgets_comes_back_from_the_long_term_one_and_is_put_back(self):
        self.ask('tale', 1)
        self.now[0] += (bookapi.HOT_DAYS_KEPT + 1) * 86400                                                           # (Redis forgot it)
        a = self.ask('tale', 1)
        self.assertEqual((a['tier'], len(self.fetch.calls)), ('durable', 1))
        self.assertEqual(self.ask('tale', 1)['tier'], 'hot')                                                         # (and it is in Redis again)

    def test_a_book_that_is_not_kept_is_forgotten_by_the_hot_layer_and_read_again(self):
        self.ask('side', 0)
        self.now[0] += (bookapi.HOT_DAYS + 1) * 86400
        self.assertEqual(self.ask('side', 0)['tier'], 'source')
        self.assertEqual(len(self.fetch.calls), 2)

    def test_a_chapter_that_is_read_often_enough_is_kept_for_good(self):
        self.ask('side', 0)                                                                                          # (the first reading is not counted: it made the copy)
        self.ask('side', 0)
        self.ask('side', 0)
        self.assertEqual(self.durable.puts, [])                                                                      # 2 reads of the copy
        self.ask('side', 0)                                                                                          # the third
        self.assertEqual(self.durable.puts, [('side', 0, 'cc-by-sa')])
        for _ in range(4):
            self.ask('side', 0)
        self.assertEqual(len(self.durable.puts), 1)                                                                  # (once)
        self.ask('side', 1)                                                                                          # (another chapter of it is counted on its own)
        self.assertEqual(len(self.durable.puts), 1)

    def test_the_number_of_reads_is_a_setting(self):
        env = {**ENV, 'BOOK_PROMOTE_READS': '1'}
        self.ask('side', 0, env=env)
        self.ask('side', 0, env=env)
        self.assertEqual(self.durable.puts, [('side', 0, 'cc-by-sa')])

    def test_a_book_that_may_not_be_kept_is_not_read_and_not_kept(self):
        for b in ('closed', 'free', 'hidden'):
            a = self.ask(b, 0)
            self.assertEqual((a['ok'], a['error']), (False, 'nocache'), b)
        self.assertEqual((self.fetch.calls, self.durable.puts, self.store.dbsize() if hasattr(self.store, 'dbsize') else self.store.run([('DBSIZE',)])[0]), ([], [], 0))

    def test_a_book_that_is_one_page_is_chapter_0(self):
        a = self.ask('page', 0)
        self.assertEqual((a['ok'], a['source']), (True, 'https://en.wikisource.org/wiki/page'))
        self.assertEqual(self.ask('page', 1).status, 404)

    def test_what_is_not_asked_right_is_refused_and_nothing_is_read(self):
        for b, c, status, code in (('nobook', 0, 404, 'unknown'), ('tale', 3, 404, 'unknown'), ('tale', 99, 404, 'unknown')):
            r = self.ask(b, c)
            self.assertEqual((r.status, r.code), (status, code), (b, c))
        for q in ({}, {'b': ['Tale'], 'c': ['0']}, {'b': ['../x'], 'c': ['0']}, {'b': ['tale'], 'c': ['-1']}, {'b': ['tale'], 'c': ['x']}, {'b': ['tale'], 'c': ['12345']}, {'b': [''], 'c': ['0']}):
            with self.assertRaises(Reply) as cm:
                bookapi.chapter(q, '1.1.1.1', ENV, bookapi.Hot(self.store), self.durable, fetch=self.fetch)
            self.assertEqual(cm.exception.status, 400, q)
        self.assertEqual(self.fetch.calls, [])

    def test_without_redis_it_still_reads_and_without_postgresql_there_is_the_one_layer(self):
        for kw in ({'store': None}, {'durable': None}, {'store': None, 'durable': None}):
            self.fetch.calls.clear()
            a = self.ask('tale', 0, **kw)
            self.assertEqual((a['ok'], a['tier']), (True, 'source'), kw)
        a = self.ask('tale', 0, store=None)                                                                          # (no Redis: PostgreSQL answers)
        self.assertEqual(a['tier'], 'durable')

    def test_a_broken_layer_is_a_miss_and_never_an_error_for_the_reader(self):
        class Broken:
            def run(self, commands):
                raise sa.StoreError('down')

        class BrokenDurable:
            def get(self, b, c):
                return None

            def put(self, b, c, rec, license):
                return False
        a = self.ask('tale', 0, store=Broken(), durable=BrokenDurable())
        self.assertEqual((a['ok'], a['tier']), (True, 'source'))
        d = bookapi.Durable(lambda: (_ for _ in ()).throw(OSError('no connection')))
        self.assertEqual((d.get('tale', 0), d.put('tale', 0, {'t': '', 'h': 'x', 'l': '', 'u': 'u', 'at': 1.0}, 'cc0')), (None, False))

    def test_a_page_that_cannot_be_read_is_told_and_nothing_is_kept_and_the_lock_is_let_go(self):
        r = self.ask('tale', 0, fetch=Fetch(raises=readable.Unreadable('gone')))
        self.assertEqual((r.status, r.code), (422, 'unreadable'))
        self.assertEqual(self.durable.puts, [])
        self.assertEqual(self.store.run([('GET', 'bk:lock:bk:1:tale:0')]), [None])
        self.assertTrue(self.ask('tale', 0)['ok'])                                                                   # (the next try reads it)
        r = self.ask('tale', 1, fetch=Fetch(text=''))
        self.assertEqual((r.status, r.code), (422, 'unreadable'))                                                    # (a page with no text is no chapter)

    def test_a_chapter_that_is_being_read_is_waited_for_not_read_twice(self):
        self.store.run([('SET', 'bk:lock:bk:1:tale:0', '1', 'EX', 30)])                                              # somebody else is reading it
        rec = {'t': 'Theirs', 'h': '<p>their copy</p>', 'l': 'en', 'u': 'u', 'at': T0, 'cut': False, 'v': 1}

        def sleep(s):                                                                                                # ... and is done by the time we look again
            self.store.run([('SET', 'bk:1:tale:0', json.dumps(rec), 'EX', 99)])
        a = self.ask('tale', 0, sleep=sleep)
        self.assertEqual((a['title'], a['tier'], self.fetch.calls), ('Theirs', 'hot', []))
        self.store.run([('DEL', 'bk:1:tale:0')])
        r = self.ask('tale', 0)                                                                                      # nobody finishes: told to try again, and nothing was read
        self.assertEqual((r.status, r.code, self.fetch.calls), (503, 'busy', []))

    def test_how_often_a_reader_makes_the_service_read_an_original_is_limited_and_a_copy_costs_nothing(self):
        env = {**ENV, 'BOOK_SOURCE_PER_10MIN': '2'}
        self.assertTrue(self.ask('tale', 0, env=env)['ok'])
        self.assertTrue(self.ask('tale', 1, env=env)['ok'])
        r = self.ask('tale', 2, env=env)
        self.assertEqual((r.status, r.code), (429, 'limit'))
        self.assertTrue(self.ask('tale', 0, env=env)['ok'])                                                          # a copy that is there: no limit
        self.assertTrue(self.ask('tale', 2, env=env, ip='8.8.8.8')['ok'])                                            # another reader
        self.now[0] += 700
        self.assertTrue(self.ask('tale', 2, env=env)['ok'])                                                          # ten minutes later

    def test_the_same_limit_holds_without_redis_in_the_memory_of_the_instance(self):
        env = {**ENV, 'BOOK_SOURCE_PER_10MIN': '1'}
        self.assertTrue(self.ask('tale', 0, env=env, store=None)['ok'])
        self.assertEqual(self.ask('tale', 1, env=env, store=None).code, 'limit')

    def test_a_very_long_chapter_is_cut_at_the_guard_of_the_store(self):
        a = self.ask('tale', 0, fetch=Fetch(text='<p>' + 'x' * (bookapi.MAX_CONTENT + 5000) + '</p>'))
        self.assertEqual(len(a['content']), bookapi.MAX_CONTENT)


class TestPostgres(unittest.TestCase):
    """The SQL of the long-term layer, with a cursor that only writes down what it is told."""

    class Cur:
        def __init__(self, log, row=None):
            self.log, self.row = log, row

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            self.log.append((' '.join(sql.split()), params))

        def fetchone(self):
            return self.row

    class Cx:
        def __init__(self, cur):
            self.cur = cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return self.cur

    def test_get_reads_one_row_by_book_chapter_and_parser_and_counts_the_read(self):
        log = []
        cur = self.Cur(log, ('Title', '<p>x</p>', 'en', 'https://example.org/c', 123.0, True))
        d = bookapi.Durable(lambda: self.Cx(cur))
        rec = d.get('tale', 2)
        self.assertEqual(rec, {'t': 'Title', 'h': '<p>x</p>', 'l': 'en', 'u': 'https://example.org/c', 'at': 123.0, 'cut': True, 'v': 1})
        self.assertIn('FROM library.book_chapters WHERE book_id=%s AND ch=%s AND parser=%s', log[0][0])
        self.assertEqual(log[0][1], ('tale', 2, bookapi.PARSER))
        self.assertIn('UPDATE library.book_chapters SET reads=reads+1', log[1][0])
        self.assertIsNone(bookapi.Durable(lambda: self.Cx(self.Cur([], None))).get('tale', 2))

    def test_put_inserts_or_updates_with_the_values_as_parameters_never_in_the_text(self):
        log = []
        d = bookapi.Durable(lambda: self.Cx(self.Cur(log)))
        rec = {'t': "It's", 'h': '<p>é</p>', 'l': 'en', 'u': 'https://example.org/c', 'at': 55.0, 'cut': False}
        self.assertTrue(d.put('tale', 0, rec, 'public-domain'))
        sql, params = log[0]
        self.assertIn('INSERT INTO library.book_chapters', sql)
        self.assertIn('ON CONFLICT (book_id, ch, parser) DO UPDATE', sql)
        self.assertNotIn("It's", sql)
        self.assertEqual((params[0], params[1], params[2], params[3], params[6]), ('tale', 0, bookapi.PARSER, "It's", 'public-domain'))
        self.assertEqual(params[8], len('<p>é</p>'.encode('utf-8')))                                                 # (bytes, not characters)

    def test_no_database_set_up_is_no_layer(self):
        self.assertIsNone(bookapi.durable_from_env({}))
        self.assertIsNone(bookapi.durable_from_env({'OPS_DATABASE_URL': ''}))


class TestHttp(Base):
    def go(self, path, origin='', ip='1.2.3.4', env=ENV, method='GET', **kw):
        h = FakeHandler(method, origin=origin, ip=ip)
        h.path = path
        kw.setdefault('store', self.store)
        kw.setdefault('durable', self.durable)
        kw.setdefault('fetch', self.fetch)
        bookapi.respond(h, env, method, **kw)
        body = json.loads(h.out['body']) if h.out['body'] else None
        return h.out['status'], h.out['headers'], body

    def test_a_chapter_is_answered_with_a_header_that_lets_the_cdn_keep_it(self):
        status, headers, body = self.go('/api/book?b=tale&c=0', origin='https://books.zhitree.cn')
        self.assertEqual((status, body['ok'], body['tier']), (200, True, 'source'))
        self.assertEqual(headers['Cache-Control'], bookapi.CDN)
        self.assertIn('s-maxage=3600', headers['Cache-Control'])
        self.assertEqual((headers['Vary'], headers['Access-Control-Allow-Origin']), ('Origin', 'https://books.zhitree.cn'))

    def test_what_is_not_an_answer_to_keep_is_never_kept_by_the_cdn(self):
        for path in ('/api/book?b=nobook&c=0', '/api/book?b=tale&c=9', '/api/book?b=%2e%2e&c=0'):
            status, headers, body = self.go(path)
            self.assertEqual((status in (400, 404), body['ok'], headers['Cache-Control']), (True, False, 'no-store'), path)
        status, headers, body = self.go('/api/book?b=tale&c=0', fetch=Fetch(raises=readable.Unreadable('login')))
        self.assertEqual((status, headers['Cache-Control']), (422, 'no-store'))

    def test_a_book_that_is_not_kept_says_so_and_that_answer_may_be_kept_by_the_cdn(self):
        status, headers, body = self.go('/api/book?b=closed&c=0')
        self.assertEqual((status, body['error'], headers['Cache-Control']), (200, 'nocache', 'public, max-age=300, s-maxage=3600'))

    def test_a_page_of_another_site_is_refused_and_the_address_is_not_taken_from_the_request(self):
        status, headers, body = self.go('/api/book?b=tale&c=0', origin='https://evil.example')
        self.assertEqual((status, body['error'], headers['Cache-Control'], 'Access-Control-Allow-Origin' in headers), (403, 'origin', 'no-store', False))
        status, headers, body = self.go('/api/book?b=tale&c=0&url=https://evil.example/x&u=http://127.0.0.1/&s=evil.example')
        self.assertEqual((status, self.fetch.calls), (200, ['https://en.wikisource.org/wiki/Tale/Chapter_1']))

    def test_the_service_says_what_it_has_and_a_preflight_is_answered(self):
        status, headers, body = self.go('/api/book')
        self.assertEqual((status, body), (200, {'ok': True, 'service': 'shelf-book', 'redis': True, 'durable': True, 'books': 6, 'shared': 3}))
        status, headers, body = self.go('/api/book', origin='https://books.zhitree.cn', method='OPTIONS')
        self.assertEqual((status, headers['Access-Control-Allow-Methods']), (204, 'GET, OPTIONS'))
        status, headers, body = self.go('/api/book?b=tale&c=0', method='POST')
        self.assertEqual(status, 405)


class TestTheRealList(unittest.TestCase):
    """registry/books.json as it is in the repository."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'registry', 'books.json'), encoding='utf-8') as f:
            cls.raw = json.load(f)

    def test_every_book_that_can_be_read_in_the_page_is_under_a_licence_that_lets_it_be_kept_and_is_kept_for_good(self):
        readable_books = [b for b in self.raw if b.get('ch') or b.get('readable')]
        self.assertTrue(len(readable_books) >= 30)
        for b in readable_books:
            self.assertIn(b['license'], bookapi.SHARED_LICENSES, b['id'])
            self.assertIs(b.get('keep'), True, b['id'])
            self.assertIsNot(b.get('share'), False, b['id'])

    def test_the_address_of_every_chapter_is_https_on_the_site_of_the_book(self):
        for b in self.raw:
            if b.get('ch'):
                for i in (0, len(b['ch']['n']) - 1):
                    u = bookapi.unit(b, i)[0]
                    self.assertTrue(u.startswith('https://%s/wiki/' % b['ch']['s']), (b['id'], u))
                    self.assertNotIn(' ', u)

SECRET = 'postgres://dbuser:S3cretPassw0rd@db.example.com:5432/postgres?pgbouncer=true&supa=base-pooler.x&sslmode=require'


class TestTheSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p

    def test_an_env_file_is_read_as_the_files_of_vercel_and_dotenv_are_written(self):
        p = self.write('.env', '# a comment\nPOSTGRES_URL="postgres://u:p@h/db"\nexport IP_SALT=\'abc\'\nOPS_X=plain # tail\n\nbad line\n1BAD=x\nEMPTY=\n')
        self.assertEqual(bookapi.read_env_file(p), {'POSTGRES_URL': 'postgres://u:p@h/db', 'IP_SALT': 'abc', 'OPS_X': 'plain', 'EMPTY': ''})
        self.assertEqual(bookapi.read_env_file(os.path.join(self.dir, 'nothing')), {})

    def test_the_shell_wins_over_the_files_and_the_files_are_named_but_never_shown(self):
        self.write('.env.local', 'POSTGRES_URL="%s"\n' % SECRET)
        self.write('.env', 'POSTGRES_URL=from-env\nDATABASE_URL=also\n')
        env, said = bookapi.settings_for_cli([], {'PATH': 'x'}, self.dir)
        self.assertEqual((env['POSTGRES_URL'], env['DATABASE_URL']), (SECRET, 'also'))                                 # (.env.local first, then .env for what is missing)
        self.assertEqual(len(said), 2)
        self.assertTrue(all('S3cret' not in x and 'postgres://' not in x for x in said))
        self.assertIn('POSTGRES_URL', said[0])
        env, _ = bookapi.settings_for_cli([], {'POSTGRES_URL': 'from-the-shell'}, self.dir)
        self.assertEqual(env['POSTGRES_URL'], 'from-the-shell')

    def test_a_file_named_on_the_command_line_is_the_only_one_read(self):
        self.write('.env', 'POSTGRES_URL=from-env\n')
        other = self.write('mine.env', 'OPS_DATABASE_URL=mine\n')
        env, said = bookapi.settings_for_cli(['check-db', '--env-file', other], {}, self.dir)
        self.assertEqual((env.get('OPS_DATABASE_URL'), env.get('POSTGRES_URL')), ('mine', None))
        env, said = bookapi.settings_for_cli(['check-db', '--env-file', os.path.join(self.dir, 'gone.env')], {}, self.dir)
        self.assertTrue('is not there' in said[0] and env.get('POSTGRES_URL') is None)

    def test_the_address_is_found_under_any_of_the_three_names_and_made_fit_for_libpq(self):
        self.assertEqual(bookapi.database_url({'DATABASE_URL': ' "a" '}), ('a', 'DATABASE_URL'))
        self.assertEqual(bookapi.database_url({'OPS_DATABASE_URL': 'o', 'POSTGRES_URL': 'p'}), ('o', 'OPS_DATABASE_URL'))
        self.assertEqual(bookapi.database_url({}), ('', ''))
        self.assertEqual(bookapi.conninfo(SECRET), 'postgres://dbuser:S3cretPassw0rd@db.example.com:5432/postgres?sslmode=require')      # (a hosted pool's own parameters are taken out)
        self.assertEqual(bookapi.conninfo('postgres://u:p@h/db'), 'postgres://u:p@h/db')

    def test_why_there_is_no_layer_is_told(self):
        old = bookapi.driver
        try:
            bookapi.driver = lambda: (None, 'psycopg is not installed')
            self.assertEqual(bookapi.durable_status({}), (None, 'no-url'))
            self.assertEqual(bookapi.durable_status({'POSTGRES_URL': SECRET}), (None, 'no-driver'))
            bookapi.driver = lambda: (type('PG', (), {'connect': staticmethod(lambda *a, **k: None)}), '')
            d, why = bookapi.durable_status({'POSTGRES_URL': SECRET})
            self.assertEqual((type(d).__name__, why), ('Durable', ''))
        finally:
            bookapi.driver = old


class TestTheDiagnosis(unittest.TestCase):
    class Cur:
        def __init__(self, answers, fail_on=None):
            self.answers, self.fail_on, self.sql = list(answers), fail_on, []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            self.sql.append(sql)
            if self.fail_on and self.fail_on in sql:
                raise RuntimeError('permission denied for schema library')

        def fetchone(self):
            return self.answers.pop(0)

    class Cx:
        def __init__(self, cur):
            self.cur = cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return self.cur

    def diag(self, env, cur=None, connect=None):
        cx = self.Cx(cur) if cur else None

        def default(url):
            if connect:
                raise connect
            return cx
        return bookapi.diagnose(env, default)

    def text(self, lines):
        return '\n'.join(l for _, l in lines)

    def test_no_address_says_where_to_put_it(self):
        lines, good = bookapi.diagnose({})
        self.assertFalse(good)
        self.assertIn('POSTGRES_URL', self.text(lines))
        self.assertIn('.env', self.text(lines))

    def test_no_driver_is_not_the_same_as_no_address(self):
        old = bookapi.driver
        bookapi.driver = lambda: (None, 'psycopg is not installed')
        try:
            lines, good = bookapi.diagnose({'POSTGRES_URL': SECRET})
        finally:
            bookapi.driver = old
        self.assertFalse(good)
        t = self.text(lines)
        self.assertIn('found in POSTGRES_URL', t)
        self.assertIn('pip install "psycopg[binary]"', t)
        self.assertNotIn('S3cret', t)

    def test_a_connection_that_fails_is_told_with_the_kind_of_error_and_never_the_address(self):
        lines, good = self.diag({'POSTGRES_URL': SECRET}, connect=OSError(SECRET))
        t = self.text(lines)
        self.assertFalse(good)
        self.assertTrue('connection: failed (OSError)' in t and 'S3cret' not in t and 'db.example.com' not in t)

    def test_a_missing_table_says_to_run_the_migration(self):
        lines, good = self.diag({'POSTGRES_URL': SECRET}, cur=self.Cur([(False,)]))
        self.assertFalse(good)
        self.assertIn('006_library_book_chapters.sql', self.text(lines))

    def test_a_table_that_cannot_be_written_is_told(self):
        lines, good = self.diag({'POSTGRES_URL': SECRET}, cur=self.Cur([(True,), (7,)], fail_on='INSERT'))
        t = self.text(lines)
        self.assertFalse(good)
        self.assertTrue('7 row(s)' in t and 'RuntimeError' in t and 'permission denied' not in t)

    def test_everything_in_place(self):
        cur = self.Cur([(True,), (12,), ('<p>x</p>',)])
        lines, good = self.diag({'OPS_DATABASE_URL': SECRET}, cur=cur)
        self.assertTrue(good and all(ok for ok, _ in lines), lines)
        self.assertIn('12 row(s)', self.text(lines))
        self.assertTrue(any('DELETE' in q for q in cur.sql))                                                          # (the test row is taken away again)
        self.assertNotIn('S3cret', self.text(lines))


class TestLoad(Base):
    def test_the_chapters_of_the_books_that_may_be_kept_are_read_and_written_and_what_is_there_is_left_alone(self):
        self.durable.rows[('tale', 1)] = {'t': 'there', 'h': '<p>h</p>', 'l': 'en', 'u': 'u', 'at': 1.0}
        said = []
        w, there, failed = bookapi.load(self.durable, only='tale', say=said.append, fetch=self.fetch, pause=0, sleep=lambda s: None)
        self.assertEqual((w, there, failed), (2, 1, 0))
        self.assertEqual(sorted(self.durable.rows), [('tale', 0), ('tale', 1), ('tale', 2)])
        self.assertEqual(len(self.fetch.calls), 2)                                                                    # (chapter 2 of 3 was there: not read again)
        self.assertEqual([p[2] for p in self.durable.puts], ['public-domain', 'public-domain'])

    def test_all_the_books_that_may_be_kept_and_only_those(self):
        w, there, failed = bookapi.load(self.durable, say=lambda m: None, fetch=self.fetch, pause=0, sleep=lambda s: None)
        books = {b for b, _ in self.durable.rows}
        self.assertEqual((books, w, failed), ({'tale', 'side', 'page'}, 7, 0))                                       # (3 + 3 + 1 chapters; not closed, free, hidden)

    def test_a_book_that_may_not_be_kept_is_not_loaded_even_when_it_is_named(self):
        said = []
        self.assertEqual(bookapi.load(self.durable, only='closed', say=said.append, fetch=self.fetch, pause=0, sleep=lambda s: None), (0, 0, 0))
        self.assertTrue(said and 'closed' in said[0])
        self.assertEqual((self.fetch.calls, self.durable.rows), ([], {}))

    def test_a_page_that_cannot_be_read_is_said_and_counted_and_the_others_go_on(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if url.endswith('Chapter_2'):
                raise readable.Unreadable('gone')
            return Fetch()(url)
        said = []
        w, there, failed = bookapi.load(self.durable, only='tale', say=said.append, fetch=fetch, pause=0, sleep=lambda s: None)
        self.assertEqual((w, failed), (2, 1))
        self.assertTrue(any('chapter 1' in m and 'gone' in m for m in said), said)

    def test_it_stops_when_everything_fails_so_a_dead_database_is_not_hammered(self):
        class Dead(FakeDurable):
            def put(self, *a):
                return False
        many = [entry('big', ch={'s': 'en.wikisource.org', 'p': 'Big/', 'n': ['C%d' % i for i in range(40)]})]
        with open(agg.books_path(), 'w', encoding='utf-8') as f:
            json.dump(many, f)
        os.utime(agg.books_path(), (T0 + 500, T0 + 500))
        said = []
        w, there, failed = bookapi.load(Dead(), say=said.append, fetch=self.fetch, pause=0, sleep=lambda s: None, fails_in_a_row=5)
        self.assertEqual((w, failed, len(self.fetch.calls)), (0, 5, 5))
        self.assertIn('stopping', said[-1])


class TestStatusSaysWhy(Base):
    def test_the_status_of_the_service_says_why_there_is_no_long_term_layer(self):
        h = FakeHandler('GET', origin='')
        h.path = '/api/book'
        bookapi.respond(h, ENV, 'GET', store=self.store, durable=None)
        body = json.loads(h.out['body'])
        self.assertEqual((body['durable'], body['durable_why']), (False, 'no-url'))
        h = FakeHandler('GET', origin='')
        h.path = '/api/book'
        bookapi.respond(h, ENV, 'GET', store=self.store, durable=self.durable)
        self.assertNotIn('durable_why', json.loads(h.out['body']))


if __name__ == '__main__':
    unittest.main()
