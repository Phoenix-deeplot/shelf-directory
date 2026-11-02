"""books_tool.py: the hand-made list of picked books (registry/books.json): fills in the chapters of a work that lives on a Wikisource, and checks that every address still opens.
The build (aggregate.py) needs NO network for the books: the chapters are written into the file.

    python -X utf8 books_tool.py fill      the entries with "ch": {"s": "zh.wikisource.org", "p": "紅樓夢/"} and no "n" yet: the pages below that name are listed (in the order a reader counts: Chapter 2 before
                                           Chapter 10; no redirects; at most 300) and written into "n". Then "read" is set to the title page when it was empty.
    python -X utf8 books_tool.py check     every "read" address and the first chapter of every work are fetched; what does not answer is listed (a work whose chapters are all gone is a work to take out)
    python -X utf8 books_tool.py warm [--api https://adminbook.zhitree.cn/api/book] [--only <id>]
                                           asks the service for every chapter of the books that may be kept on the server (bookapi.py), slowly, so that the first reader of each is not the one who waits;
                                           what is there already is answered at once. Stops at the first "limit" answer (try again in ten minutes).

An entry (all the fields are checked again by the build, aggregate.load_books):
    id (a slug), title, author, lang ("zh"|"en"), kind ("novel"|"story"|"poetry"|"classic"|"magazine"|"journal"|"textbook"|"essay"|"library"), tags (the fields: 小说, 科学, ...), summary (a sentence or two),
    license ("public-domain"|"cc0"|"cc-by"|"cc-by-sa"|"cc-by-nc"|"cc-by-nc-sa"|"free-to-read": whoever may read it for nothing), source (the site's name), read (an https address a reader opens),
    ch (optional): {"s": site, "p": prefix of the page names, "n": [the rest of each name]}: the chapters, read in the page (a chapter is https://<s>/wiki/<p><n>).
    keep (optional, true|false): the book is kept for good on the server (PostgreSQL) once a chapter was read; share (optional, false): never keep it on the server (bookapi.py).
Only what anybody may read for nothing belongs here: the public domain, a licence that says so, or a text its author gives away on the web."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, 'registry', 'books.json')
UA = 'ZhiTreeBooks-Curation/1 (+https://books.zhitree.cn)'
MAX_CHAPTERS = 300


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(400_000)


def natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]


def subpages(site, prefix):
    """The names of the pages whose name begins with `prefix` (not redirects), in reading order."""
    out, cont = [], None
    for _ in range(20):
        q = {'action': 'query', 'list': 'allpages', 'apprefix': prefix, 'aplimit': '500', 'apfilterredir': 'nonredirects', 'format': 'json'}
        if cont:
            q['apcontinue'] = cont
        status, body = get('https://%s/w/api.php?%s' % (site, urllib.parse.urlencode(q)))
        d = json.loads(body.decode('utf-8'))
        out += [p['title'] for p in d['query']['allpages']]
        cont = (d.get('continue') or {}).get('apcontinue')
        if not cont or len(out) >= 2000:
            break
        time.sleep(0.2)
    return sorted(out, key=natural)


def load():
    with open(PATH, encoding='utf-8') as f:
        return json.load(f)


def save(books):
    with open(PATH, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(books, f, ensure_ascii=False, indent=1)
        f.write('\n')


def fill(books, log=print):
    changed = 0
    for b in books:
        ch = b.get('ch')
        if not isinstance(ch, dict) or ch.get('n'):
            continue
        try:
            names = subpages(ch['s'], ch['p'])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            log('%s: the list of pages did not come (%s)' % (b['id'], type(e).__name__))
            continue
        names = [x[len(ch['p']):] for x in names if x.startswith(ch['p']) and len(x) > len(ch['p'])][:MAX_CHAPTERS]
        if not names:
            log('%s: no pages below "%s" on %s: the work is not there under that name' % (b['id'], ch['p'], ch['s']))
            continue
        ch['n'] = names
        changed += 1
        log('%s: %d chapters (%s ... %s)' % (b['id'], len(names), names[0], names[-1]))
    return changed


def chapter_url(ch, name):
    import bookapi                                      # (one way to make the address of a chapter: the one the service uses)
    return bookapi.chapter_url(ch, name)


def check(books, log=print):
    bad = 0
    for b in books:
        urls = [('read', b['read'])]
        if isinstance(b.get('ch'), dict) and b['ch'].get('n'):
            urls.append(('chapter 1', chapter_url(b['ch'], b['ch']['n'][0])))
        for what, u in urls:
            try:
                status, _ = get(u)
                ok = status == 200
            except (urllib.error.URLError, OSError) as e:
                status, ok = getattr(e, 'code', type(e).__name__), False
            if not ok:
                bad += 1
                log('%s %s: %s -> %s' % (b['id'], what, u, status))
            time.sleep(0.2)
    log('%d address(es) did not open' % bad)
    return bad


def warm(books, api, only=None, log=print, pause=0.6):
    """Ask the service for each chapter of the books that may be kept (so the copies exist before the readers come). -> how many chapters answered."""
    import bookapi
    done = 0
    for b in books:
        if only and b['id'] != only:
            continue
        if not bookapi.shared(b):
            continue
        n = len(b['ch']['n']) if isinstance(b.get('ch'), dict) and b['ch'].get('n') else (1 if b.get('readable') is True else 0)
        for c in range(n):
            u = '%s?b=%s&c=%d' % (api, urllib.parse.quote(b['id']), c)
            try:
                status, body = get(u, timeout=40)
                d = json.loads(body.decode('utf-8'))
            except urllib.error.HTTPError as e:
                try:
                    d = json.loads(e.read().decode('utf-8'))
                except (OSError, ValueError):
                    d = {'ok': False, 'error': 'http %s' % e.code}
            except (urllib.error.URLError, OSError, ValueError) as e:
                d = {'ok': False, 'error': type(e).__name__}
            if d.get('ok'):
                done += 1
            else:
                log('%s chapter %d: %s' % (b['id'], c, d.get('error')))
                if d.get('error') == 'limit':
                    log('the service says it is asked too often: stop here and try again in ten minutes')
                    return done
            if (c + 1) % 20 == 0:
                log('%s: %d / %d' % (b['id'], c + 1, n))
            time.sleep(pause if d.get('tier') == 'source' else 0.05)           # (a copy that was there needs no politeness)
        log('%s: %d chapter(s) done' % (b['id'], n))
    return done


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    books = load()
    if cmd == 'fill':
        if fill(books):
            save(books)
    elif cmd == 'check':
        sys.exit(1 if check(books) else 0)
    elif cmd == 'warm':
        args = sys.argv[2:]
        api = args[args.index('--api') + 1] if '--api' in args else 'https://adminbook.zhitree.cn/api/book'
        only = args[args.index('--only') + 1] if '--only' in args else None
        print('%d chapter(s) answered' % warm(books, api, only))
    else:
        print(__doc__)
