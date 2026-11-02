"""bookapi.py: the shared copy of the chapters of the picked books (api/book.py is only the Vercel wrapper).

    GET /api/book?b=<id of a book of registry/books.json>&c=<chapter number, 0 for a book that is one page>
        -> {"ok": true, "b", "c", "title", "content": <safe html>, "lang", "license", "source": <where it was read>, "by", "tier": "hot" | "durable" | "source", "cut", "v"}
        -> {"ok": false, "error": "unknown" | "nocache" | "unreadable" | "limit" | "busy" | "origin" | "request", "message": "...", "message_en": "..."}
    GET /api/book  -> {"ok": true, "service": "shelf-book", "redis": bool, "durable": bool, "books": n, "shared": n}

What this is for: the first reader of a chapter makes the function read the original page (a few seconds, and the site may be slow or down); everybody after gets the copy at once, without the original
being asked again. Where a copy is kept: Redis (the hot layer: every chapter that was read, 30 days, 14 days for a book that is kept in PostgreSQL as well) and PostgreSQL (the long-term layer: the books
that are marked "keep": true in the list, and any chapter that was read at least BOOK_PROMOTE_READS times: what is not read much goes away by itself). The answer carries a CDN header, so a chapter that
many read costs no query at all for an hour. Reading order: CDN -> Redis -> PostgreSQL (what is found is put back in Redis) -> the original page.

What may be kept: ONLY a book whose licence lets anybody copy it (public-domain, cc0, cc-by, cc-by-sa) and that is not marked "share": false in the list. Anything else answers "nocache" and the page reads it
as it did before (through /api/feed), keeping it in the reader's own browser only. The address of a chapter is made HERE from the list, never taken from the request: this is not a proxy, and nothing
else can be put in the store. A chapter is read like /api/feed does it (the same safe reading, robots.txt, a time limit) and kept as the safe html that comes out of it.

Without Redis nothing is counted or locked, but reading still works (a soft limit in the memory of the instance); without PostgreSQL there is just the one layer. A broken layer is a miss, never an error
for the reader. The connection is OPS_DATABASE_URL (or POSTGRES_URL / DATABASE_URL); this function reads and writes one table, library.book_chapters (migrations/006_library_book_chapters.sql).
From a terminal (the settings are read from this shell, or from .env.local / .env of this folder, or --env-file <file>; nothing secret is printed):
    python bookapi.py check-db            says step by step what is there: the address, the driver (psycopg), the connection, the table, one row written and read
    python bookapi.py load [--only <id>]  reads the chapters of the books that may be kept from their original pages and writes them straight into PostgreSQL (what is there is left alone); no Vercel, no Redis
Settings (all optional): BOOK_PROMOTE_READS (default 3), BOOK_SOURCE_PER_10MIN (reads of an original page per reader, default 30)."""
import json
import os
import re
import sys
import threading
import time
import urllib.parse

import aggregate as agg
import feedapi
import readable
import submission
import syncapi
from submission import Reply
from syncapi import StoreError

PARSER = 1                                   # the version of the reading: a better one is a new key, so the copies of the old one are read again
SHARED_LICENSES = ('public-domain', 'cc0', 'cc-by', 'cc-by-sa')
HOT_DAYS_KEPT = 14                           # in Redis, for a book that PostgreSQL holds as well (it comes back from there)
HOT_DAYS = 30                                # in Redis, for the others (a chapter that is read again is kept as long again)
PROMOTE_READS = 3                            # reads that reached this function (the CDN hides the others: one an hour at most) before a chapter that is not "keep" goes to PostgreSQL
SOURCE_PER_10MIN = 30
LOCK_SECONDS = 30
WAIT_SECONDS = 6
MAX_CONTENT = 400_000                        # characters of one stored chapter (the reading cuts at feedapi.READ_CHARS; this is the guard of the store)
BOOK_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')
CDN = 'public, max-age=300, s-maxage=3600, stale-while-revalidate=86400'
_LOCAL_USED = {}                             # the soft limit of a function without Redis


# ======================================================================= the list of books
_CATALOG = {'key': None, 'books': {}}
_CATALOG_LOCK = threading.Lock()


