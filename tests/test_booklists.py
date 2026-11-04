"""The book lists (registry/booklists.json, aggregate.load_booklists and the "booklists" of directory.json) and the real file: every book of it is a picked book.

    python -X utf8 -m unittest tests.test_booklists -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
from test_aggregate import Base  # noqa: E402
from test_books import book  # noqa: E402

REG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'registry')


def lst(**kw):
    l = {'id': 'start', 'name': {'zh': '入门', 'en': 'Start'}, 'blurb': {'zh': '先读这些', 'en': 'Read these first'},
         'books': [{'id': 'a', 'why': {'zh': '短', 'en': 'Short'}}, {'id': 'b', 'why': '一句话'}, 'c']}
    l.update(kw)
    return l


class TestLists(Base):
    def put(self, books, lists):
        os.makedirs(os.path.join(self.tmp, 'registry'), exist_ok=True)
        for name, data in (('books.json', books), ('booklists.json', lists)):
            if data is not None:
                with open(os.path.join(self.tmp, 'registry', name), 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)

    def built(self, books, lists):
        self.put(books, lists)
        log = []
        orig = agg.load_booklists
        agg.load_booklists = lambda items, log=log.append: orig(items, log)
        try:
            d = json.loads(agg.build()['directory.json'])
        finally:
            agg.load_booklists = orig
        return d, log

    def three(self):
        return [book(id=i, title='书' + i, read='https://example.org/' + i) for i in 'abc']

    def test_a_list_names_its_books_by_the_id_of_the_list_of_books_and_the_directory_gives_it_the_ids_of_its_items(self):
        d, log = self.built(self.three(), [lst()])
        by_bid = {i['bid']: i['id'] for i in d['items']}
        l = d['booklists'][0]
        self.assertEqual((l['id'], l['name'], l['blurb']), ('start', {'zh': '入门', 'en': 'Start'}, {'zh': '先读这些', 'en': 'Read these first'}))
        self.assertEqual([b['id'] for b in l['books']], [by_bid['a'], by_bid['b'], by_bid['c']])           # (in the order the owner wrote them)
        self.assertEqual(l['books'][0]['why'], {'zh': '短', 'en': 'Short'})
        self.assertEqual(l['books'][1]['why'], {'zh': '一句话', 'en': '一句话'})                                # (a plain string is for both languages)
        self.assertNotIn('why', l['books'][2])                                                              # (a book may come without a line)
        self.assertEqual(log, [])

    def test_no_file_no_lists_and_a_file_that_is_not_a_list_is_ignored(self):
        d, _ = self.built(self.three(), None)
        self.assertNotIn('booklists', d)
        d, log = self.built(self.three(), {'not': 'a list'})
        self.assertNotIn('booklists', d)
        self.assertTrue(any('must be a list' in m for m in log), log)

    def test_a_book_that_is_not_there_is_left_out_and_said_and_a_list_with_under_two_books_goes(self):
        d, log = self.built(self.three(), [lst(books=['a', 'b', 'nope']), lst(id='one', books=['a', 'nope']), lst(id='none', books=[])])
        self.assertEqual([l['id'] for l in d['booklists']], ['start'])
        self.assertEqual(len(d['booklists'][0]['books']), 2)
        self.assertTrue(any('"nope"' in m and 'start' in m for m in log), log)
        self.assertTrue(any('one' in m and 'fewer than two' in m for m in log), log)
        self.assertTrue(any('none' in m and 'fewer than two' in m for m in log), log)

    def test_a_book_left_out_of_the_list_of_books_is_left_out_of_the_lists_too(self):
        d, log = self.built(self.three() + [book(id='bad', license='x', read='https://example.org/bad')], [lst(books=['a', 'b', 'bad'])])
        self.assertEqual(len(d['booklists'][0]['books']), 2)
        self.assertTrue(any('"bad"' in m for m in log), log)

    def test_what_is_not_right_is_left_out(self):
        bad = [None, 'x', {}, lst(id='Start'), lst(id=''), lst(id='a b'), lst(name=''), lst(name={}), lst(name={'zh': ' '}), lst(books='abc')]
        d, log = self.built(self.three(), bad + [lst(id='fine')])
        self.assertEqual([l['id'] for l in d['booklists']], ['fine'])
        d, log = self.built(self.three(), [lst(), lst()])
        self.assertEqual(len(d['booklists']), 1)
        self.assertTrue(any('twice' in m for m in log), log)

    def test_a_language_that_is_missing_is_filled_from_the_other_and_a_book_listed_twice_is_one(self):
        d, _ = self.built(self.three(), [lst(name={'en': 'Only English'}, blurb='一句话', books=['a', 'a', {'id': 'b', 'why': {'zh': '只有中文'}}])])
        l = d['booklists'][0]
        self.assertEqual((l['name'], l['blurb']), ({'zh': 'Only English', 'en': 'Only English'}, {'zh': '一句话', 'en': '一句话'}))
        self.assertEqual(len(l['books']), 2)
        self.assertEqual(l['books'][1]['why'], {'zh': '只有中文', 'en': '只有中文'})

    def test_a_blurb_is_not_needed_and_the_numbers_are_capped(self):
        d, _ = self.built(self.three(), [lst(blurb='')])
        self.assertNotIn('blurb', d['booklists'][0])
        many = [lst(id='l%d' % n) for n in range(agg.BOOKLIST_MAX + 5)]
        d, _ = self.built(self.three(), many)
        self.assertEqual(len(d['booklists']), agg.BOOKLIST_MAX)
        long = [book(id='b%d' % n, title='书%d' % n, read='https://example.org/%d' % n) for n in range(agg.BOOKLIST_BOOKS_MAX + 10)]
        d, _ = self.built(long, [lst(books=['b%d' % n for n in range(len(long))])])
        self.assertEqual(len(d['booklists'][0]['books']), agg.BOOKLIST_BOOKS_MAX)


class TestTheRealLists(unittest.TestCase):
    """registry/booklists.json as it is in the repository: it is what the page shows above the books, so every book in it must be a picked book."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REG, 'booklists.json'), encoding='utf-8') as f:
            cls.lists = json.load(f)
        with open(os.path.join(REG, 'books.json'), encoding='utf-8') as f:
            cls.books = {b['id']: b for b in json.load(f)}

    def test_there_are_several_and_each_is_well_made(self):
        self.assertTrue(6 <= len(self.lists) <= agg.BOOKLIST_MAX, len(self.lists))
        ids = [l['id'] for l in self.lists]
        self.assertEqual(len(set(ids)), len(ids))
        for l in self.lists:
            self.assertTrue(agg.BOOK_ID.match(l['id']), l['id'])
            for k in ('name', 'blurb'):
                self.assertTrue(l[k].get('zh') and l[k].get('en'), (l['id'], k))
            self.assertTrue(len(l['name']['zh']) <= 12 and len(l['name']['en']) <= 48, l['id'])
            self.assertTrue(3 <= len(l['books']) <= 10, l['id'])

    def test_every_book_is_a_picked_book_once_with_a_line_in_both_languages(self):
        for l in self.lists:
            seen = set()
            for b in l['books']:
                self.assertIn(b['id'], self.books, '%s: %s' % (l['id'], b['id']))
                self.assertNotIn(b['id'], seen, '%s: %s twice' % (l['id'], b['id']))
                seen.add(b['id'])
                self.assertTrue(b['why'].get('zh') and b['why'].get('en'), (l['id'], b['id']))
                self.assertTrue(len(b['why']['zh']) <= 40 and len(b['why']['en']) <= 80, (l['id'], b['id']))

    def test_the_real_build_gives_every_list_all_its_books(self):
        items = []
        for b in self.books.values():
            it, _ = agg.check_book(b)
            it['id'] = 'i-' + b['id']
            items.append(it)
        old = agg.ROOT
        agg.ROOT = os.path.join(REG, '..')
        try:
            out = agg.load_booklists(items, lambda m: self.fail(m))
        finally:
            agg.ROOT = old
        self.assertEqual([l['id'] for l in out], [l['id'] for l in self.lists])
        for a, b in zip(out, self.lists):
            self.assertEqual([x['id'] for x in a['books']], ['i-' + x['id'] for x in b['books']])


if __name__ == '__main__':
    unittest.main()
