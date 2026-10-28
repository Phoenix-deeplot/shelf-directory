"""A book's author, pages and contents (what its owner's doc.json says): read from a feed, kept in directory.json, written on a book entry and its page, asked for by the submit form.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import submission as sub  # noqa: E402
from test_aggregate import Base  # noqa: E402
from test_records import ABOUT_A  # noqa: E402

TOC = ['第一章 需求预测', '第二章 库存策略', '第三章 采购谈判']


def book_feed(base='https://a.example'):
    return json.dumps({'version': 'https://jsonfeed.org/version/1.1', 'title': 'A library', 'home_page_url': base + '/', 'language': 'zh-CN', '_shelf': {'spec': 1}, 'items': [
        {'id': '1', 'url': base + '/books/lean.pdf', 'title': '精益生产', 'content_text': '一本讲精益的书', 'authors': [{'name': '小明'}],
         '_shelf': {'type': 'pdf', 'pages': 242, 'toc': TOC + ['  ', '第一章 需求预测']}},
        {'id': '2', 'url': base + '/books/plain.pdf', 'title': '没有目录的', 'content_text': '一份简单的文件', '_shelf': {'type': 'pdf', 'pages': 'many', 'toc': {'not': 'a list'}}},
        {'id': '3', 'url': base + '/books/odd.pdf', 'title': 'Odd', 'content_text': 'x', '_shelf': {'type': 'pdf', 'author': '李雷', 'pages': 1e9, 'toc': ['x%d' % i + 'y' * 200 for i in range(100)]}}]}).encode()


class TestNormalising(unittest.TestCase):
    def test_pages(self):
        self.assertEqual([agg.norm_pages(v) for v in (242, '242', 242.0, '242 页', 0, -3, 100000, None, '', True)], [242, 242, 242, 0, 0, 0, 0, 0, 0, 0])

    def test_contents(self):
        self.assertEqual(agg.norm_toc(TOC + ['  ', TOC[0]]), TOC)                                   # blanks and repeats are dropped
        self.assertEqual(agg.norm_toc('- 一\n* 二\n\n·三\n四'), ['一', '二', '三', '四'])             # text, a heading a line, list marks dropped
        self.assertEqual(agg.norm_toc('1. 第一章'), ['1. 第一章'])                                  # a number belongs to a heading
        self.assertEqual(agg.norm_toc(None), [])
        self.assertEqual(agg.norm_toc({'a': 1}), [])
        self.assertEqual(len(agg.norm_toc(['x%d' % i for i in range(200)])), agg.TOC_MAX)
        self.assertEqual(len(agg.norm_toc(['x' * 500])[0]), agg.TOC_TITLE + 1)                        # cut with an ellipsis


class TestFeed(Base):
    def test_the_feed_says_it(self):
        _, items = agg.parse_feed(book_feed(), {}, 'https://a.example/feed.json')
        one, two, three = items
        self.assertEqual((one['author'], one['pages'], one['toc']), ('小明', 242, TOC))
        self.assertNotIn('author', two)
        self.assertNotIn('pages', two)                                                              # nonsense is not kept
        self.assertNotIn('toc', two)
        self.assertEqual((three['author'], three['pages'] if 'pages' in three else None), ('李雷', None))
        self.assertEqual(len(three['toc']), agg.TOC_MAX)

    def test_it_reaches_directory_json_and_only_there_as_data(self):
        self.site.put('/feed.json', book_feed(self.site.url), 'application/feed+json')
        e, _ = agg.approve(self.site.url + '/feed.json')
        files = agg.build()
        d = json.loads(files['directory.json'])
        row = next(i for i in d['items'] if i['title'] == '精益生产')
        self.assertEqual((row['author'], row['pages'], row['toc']), ('小明', 242, TOC))
        plain = next(i for i in d['items'] if i['title'] == '没有目录的')
        self.assertTrue(all(k not in plain for k in ('author', 'pages', 'toc')))                     # an entry that has none carries none: the file stays small


class TestEntries(Base):
    SUB = {'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'tags': ['书', '供应链'], 'url': '', 'pages': 242, 'toc': TOC}

    def test_the_card_keeps_them_and_its_page_shows_them(self):
        info = agg.check_record(self.SUB)
        self.assertEqual((info['pages'], info['toc']), (242, TOC))
        name, _ = agg.publish_record(self.SUB, 41)
        with open(os.path.join(agg.posts_dir(), name + '.md'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('pages: 242', text)
        self.assertIn('toc: ["第一章 需求预测"', text)
        p, = agg.load_posts()
        self.assertEqual((p['pages'], p['toc']), (242, TOC))
        page = agg.post_page(p, agg.config(), '')
        self.assertIn('242 页', page)
        self.assertIn('<li>第二章 库存策略</li>', page)
        os.environ['SHELF_BASE_URL'] = 'https://dir.example/'
        self.addCleanup(os.environ.pop, 'SHELF_BASE_URL', None)
        row = next(i for i in json.loads(agg.build()['directory.json'])['items'] if i['shelf'] == 'posts')
        self.assertEqual((row['pages'], row['toc']), (242, TOC))

    def test_a_card_without_them_has_none(self):
        info = agg.check_record({k: v for k, v in self.SUB.items() if k not in ('pages', 'toc')})
        self.assertEqual((info['pages'], info['toc']), (0, []))
        name, _ = agg.publish_record({k: v for k, v in self.SUB.items() if k not in ('pages', 'toc')}, 42)
        with open(os.path.join(agg.posts_dir(), name + '.md'), encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn('pages:', text)
        self.assertNotIn('toc:', text)

    def test_text_in_the_contents_is_data(self):
        agg.publish_record({**self.SUB, 'toc': ['<script>x()</script>', '<img src=x onerror=1>']}, 43)
        page = agg.post_page(agg.load_posts()[0], agg.config(), '')
        self.assertNotIn('<script>x()', page)
        self.assertNotIn('<img', page)

    def test_a_blocked_word_in_the_contents_is_refused(self):
        with open(os.path.join(agg.ROOT, 'registry', 'blockwords.txt'), 'w', encoding='utf-8') as f:
            f.write('forbiddenword\n')
        with self.assertRaises(agg.Refused):
            agg.check_record({**self.SUB, 'toc': ['ok', 'a ForbiddenWord inside']})


class TestIssue(Base):
    def issue(self, **k):
        s = {'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'url': '', 'tags': ['书'], 'pages': 242, 'toc': TOC}
        s.update(k)
        return sub.record_issue(s, 'abc123')

    def test_the_form_text_is_understood(self):
        s = agg.parse_issue({'issue': {'number': 9, 'body': self.issue()}})
        self.assertEqual((s['kind'], s['title'], s['pages'], s['toc']), ('record', '精益生产', 242, TOC))
        self.assertEqual(s['summary'], ABOUT_A)                                                       # the new headings do not cut the summary short or leak into it

    def test_without_them_the_form_says_no_response_and_means_none(self):
        s = agg.parse_issue({'issue': {'number': 9, 'body': self.issue(pages=0, toc=[])}})
        self.assertEqual((s['pages'], s['toc']), (0, []))

    def test_a_heading_inside_the_summary_does_not_open_a_field(self):
        s = agg.parse_issue({'issue': {'number': 9, 'body': self.issue(summary=ABOUT_A + '\n### 目录\n- 假的一行')}})
        self.assertEqual(s['toc'], TOC)
        self.assertIn('假的一行', s['summary'])


class TestSubmitFunction(Base):
    def test_the_payload_is_taken_over_and_checked(self):
        s, info, _ = _submit_record({'kind': 'record', 'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'tags': ['书'], 'pages': '242', 'toc': TOC + [7, None]})
        self.assertEqual((s['pages'], s['toc']), (242, TOC + ['7']))
        self.assertEqual((info['pages'], len(info['toc'])), (242, 4))
        r = sub.summarize_record(info)
        self.assertEqual((r['pages'], r['toc']), (242, 4))

    def test_a_bad_value_is_dropped_not_refused(self):
        s, info, _ = _submit_record({'kind': 'record', 'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'pages': 'lots', 'toc': {'x': 1}})
        self.assertEqual((info['pages'], info['toc']), (0, []))


def _submit_record(payload):
    cfg = {'repo': 'o/r', 'token': 't', 'salt': 's', 'max_open': 50, 'per_day': 5}
    old = (sub.load_rejected, sub.load_directory, sub.open_submissions)
    sub.load_rejected, sub.load_directory, sub.open_submissions = (lambda c: set()), (lambda c: {}), (lambda c: [])
    try:
        return sub.submit_record(payload, cfg)
    finally:
        sub.load_rejected, sub.load_directory, sub.open_submissions = old


if __name__ == '__main__':
    unittest.main()