def catalog():
    """-> {id: entry} of the well-made entries of registry/books.json (read again when the file changes)."""
    path = agg.books_path()
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    with _CATALOG_LOCK:
        if _CATALOG['key'] != (path, mtime):
            books = {}
            data = agg.read_json(path, [])
            for b in data if isinstance(data, list) else []:
                item, _ = agg.check_book(b)
                if item:
                    books[b['id']] = b
            _CATALOG.update(key=(path, mtime), books=books)
        return _CATALOG['books']


def shared(entry):
    """May this book be kept for everybody? Its licence says so and the list does not say no."""
    return entry.get('license') in SHARED_LICENSES and entry.get('share') is not False


def chapter_url(ch, name):
    """The address of chapter `name` of a book with chapters (a wiki: a space is written _)."""
    return 'https://%s/wiki/%s' % (ch['s'], urllib.parse.quote((ch['p'] + name).replace(' ', '_'), safe="/:()',;!*~"))


def unit(entry, c):
    """-> (address, chapter name) of chapter c of a book, or None when there is no such chapter."""
    ch = entry.get('ch')
    if ch:
        if not 0 <= c < len(ch['n']):
            return None
        return chapter_url(ch, ch['n'][c]), ch['n'][c]
    if entry.get('readable') is True and c == 0:
        return agg.abs_url(entry['read']), ''
    return None


