"""books_tool.py: the hand-made list of picked books (registry/books.json): fills in the chapters of a work that lives on a Wikisource, and checks that every address still opens.
The build (aggregate.py) needs NO network for the books: the chapters are written into the file.

    python -X utf8 books_tool.py fill      the entries with "ch": {"s": "zh.wikisource.org", "p": "紅樓夢/"} and no "n" yet: the pages below that name are listed (in the order a reader counts: Chapter 2 before
                                           Chapter 10; no redirects; at most 300) and written into "n". Then "read" is set to the title page when it was empty.
    python -X utf8 books_tool.py check     every "read" address and the first chapter of every work are fetched; what does not answer is listed (a work whose chapters are all gone is a work to take out)

An entry (all the fields are checked again by the build, aggregate.load_books):
    id (a slug), title, author, lang ("zh"|"en"), kind ("novel"|"story"|"poetry"|"classic"|"magazine"|"journal"|"textbook"|"essay"|"library"), tags (the fields: 小说, 科学, ...), summary (a sentence or two),
    license ("public-domain"|"cc0"|"cc-by"|"cc-by-sa"|"cc-by-nc"|"cc-by-nc-sa"|"free-to-read": whoever may read it for nothing), source (the site's name), read (an https address a reader opens),
    ch (optional): {"s": site, "p": prefix of the page names, "n": [the rest of each name]}: the chapters, read in the page (a chapter is https://<s>/wiki/<p><n>).
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
    return 'https://%s/wiki/%s' % (ch['s'], urllib.parse.quote((ch['p'] + name).replace(' ', '_'), safe='/:()\',;!*~'))


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


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    books = load()
    if cmd == 'fill':
        if fill(books):
            save(books)
    elif cmd == 'check':
        sys.exit(1 if check(books) else 0)
    else:
        print(__doc__)
