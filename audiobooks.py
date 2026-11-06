"""Audiobooks for the books page: a short list picked by hand (registry/audiobooks.json: public domain, or the broadcaster's own; nothing else goes in) and, for one book, ALL its chapters in order.

  GET /api/listen?books=1&lang=zh|en   -> {ok, styles: [{id, name, name_en}], books: [{id, title, title_en, author, narrator, lang, style, rights, source, cover, summary, n, secs, hot, url}]}   (the most popular first)
  GET /api/listen?book=<id>            -> {ok, stale?, book: {... the same ..., chapters: [{n, title, audio, dur}]}}                       (n from 1, the first chapter first)

An audiobook is not a podcast: it has a beginning and an end, it is heard in order and picked up where it was left, and all of it is needed (a podcast keeps its last few dozen episodes). The chapters are the items
of the book's RSS feed: LibriVox gives one per recording, in order (the sounds are on archive.org); a feed that has the newest first is turned round (`order: desc`). The feed is read up to 2 MB, an item that was cut
at the end is left out, at most 600 chapters are kept; what was read is kept in the instance for 6 hours (and served 36 hours more when the feed does not answer)."""
import html
import json
import os
import re

import aggregate as agg

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'registry', 'audiobooks.json')
MAX_CHAPTERS = 600
CACHE_BOOKS = 'public, max-age=600, s-maxage=1800, stale-while-revalidate=21600'
CACHE_BOOK = 'public, max-age=600, s-maxage=3600, stale-while-revalidate=86400'
ITEM = re.compile(r'<item\b[^>]*>(.*?)</item>', re.S | re.I)
_LOADED = {}


def _cdata(s):
    return re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', s or '', flags=re.S)


def _text(s, n=120):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(_cdata(s)))).strip()[:n]


def _secs(s):
    t = (s or '').strip()
    try:
        if ':' in t:
            n = 0.0
            for p in t.split(':'):
                n = n * 60 + float(p)
            return int(n)
        return int(float(t))
    except ValueError:
        return 0


def _tag(block, name):
    m = re.search(r'<%s\b[^>]*>(.*?)</%s>' % (name, name), block, re.S | re.I)
    return m.group(1) if m else ''


def parse_chapters(raw, order='asc'):
    """-> [{n, title, audio, dur}] with the first chapter first, from the text of an RSS feed (an item cut off at the end of the text is left out; one without a sound is left out)."""
    import listen
    rows = []
    for block in ITEM.findall(str(raw or '')):
        m = re.search(r'<enclosure\b[^>]*?\burl\s*=\s*["\']([^"\']+)["\']', block, re.I)
        audio = listen._https(html.unescape(m.group(1))) if m else ''
        title = _text(_tag(block, 'title'))
        if not audio or not title:
            continue
        rows.append({'title': title, 'audio': audio, 'dur': _secs(_tag(block, 'itunes:duration'))})
    if order == 'desc':
        rows.reverse()
    rows = rows[:MAX_CHAPTERS]
    for i, r in enumerate(rows):
        r['n'] = i + 1
    return [{'n': r['n'], 'title': r['title'], 'audio': r['audio'], 'dur': r['dur']} for r in rows]


def load(path=None):
    """-> {'styles': [...], 'books': [...]} from registry/audiobooks.json; an entry that is not valid is left out, a missing or broken file gives nothing."""
    path = path or PATH
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {'styles': [], 'books': []}
    hit = _LOADED.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    cfg = agg.read_json(path, {})
    styles, books, seen = [], [], set()
    for s in (cfg.get('styles') if isinstance(cfg, dict) else None) or []:
        if isinstance(s, dict) and re.fullmatch(r'[a-z]{2,12}', str(s.get('id') or '')) and agg.clean(s.get('name'), 20):
            styles.append({'id': s['id'], 'name': agg.clean(s['name'], 20), 'name_en': agg.clean(s.get('name_en'), 40) or agg.clean(s['name'], 20)})
    ok = {s['id'] for s in styles}
    for b in (cfg.get('books') if isinstance(cfg, dict) else None) or []:
        if not isinstance(b, dict) or not re.fullmatch(r'[a-z0-9-]{2,24}', str(b.get('id') or '')) or b['id'] in seen:
            continue
        if not agg.clean(b.get('title'), 80) or not str(b.get('feed') or '').startswith('https://') or b.get('rights') not in ('public-domain', 'broadcaster') or b.get('order') not in ('asc', 'desc'):
            continue
        seen.add(b['id'])
        cover = str(b.get('cover') or '')
        books.append({'id': b['id'], 'title': agg.clean(b['title'], 80), 'title_en': agg.clean(b.get('title_en'), 100) or agg.clean(b['title'], 80), 'author': agg.clean(b.get('author'), 60), 'narrator': agg.clean(b.get('narrator'), 40),
                      'narrator_en': agg.clean(b.get('narrator_en'), 60) or agg.clean(b.get('narrator'), 40), 'lang': 'zh' if b.get('lang') == 'zh' else 'en', 'style': [x for x in (b.get('style') or []) if x in ok][:3],
                      'rights': b['rights'], 'source': agg.clean(b.get('source'), 30), 'feed': b['feed'], 'order': b['order'], 'cover': cover if cover.startswith('https://') else '',
                      'summary': agg.clean(b.get('summary'), 200), 'n': max(0, int(b.get('n') or 0)), 'secs': max(0, int(b.get('secs') or 0)), 'hot': max(0, int(b.get('hot') or 0)),
                      'url': str(b.get('url') or '') if str(b.get('url') or '').startswith('https://') else ''})
    out = {'styles': styles, 'books': books}
    _LOADED[path] = (mt, out)
    return out


def public(b, lang='zh'):
    return {'id': b['id'], 'title': b['title'], 'title_en': b['title_en'], 'author': b['author'], 'narrator': b['narrator'] if lang != 'en' else b['narrator_en'], 'lang': b['lang'], 'style': b['style'], 'rights': b['rights'],
            'source': b['source'], 'cover': b['cover'], 'summary': b['summary'], 'n': b['n'], 'secs': b['secs'], 'hot': b['hot'], 'url': b['url']}


def handle_books(lang='zh'):
    import listen
    cfg = load()
    if not cfg['books']:
        return listen._fail(404, 'none', '现在没有有声书', 'There are no audiobooks right now')
    books = sorted(cfg['books'], key=lambda b: (b['hot'] or 10 ** 6))
    return listen._ok({'styles': cfg['styles'], 'books': [public(b, lang) for b in books]}, CACHE_BOOKS)


def handle_book(book_id, now, lang='zh'):
    import listen
    book = next((b for b in load()['books'] if b['id'] == str(book_id)), None)
    if book is None:
        return listen._fail(404, 'none', '没有这本书', 'No such book')
    stale = listen.STATS['stale']
    try:
        chapters = listen.pooled('ab:' + book['id'], now, lambda: _read(book))
    except agg.Refused:
        return listen._fail(502, 'down', '这本书现在读不到', 'This book cannot be read right now')
    if not chapters:
        return listen._fail(502, 'empty', '这本书现在读不到章节', 'No chapter of this book can be read right now')
    return listen._ok({**({'stale': True} if listen.STATS['stale'] > stale else {}), 'book': {**public(book, lang), 'n': len(chapters), 'secs': sum(c['dur'] for c in chapters) or book['secs'], 'chapters': chapters}}, CACHE_BOOK)


def _read(book):
    import listen
    chapters = parse_chapters(listen._get(book['feed']), book['order'])
    if not chapters:
        raise agg.Refused('读不到这本书的章节', 'The chapters of this book cannot be read')
    return chapters