# ======================================================================= the layers
class Hot:
    """Redis (the store of syncapi: MemoryStore in the tests, UpstashStore for real) as this function uses it; without a store the few things that count are counted in memory."""

    def __init__(self, store):
        self.store = store

    def get(self, key):
        if self.store is None:
            return None
        try:
            v = self.store.run([('GET', key)])[0]
        except StoreError:
            return None
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def put(self, key, rec, days):
        if self.store is None:
            return
        try:
            self.store.run([('SET', key, json.dumps(rec, ensure_ascii=False, separators=(',', ':')), 'EX', days * 86400)])
        except StoreError:
            pass

    def lock(self, key):
        """True when this request is the one that goes to the original page."""
        if self.store is None:
            return True
        try:
            return self.store.run([('SET', key, '1', 'NX', 'EX', LOCK_SECONDS)])[0] == 'OK'
        except StoreError:
            return True

    def unlock(self, key):
        if self.store is not None:
            try:
                self.store.run([('DEL', key)])
            except StoreError:
                pass

    def count(self, key, days):
        """-> how many times (this one included); 0 when it cannot be counted."""
        if self.store is None:
            return 0
        try:
            n = self.store.run([('INCR', key), ('EXPIRE', key, days * 86400)])[0]
            return int(n)
        except (StoreError, ValueError, TypeError):
            return 0

    def source_allowance(self, who, now, limit):
        """A reader may make the function read an original page `limit` times in ten minutes (a copy that is there costs nothing)."""
        if self.store is not None:
            try:
                key = 'bk:q:%s:%d' % (who, int(now // 600))
                n = self.store.run([('INCR', key), ('EXPIRE', key, 1200)])[0]
                return int(n) <= limit
            except (StoreError, ValueError, TypeError):
                pass
        recent = [t for t in _LOCAL_USED.get(who, []) if now - t < 600]
        ok = len(recent) < limit
        if ok:
            recent.append(now)
        _LOCAL_USED[who] = recent
        if len(_LOCAL_USED) > 2000:
            for k in [k for k, v in _LOCAL_USED.items() if not v or now - v[-1] > 600]:
                _LOCAL_USED.pop(k, None)
        return ok


class Durable:
    """The long-term layer in PostgreSQL: the one table library.book_chapters. Every method answers "nothing" / does nothing when the database is not there or fails: the long-term layer is a help, not a must."""

    def __init__(self, connect):
        self.connect = connect               # a function that gives a psycopg connection (a context manager) or raises

    def get(self, b, c):
        try:
            with self.connect() as cx, cx.cursor() as cur:
                cur.execute('SELECT title, html, lang, source_url, fetched_at, cut FROM library.book_chapters WHERE book_id=%s AND ch=%s AND parser=%s', (b, c, PARSER))
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute('UPDATE library.book_chapters SET reads=reads+1, last_read_at=now() WHERE book_id=%s AND ch=%s AND parser=%s', (b, c, PARSER))
        except Exception:                    # any failure of the database is a miss
            return None
        return {'t': row[0], 'h': row[1], 'l': row[2], 'u': row[3], 'at': float(row[4] or 0), 'cut': bool(row[5]), 'v': PARSER}

    def has(self, b, c):
        try:
            with self.connect() as cx, cx.cursor() as cur:
                cur.execute('SELECT 1 FROM library.book_chapters WHERE book_id=%s AND ch=%s AND parser=%s', (b, c, PARSER))
                return cur.fetchone() is not None
        except Exception:
            return False

    def put(self, b, c, rec, license):
        try:
            with self.connect() as cx, cx.cursor() as cur:
                cur.execute('INSERT INTO library.book_chapters (book_id, ch, parser, title, html, lang, license, source_url, bytes, cut, reads, fetched_at, last_read_at) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,now()) ON CONFLICT (book_id, ch, parser) DO UPDATE SET title=EXCLUDED.title, html=EXCLUDED.html, lang=EXCLUDED.lang, '
                            'license=EXCLUDED.license, source_url=EXCLUDED.source_url, bytes=EXCLUDED.bytes, cut=EXCLUDED.cut, fetched_at=EXCLUDED.fetched_at',
                            (b, c, PARSER, rec['t'], rec['h'], rec['l'], license, rec['u'], len((rec['h'] or '').encode('utf-8')), bool(rec.get('cut')), rec['at']))
            return True
        except Exception:
            return False


DB_VARIABLES = ('OPS_DATABASE_URL', 'POSTGRES_URL', 'DATABASE_URL')
_NOT_LIBPQ = ('supa', 'pgbouncer', 'connection_limit', 'pool_timeout', 'schema')       # parameters that a provider's address may carry for its own tools: libpq (psycopg) refuses them


def database_url(env):
    """-> (the address, the name of the setting it came from) or ('', '')."""
    for name in DB_VARIABLES:
        v = str(env.get(name) or '').strip().strip('"\'')
        if v:
            return v, name
    return '', ''


def conninfo(url):
    """The address as psycopg wants it: postgres:// is fine as it is, but the parameters that are not libpq's own (?pgbouncer=true&supa=base-pooler.x of a hosted pool) are taken out."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    keep = [kv for kv in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if kv[0] not in _NOT_LIBPQ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(keep)))


def driver():
    """-> (the psycopg module, '') or (None, why)."""
    try:
        import psycopg                                   # installed from requirements.txt on Vercel
        return psycopg, ''
    except ImportError:
        return None, 'psycopg is not installed'


def durable_status(env):
    """-> (Durable or None, why not): why is '' when there is one, 'no-url' (no address in the settings) or 'no-driver' (psycopg is not installed)."""
    url, _ = database_url(env)
    if not url:
        return None, 'no-url'
    pg, _ = driver()
    if pg is None:
        return None, 'no-driver'
    return Durable(lambda: pg.connect(conninfo(url), connect_timeout=4, autocommit=True)), ''


def durable_from_env(env):
    """-> Durable, or None when no database is set up (or its driver is missing)."""
    return durable_status(env)[0]


def _number(env, name, default, low, top):
    try:
        return max(low, min(top, int(str(env.get(name) or '').strip())))
    except ValueError:
        return default


# ======================================================================= the service
def _record(art, url, now):
    safe = art.get('content') or ''
    if len(safe) > MAX_CONTENT:
        safe = safe[:MAX_CONTENT]
    return {'t': art.get('title') or '', 'h': safe, 'l': art.get('lang') or '', 'u': art.get('url') or url, 'at': now, 'cut': bool(art.get('cut')), 'v': PARSER}


def _answer(entry, c, rec, tier):
    return {'ok': True, 'b': entry['id'], 'c': c, 'title': rec['t'], 'content': rec['h'], 'lang': rec.get('l') or entry.get('lang') or '', 'license': entry['license'],
            'source': rec.get('u') or '', 'by': entry.get('source') or '', 'tier': tier, 'cut': bool(rec.get('cut')), 'v': PARSER}


def status(hot, durable, env=None):
    books = catalog()
    out = {'ok': True, 'service': 'shelf-book', 'redis': hot.store is not None, 'durable': durable is not None, 'books': len(books), 'shared': sum(1 for b in books.values() if shared(b))}
    if durable is None and env is not None:
        out['durable_why'] = durable_status(env)[1]              # (no secret in it: "no-url" or "no-driver")
    return out


def chapter(query, ip, env, hot, durable, now=None, fetch=None, sleep=time.sleep):
    """The chapter a query asks for. -> (answer dict, cacheable). Raises Reply. `fetch(url)` -> the article dict of readable.extract (the tests give their own)."""
    now = time.time() if now is None else now
    b, c = (query.get('b') or [''])[0], (query.get('c') or ['0'])[0]
    if not BOOK_ID.match(b) or not re.match(r'^\d{1,4}$', c):
        raise Reply(400, 'request', '请求格式不对', 'Bad request')
    c = int(c)
    books = catalog()
    entry = books.get(b)
    got = unit(entry, c) if entry else None
    if not entry or not got:
        raise Reply(404, 'unknown', '没有这本书或这一章', 'No such book or chapter')
    entry = {**entry, 'id': b}
    if not shared(entry):
        return {'ok': False, 'error': 'nocache', 'message': '这本书不放在云端，请直接读', 'message_en': 'This book is not kept on the server; read it directly'}, 'nocache'
    url = got[0]
    key = 'bk:%d:%s:%d' % (PARSER, b, c)
    keep = entry.get('keep') is True
    reads_needed = _number(env, 'BOOK_PROMOTE_READS', PROMOTE_READS, 1, 1000)

    def served(rec, tier):
        n = hot.count('bk:r:%s' % key, HOT_DAYS)                    # a read that reached this function: the chapter is wanted
        if durable is not None and not keep and tier != 'durable' and n and n == reads_needed:
            durable.put(b, c, rec, entry['license'])                # read often enough: kept for good
        return _answer(entry, c, rec, tier), 'ok'

    rec = hot.get(key)
    if rec:
        return served(rec, 'hot')
    if durable is not None:
        rec = durable.get(b, c)
        if rec:
            hot.put(key, rec, HOT_DAYS_KEPT)                        # (what comes from the long-term layer is put back in the hot one)
            return served(rec, 'durable')
    who = submission.ip_hash(ip, submission.settings(env)['salt'])
    if not hot.lock('bk:lock:' + key):                             # somebody else is reading it now: wait for their copy
        waited = 0.0
        while waited < WAIT_SECONDS:
            sleep(0.5)
            waited += 0.5
            rec = hot.get(key)
            if rec:
                return served(rec, 'hot')
        raise Reply(503, 'busy', '这一章正在读取，请稍后再试', 'This chapter is being read; try again in a moment')
    try:
        if not hot.source_allowance(who, now, _number(env, 'BOOK_SOURCE_PER_10MIN', SOURCE_PER_10MIN, 1, 1000)):
            raise Reply(429, 'limit', '读得太频繁了，请稍等几分钟', 'Too many chapters at once; wait a few minutes')
        try:
            with feedapi.reading():
                art = (fetch or (lambda u: feedapi._page_article(u, None, env)))(url)
        except readable.Unreadable as e:
            raise Reply(422, 'unreadable', e.zh, e.en)
        except agg.Refused as e:
            raise Reply(422, 'unreadable', str(e), getattr(e, 'en', '') or str(e))
        rec = _record(art, url, now)
        if not rec['h']:
            raise Reply(422, 'unreadable', '这一章读不出正文', 'No text could be read from this chapter')
        hot.put(key, rec, HOT_DAYS_KEPT if (keep and durable is not None) else HOT_DAYS)
        if keep and durable is not None:
            durable.put(b, c, rec, entry['license'])
        return _answer(entry, c, rec, 'source'), 'ok'
    finally:
        hot.unlock('bk:lock:' + key)


def respond(h, env, method, store=None, durable=None, fetch=None):
    """Answer one request on a BaseHTTPRequestHandler `h` (used by api/book.py and by the tests): a page of another site is refused, as in the other functions."""
    store = store if store is not None else syncapi.store_from_env(env)
    durable = durable if durable is not None else durable_from_env(env)
    hot = Hot(store)
    cfg = submission.settings(env)
    origin = h.headers.get('Origin') or ''
    cors = submission.cors_headers(origin, cfg)

    def send(code, payload, cache='no-store', extra=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else b''
        h.send_response(code)
        h.send_header('Content-Type', 'application/json; charset=utf-8')
        h.send_header('Vary', 'Origin')
        for k, v in {'Cache-Control': cache, **cors, **(extra or {})}.items():
            h.send_header(k, v)
        h.send_header('Content-Length', str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    if method == 'OPTIONS':
        return send(204, None, extra={'Access-Control-Allow-Methods': 'GET, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400'} if cors else {})
    if origin and not cors:
        return send(403, {'ok': False, 'error': 'origin', 'message': '这个来源不允许', 'message_en': 'This origin is not allowed'})
    if method != 'GET':
        return send(405, {'ok': False, 'error': 'request', 'message': '请求格式不对', 'message_en': 'Bad request'})
    parts = urllib.parse.urlsplit(getattr(h, 'path', '') or '')
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if not query:
        return send(200, status(hot, durable, env), 'public, max-age=60, s-maxage=300')
    ip = (h.headers.get('X-Vercel-Forwarded-For') or h.headers.get('X-Forwarded-For') or h.headers.get('X-Real-IP') or (h.client_address[0] if h.client_address else '')).split(',')[0].strip()
    try:
        body, kind = chapter(query, ip, env, hot, durable, fetch=fetch)
        if kind == 'nocache':
            return send(200, body, 'public, max-age=300, s-maxage=3600')       # (the same answer every time: the CDN may keep it)
        return send(200, body, CDN)
    except Reply as r:
        return send(r.status, r.body())


# ======================================================================= the command line
def read_env_file(path):
    """The settings of a .env file (KEY=VALUE lines, quotes allowed, # comments) -> dict. Nothing is put in the environment here."""
    out = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if k.startswith('export '):
                    k = k[7:].strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                    v = v[1:-1]
                elif ' #' in v:
                    v = v.split(' #', 1)[0].strip()
                if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', k):
                    out[k] = v
    except OSError:
        return {}
    return out


def settings_for_cli(argv, environ=None, folder=None):
    """-> (env, [lines saying where the settings came from]): this shell first, then --env-file, then .env.local and .env of this folder (what the shell has wins)."""
    environ = os.environ if environ is None else environ
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    env, said = dict(environ), []
    files = [argv[argv.index('--env-file') + 1]] if '--env-file' in argv and argv.index('--env-file') + 1 < len(argv) else [os.path.join(folder, '.env.local'), os.path.join(folder, '.env')]
    for p in files:
        if os.path.isfile(p):
            got = read_env_file(p)
            for k, v in got.items():
                env.setdefault(k, v)
            names = [k for k in got if k in DB_VARIABLES]
            said.append('read %s (the database setting in it: %s)' % (p, ', '.join(names) if names else 'none'))
        elif '--env-file' in argv:
            said.append('the file %s is not there' % p)
    return env, said


def diagnose(env, connect_with=None):
    """-> ([(ok, line)...], all ok?): the steps from "is there an address" to "one row written and read back"; no address, password or row text is ever printed. `connect_with(url)` gives a connection (tests give a fake)."""
    lines = []
    url, name = database_url(env)
    if not url:
        lines.append((False, 'database address: not found. Set OPS_DATABASE_URL (or POSTGRES_URL / DATABASE_URL) in this terminal, in .env.local / .env of this folder, or give --env-file <file>'))
        return lines, False
    lines.append((True, 'database address: found in %s (not printed)' % name))
    pg, why = driver()
    if connect_with is None:
        if pg is None:
            lines.append((False, 'driver: %s. Run:  pip install "psycopg[binary]"   (the Python that runs this: %s)' % (why, sys.executable)))
            return lines, False
        lines.append((True, 'driver: psycopg %s' % getattr(pg, '__version__', '?')))
        connect_with = lambda u: pg.connect(conninfo(u), connect_timeout=6, autocommit=True)       # noqa: E731
    try:
        cx = connect_with(url)
    except Exception as e:
        lines.append((False, 'connection: failed (%s). The address, the user or the password may be wrong, the database may not be running (a local one: is it started?), or the host is not reachable from here' % type(e).__name__))
        return lines, False
    lines.append((True, 'connection: ok'))
    try:
        with cx, cx.cursor() as cur:
            cur.execute("SELECT to_regclass('library.book_chapters') IS NOT NULL")
            if not cur.fetchone()[0]:
                lines.append((False, 'table library.book_chapters: missing. Run once in this database:  \\i migrations/006_library_book_chapters.sql   (psql -d <database> -f migrations/006_library_book_chapters.sql)'))
                return lines, False
            cur.execute('SELECT count(*) FROM library.book_chapters')
            lines.append((True, 'table library.book_chapters: there, %d row(s)' % cur.fetchone()[0]))
            cur.execute("INSERT INTO library.book_chapters (book_id, ch, parser, title, html, lang, license, source_url, bytes, cut, reads, fetched_at, last_read_at) "
                        "VALUES ('_check', 0, 0, 't', '<p>x</p>', 'en', 'public-domain', 'https://example.org/', 8, false, 0, 0, now()) ON CONFLICT (book_id, ch, parser) DO UPDATE SET fetched_at=0")
            cur.execute("SELECT html FROM library.book_chapters WHERE book_id='_check' AND ch=0 AND parser=0")
            same = cur.fetchone()[0] == '<p>x</p>'
            cur.execute("DELETE FROM library.book_chapters WHERE book_id='_check'")
            lines.append((same, 'one row written, read back and deleted: %s' % ('ok' if same else 'READ BACK DIFFERENT')))
            return lines, same
    except Exception as e:
        lines.append((False, 'the table could not be used (%s): the user may not be allowed to write in schema "library", or the table is not the one of migration 006' % type(e).__name__))
        return lines, False


def load(durable, only=None, say=print, fetch=None, pause=0.6, sleep=time.sleep, env=None, fails_in_a_row=8):
    """Read the chapters of the books that may be kept from their original pages and write them straight into PostgreSQL (what is there is left alone). -> (written, there already, failed)."""
    env = env or {}
    done = [0, 0, 0]
    streak = 0
    books = [(k, b) for k, b in catalog().items() if shared(b) and (not only or k == only)]
    if only and not books:
        say('no book "%s" that may be kept on the server (see registry/books.json: its licence and "share")' % only)
        return tuple(done)
    for bid, b in books:
        n = len(b['ch']['n']) if b.get('ch') else (1 if b.get('readable') is True else 0)
        if not n:
            continue
        fresh = 0
        for c in range(n):
            if durable.has(bid, c):
                done[1] += 1
                continue
            url = unit(b, c)[0]
            try:
                with feedapi.reading():
                    art = (fetch or (lambda u: feedapi._page_article(u, None, env)))(url)
                rec = _record(art, url, time.time())
                if not rec['h']:
                    raise ValueError('no text')
                ok = durable.put(bid, c, rec, b['license'])
            except Exception as e:
                ok = False
                say('%s chapter %d: not read (%s)' % (bid, c, getattr(e, 'code', '') or type(e).__name__))
            if ok:
                done[0] += 1
                fresh += 1
                streak = 0
            else:
                done[2] += 1
                streak += 1
                if streak >= fails_in_a_row:
                    say('%d failures in a row: stopping (is the database writable, is the site reachable?)' % streak)
                    return tuple(done)
            sleep(pause)
        say('%s: %d chapter(s) read and written now, %d already there or failed' % (bid, fresh, n - fresh))
    return tuple(done)


if __name__ == '__main__':
    args = sys.argv[1:]
    cmd = args[0] if args else ''
    if cmd in ('check-db', 'load'):
        cli_env, said = settings_for_cli(args)
        for line in said:
            print(line)
    if cmd == 'check-db':
        lines, good = diagnose(cli_env)
        for ok, line in lines:
            print(('OK    ' if ok else 'FIX   ') + line)
        sys.exit(0 if good else 1)
    elif cmd == 'load':
        lines, good = diagnose(cli_env)
        if not good:
            for ok, line in lines:
                print(('OK    ' if ok else 'FIX   ') + line)
            sys.exit('the database is not ready: fix the line marked FIX, then run this again')
        d = durable_status(cli_env)[0]
        only = args[args.index('--only') + 1] if '--only' in args and args.index('--only') + 1 < len(args) else None
        w, there, failed = load(d, only, env=cli_env)
        print('written %d, there already %d, failed %d' % (w, there, failed))
        sys.exit(1 if failed else 0)
    else:
        print(__doc__)
