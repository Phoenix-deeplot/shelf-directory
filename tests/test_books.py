"""The picked books (registry/books.json, aggregate.check_book / load_books / the shelf "精选图书" in build) and the real list: well-made, free to read, many fields.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
from test_aggregate import Base  # noqa: E402

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'registry', 'books.json')


def book(**kw):
    b = {'id': 'tea', 'title': '茶之书', 'author': '冈仓天心', 'lang': 'zh', 'kind': 'essay', 'tags': ['散文', '艺术'], 'summary': '一本讲茶道与东方美学的小书。', 'license': 'public-domain', 'source': 'example.org',
         'read': 'https://example.org/tea'}
    b.update(kw)
    return b


class TestOneBook(unittest.TestCase):
    def test_a_well_made_entry_is_a_book_of_the_directory(self):
        it, why = agg.check_book(book())
        self.assertEqual(why, '')
        self.assertEqual((it['type'], it['url'], it['title'], it['author'], it['lang'], it['license'], it['date']), ('book', 'https://example.org/tea', '茶之书', '冈仓天心', 'zh', 'public-domain', ''))
        self.assertEqual(it['tags'], ['散文', '艺术'])                                                      # (no chapters, not marked readable: opened on its own site)
        self.assertNotIn('ch', it)

    def test_chapters_or_readable_mean_it_is_read_in_the_page(self):
        ch = {'s': 'zh.wikisource.org', 'p': '紅樓夢/', 'n': ['第001回', ' 第002回 ']}
        it, _ = agg.check_book(book(ch=ch))
        self.assertEqual((it['tags'][-1], it['ch']), ('在线书籍', {'s': 'zh.wikisource.org', 'p': '紅樓夢/', 'n': ['第001回', '第002回']}))
        it, _ = agg.check_book(book(readable=True, tags=['a', 'b', 'c', 'd', 'e', 'f']))
        self.assertEqual((len(it['tags']), it['tags'][-1]), (6, '在线书籍'))                                 # (the tag that says it is always there)
        it, _ = agg.check_book(book(readable=True, tags=['在线书籍']))
        self.assertEqual(it['tags'], ['在线书籍'])

    def test_a_long_summary_keeps_its_first_part_in_the_card_and_the_rest_in_the_feeds(self):
        it, _ = agg.check_book(book(summary='这是一句话。' * 60))
        self.assertEqual(len(it['summary']), agg.SUMMARY + 1)
        self.assertTrue(len(it['abstract']) > len(it['summary']))

    def test_what_is_not_right_is_left_out_and_says_why(self):
        bad = [None, 'x', {}, book(id='Tea'), book(id=''), book(id='a b'), book(title=''), book(summary=''), book(lang='fr'), book(kind='video'), book(license='all-rights-reserved'), book(license=''),
               book(read='http://example.org/tea'), book(read='tea'), book(read=''),
               book(ch={'s': 'zh.wikisource.org', 'p': 'x/', 'n': []}), book(ch={'s': 'not a host', 'p': 'x/', 'n': ['a']}), book(ch={'s': 'zh.wikisource.org', 'p': 'x/', 'n': ['a'] * 301}),
               book(ch={'s': 'zh.wikisource.org', 'p': 'x/', 'n': [1]}), book(ch={'s': 'zh.wikisource.org', 'p': 'x/', 'n': ['a\nb']}), book(ch='zh.wikisource.org')]
        for b in bad:
            it, why = agg.check_book(b)
            self.assertIsNone(it, b)
            self.assertTrue(why, b)


class TestShelf(Base):
    def write(self, books):
        os.makedirs(os.path.join(self.tmp, 'registry'), exist_ok=True)
        with open(os.path.join(self.tmp, 'registry', 'books.json'), 'w', encoding='utf-8') as f:
            json.dump(books, f, ensure_ascii=False)

    def test_the_picked_books_are_a_shelf_of_their_own_and_it_is_featured(self):
        self.write([book(), book(id='hlm', title='红楼梦', read='https://zh.wikisource.org/wiki/紅樓夢', ch={'s': 'zh.wikisource.org', 'p': '紅樓夢/', 'n': ['第001回', '第002回']}), book(id='bad', license='x')])
        log = []
        orig = agg.load_books
        agg.load_books = lambda log=log.append: orig(log)
        try:
            d = json.loads(agg.build()['directory.json'])
        finally:
            agg.load_books = orig
        shelf = [s for s in d['shelves'] if s['id'] == 'books'][0]
        self.assertEqual((shelf['name'], shelf['n'], shelf.get('featured'), shelf['kind']), ('精选图书', 2, True, 'books'))
        by = {i['title']: i for i in d['items']}
        self.assertEqual((by['红楼梦']['type'], by['红楼梦']['shelf'], by['红楼梦']['ch']['p'], by['红楼梦']['license'], '在线书籍' in by['红楼梦']['tags']), ('book', 'books', '紅樓夢/', 'public-domain', True))
        self.assertEqual((by['红楼梦']['bid'], by['茶之书']['bid']), ('hlm', 'tea'))                       # (the id in the list: the page asks /api/book for a chapter of THAT book)
        self.assertNotIn('ch', by['茶之书'])
        self.assertTrue(all('abstract' not in i for i in d['items']))                                       # (the longer text is for the feeds only)
        self.assertTrue(any('bad' in m and 'left out' in m for m in log), log)

    def test_without_a_list_there_is_no_shelf_and_a_list_that_is_not_a_list_is_ignored(self):
        self.assertEqual([s for s in json.loads(agg.build()['directory.json'])['shelves'] if s['id'] == 'books'], [])
        self.write({'not': 'a list'})
        self.assertEqual([s for s in json.loads(agg.build()['directory.json'])['shelves'] if s['id'] == 'books'], [])

    def test_two_books_with_the_same_id_are_one_book(self):
        self.write([book(), book(title='另一本')])
        items = agg.load_books(lambda m: None)
        self.assertEqual([i['title'] for i in items], ['茶之书'])


class TestTheRealList(unittest.TestCase):
    """registry/books.json as it is in the repository: it is what the site shows, so it is checked as hard as a build would (and more)."""

    @classmethod
    def setUpClass(cls):
        with open(REAL, encoding='utf-8') as f:
            cls.raw = json.load(f)

    def test_every_entry_is_well_made_and_nothing_is_there_twice(self):
        self.assertTrue(isinstance(self.raw, list) and len(self.raw) >= 40, len(self.raw))
        ids, urls = set(), set()
        for b in self.raw:
            it, why = agg.check_book(b)
            self.assertIsNotNone(it, '%s: %s' % (b.get('id'), why))
            self.assertNotIn(b['id'], ids)
            self.assertNotIn(it['url'], urls, b['id'])
            ids.add(b['id'])
            urls.add(it['url'])
            self.assertTrue(b.get('source'), b['id'])

    def test_it_covers_many_fields_in_both_languages_with_magazines_and_journals(self):
        kinds = {b['kind'] for b in self.raw}
        self.assertTrue({'novel', 'classic', 'magazine', 'journal', 'textbook', 'poetry'} <= kinds, kinds)
        self.assertTrue(sum(b['lang'] == 'zh' for b in self.raw) >= 15 and sum(b['lang'] == 'en' for b in self.raw) >= 15)
        tags = {t for b in self.raw for t in b.get('tags', [])}
        for want in ('小说', '科学', '技术', '哲学', '杂志', '期刊', '诗歌', '历史', '艺术'):
            self.assertIn(want, tags)

    def test_what_is_to_be_read_in_the_page_has_chapters_that_are_there(self):
        withch = [b for b in self.raw if b.get('ch')]
        self.assertTrue(len(withch) >= 20, len(withch))
        for b in withch:
            self.assertTrue(2 <= len(b['ch']['n']) <= agg.BOOK_CH_MAX, b['id'])
            self.assertEqual(len(set(b['ch']['n'])), len(b['ch']['n']), b['id'])                          # (no chapter twice)
            self.assertTrue(b['read'].startswith('https://%s/wiki/' % b['ch']['s']), b['id'])
        self.assertTrue(sum(1 for b in self.raw if b.get('readable')) >= 3)

    def test_the_whole_list_in_the_directory_is_small(self):
        items = [agg.check_book(b)[0] for b in self.raw]
        self.assertTrue(len(json.dumps(items, ensure_ascii=False)) < 200_000)                              # (the chapters are names only: the directory must stay light)


if __name__ == '__main__':
    unittest.main()
