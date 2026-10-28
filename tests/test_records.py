"""Titles, authors and summaries that repeat (similar entries), catalogue cards (records: no file, no necessary address), and the checks of the public function for them.

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
from test_aggregate import Base, event, feed_site  # noqa: E402
from test_submission import SubBase, canned_probe  # noqa: E402

ABOUT_A = '本书从需求预测讲到库存策略，再讲到采购谈判与供应商管理，适合刚入行的计划员阅读，每章都有案例'
ABOUT_B = '本书从需求预测讲到库存策略，再讲到采购谈判与供应商管理，适合刚入行的计划员阅读，每章都有案例。'   # a full stop more
ABOUT_C = '这是一本讲现金流与融资租赁的书，作者结合多年的投行经验，分析了制造业企业扩张时常见的资金陷阱'
ABOUT_LONG = ABOUT_A + '。' + ABOUT_C + '。' + ABOUT_A.replace('需求预测', '销售预测') + '。' + ABOUT_C.replace('制造业', '零售业') + '。'          # longer than the 160 characters the directory keeps


class TestSimilar(unittest.TestCase):
    def test_norm_words_ignores_spacing_punctuation_brackets_and_width(self):
        self.assertEqual(agg.norm_words('《供应链 管理》（第3版）'), agg.norm_words('供应链管理 第３版'))
        self.assertEqual(agg.norm_words('Lean-Production!'), 'leanproduction')

    def test_summary_similarity(self):
        self.assertGreaterEqual(agg.summary_similarity(ABOUT_A, ABOUT_B), 0.95)
        self.assertLess(agg.summary_similarity(ABOUT_A, ABOUT_C), 0.2)
        self.assertEqual(agg.summary_similarity('太短', '太短'), 0.0)                       # too short to say anything
        self.assertGreater(len(ABOUT_LONG), 160)
        self.assertEqual(agg.summary_similarity(ABOUT_LONG, ABOUT_LONG + '后面再多写很多别的内容' * 20), 1.0)   # the directory keeps 160 characters: what comes after them is not compared
        self.assertLess(agg.summary_similarity(ABOUT_LONG, ABOUT_A), 0.5)                    # a text that merely begins like a short one is not the same text

    def test_the_rules(self):
        a = {'title': '精益生产读书笔记', 'author': '小明', 'summary': ABOUT_A}
        same_summary = {'title': '另一个标题', 'author': '小红', 'summary': ABOUT_B}
        self.assertEqual(agg.similar_to(a, same_summary), 'summary')                         # an almost identical summary is enough, whoever wrote it
        same_title = {'title': '《精益生产读书笔记》', 'author': '小明', 'summary': ABOUT_C}
        self.assertEqual(agg.similar_to(a, same_title), 'title')                              # the same title and the same author
        self.assertEqual(agg.similar_to(a, {**same_title, 'author': '别人'}), '')               # another author: another book with the same title
        self.assertEqual(agg.similar_to(a, {**same_title, 'author': ''}), '')                 # author unknown, and the summaries speak against it
        self.assertEqual(agg.similar_to(a, {**same_title, 'author': '', 'summary': ''}), 'title')   # author unknown, nothing speaks against it
        self.assertEqual(agg.similar_to({'title': 'Home', 'summary': ''}, {'title': 'home', 'summary': ''}), '')                    # a title that says nothing is no reason
        self.assertEqual(agg.similar_to({'title': '库存', 'summary': ''}, {'title': '库存', 'summary': ''}), '')                      # and too short a title neither

    def test_find_similar_looks_at_one_level(self):
        pool = [{'level': 'shelf', 'id': 's1', 'title': '精益生产读书笔记', 'author': '', 'summary': ''}, {'level': 'item', 'id': 's2', 'title': '别的', 'author': '', 'summary': ABOUT_A}]
        cand = {'title': '精益生产读书笔记', 'author': '', 'summary': ABOUT_B}
        self.assertEqual(agg.find_similar(cand, pool, 'shelf')[1], 's1')
        self.assertEqual(agg.find_similar(cand, pool, 'item')[:2], ('summary', 's2'))
        self.assertIsNone(agg.find_similar({'title': 'x' * 6, 'summary': ''}, pool))


class TestUnreadAddresses(Base):
    def test_a_site_that_keeps_robots_out_is_listed_unread_and_stays_listed(self):
        self.site.put('/blocked', 'forbidden', status=403)
        r = agg.probe(self.site.url + '/blocked')
        self.assertTrue(r['unread'])
        self.assertEqual((r['entry']['kind'], r['entry']['sub'], r['items'][0]['title']), ('link', 'page', 'blocked'))
        self.assertIn('403', r['warnings'][0][0])
        e, _ = agg.approve(self.site.url + '/blocked')                                       # what is kept: the address and a title guessed from it
        self.assertTrue(e['bare'])
        self.assertEqual(agg.load_snapshot(e['id'])['items'][0]['url'], self.site.url + '/blocked')
        self.quiet(agg.fetch_all)                                                            # the daily read fails again, and that is no failure
        snap = agg.load_snapshot(e['id'])
        self.assertEqual((snap['status'], snap['fails'], len(snap['items'])), ('ok', 0, 1))
        self.site.put('/blocked', '<html><head><title>Now readable</title></head><body>x</body></html>')
        self.quiet(agg.fetch_all)                                                            # and once it can be read, it is read
        self.assertEqual(agg.load_snapshot(e['id'])['items'][0]['title'], 'Now readable')

    def test_a_missing_page_is_still_refused(self):
        with self.assertRaises(agg.Refused):
            agg.probe(self.site.url + '/nothing-here')

    def test_a_page_of_a_listed_site_is_its_own_link(self):
        """A sub-directory of a listed site is not "the site": only the same address is a duplicate."""
        feed_site(self.site)
        agg.approve(self.site.url + '/feed.xml')
        self.site.put('/section/', '<html><head><title>A section</title></head><body>x</body></html>')
        r = agg.probe(self.site.url + '/section/')              # example.com/blog/ looks like a home page, whose feed is then looked for: it is the feed of the whole listed site
        self.assertEqual((r['entry']['kind'], r['entry']['sub'], r['duplicate']), ('link', 'page', None))
        self.assertTrue(any('整个网站的订阅源' in zh for zh, en in r['warnings']))
        self.assertEqual(agg.probe(self.site.url + '/feed.xml')['duplicate'][0], 'shelf')     # the feed of the site itself is still the listed shelf


class TestRecords(Base):
    SUB = {'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'tags': ['书', '供应链'], 'url': ''}

    def test_the_limits(self):
        for bad in ({'title': ''}, {'summary': '太短'}, {'summary': '长' * 1001}, {'title': 'x' * 121}, {'author': 'x' * 41}, {'url': 'ftp://plain.example/x'}, {'url': 'https://127.0.0.1/x'}):
            with self.assertRaises(agg.Refused, msg=bad):
                agg.check_record({**self.SUB, **bad})
        info = agg.check_record(self.SUB)
        self.assertEqual((info['title'], info['author'], info['url']), ('精益生产', '小明', ''))

    def test_an_address_is_optional_and_checked_when_there(self):
        agg.ALLOW_LOCAL = False
        info = agg.check_record({**self.SUB, 'url': 'https://shop.example.org/book/1?utm_source=x'})
        self.assertEqual(info['url'], 'https://shop.example.org/book/1')

    def test_publish_makes_a_page_and_an_item_of_the_type_book(self):
        name, info = agg.publish_record({**self.SUB, 'url': 'https://shop.example.org/book/1'}, 31)
        with open(os.path.join(agg.posts_dir(), name + '.md'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('kind: record', text)
        p, = agg.load_posts()
        self.assertEqual((p['kind'], p['author'], p['url'], p['body'].strip()), ('record', '小明', 'https://shop.example.org/book/1', ABOUT_A))
        os.environ['SHELF_BASE_URL'] = 'https://dir.example/'
        self.addCleanup(os.environ.pop, 'SHELF_BASE_URL', None)
        files = agg.build()
        d = json.loads(files['directory.json'])
        row = next(i for i in d['items'] if i['shelf'] == 'posts')
        self.assertEqual((row['type'], row['author'], row['title']), ('book', '小明', '精益生产'))
        page = files['p/%s/index.html' % name]
        self.assertIn('书籍', page)
        self.assertIn('rel="nofollow ugc noopener noreferrer"', page)
        self.assertNotIn('<script', page)

    def test_the_same_card_twice_is_refused(self):
        agg.publish_record(self.SUB, 31)
        with self.assertRaises(agg.Refused) as cm:
            agg.check_record({**self.SUB, 'title': '《精益生产》', 'summary': '换一句不同的摘要，看看同标题同作者是不是被拦下'})
        self.assertIn('已经提交过', str(cm.exception))
        agg.check_record({**self.SUB, 'author': '另一个人'})                                  # the same title by someone else is another book

    def test_text_in_the_card_is_data_and_comes_out_escaped(self):
        name, info = agg.publish_record({**self.SUB, 'title': 'T<script>x()</script>', 'summary': '一句话 <img src=x onerror=alert(1)> 的摘要，够长了'}, 7)
        page = agg.post_page(agg.load_posts()[0], agg.config(), '')
        self.assertNotIn('<script>x()', page)
        self.assertNotIn('<img', page)


def record_event(number=31, title='精益生产', author='小明', summary=ABOUT_A, url='', rights='我有权公开这些信息 / I have the right to publish this information'):
    body = '### 标题\n\n%s\n\n### 作者（可选）\n\n%s\n\n### 标签（可选）\n\n书\n\n### 摘要\n\n%s\n\n### 网址（可选）\n\n%s\n\n### 权利确认\n\n%s\n' % (title, author or '_No response_', summary, url or '_No response_', rights)
    return {'issue': {'number': number, 'body': body}}


class TestRecordIssues(Base):
    def test_the_issue_is_understood(self):
        s = agg.parse_issue(record_event(url='https://shop.example.org/b'))
        self.assertEqual((s['kind'], s['title'], s['author'], s['summary'], s['url'], s['rights']), ('record', '精益生产', '小明', ABOUT_A, 'https://shop.example.org/b', True))
        self.assertEqual(agg.parse_issue(record_event())['url'], '')                            # no address: none (not the text of the summary)

    def test_a_post_that_has_form_headings_in_its_text_stays_one_post(self):
        body = '### 标题\n\nT\n\n### 正文\n\n第一段\n\n### 摘要\n\n这一行是文章里的小标题\n\n### Title\n\n又一个\n\n### 权利确认\n\n我是作者并同意公开发布\n'
        s = agg.parse_issue({'issue': {'number': 5, 'body': body}})
        self.assertEqual(s['kind'], 'post')
        self.assertIn('这一行是文章里的小标题', s['body'])
        self.assertTrue(s['rights'])

    def test_check_approve_and_reject(self):
        res = agg.handle_issue(record_event(), 'check')
        self.assertTrue(res['ok'], res['comment'])
        self.assertEqual((res['add'], res['close']), (['checked'], False))
        self.assertFalse(os.path.exists(agg.posts_dir()))                                      # nothing is written before the approval
        res = agg.handle_issue(record_event(), 'approve')
        self.assertEqual((res['ok'], res['add'], res['close']), (True, ['listed'], True))
        self.assertEqual(len(agg.load_posts()), 1)
        res = agg.handle_issue(record_event(number=32), 'check')                               # the same card again
        self.assertFalse(res['ok'])
        self.assertEqual(res['add'], ['invalid'])
        res = agg.handle_issue(record_event(number=33, title='别的书', author='别人', summary=ABOUT_C), 'reject')
        self.assertEqual((res['add'], res['redact']), (['rejected'], True))
        self.assertTrue(agg.rejected(agg.record_key('别的书', '别人')))
        res = agg.handle_issue(record_event(number=34, title='别的书', author='别人', summary=ABOUT_C), 'check')
        self.assertIn('暂时不能收录', res['comment'])

    def test_a_similar_card_is_marked_not_refused_and_the_maintainer_can_approve_it(self):
        agg.publish_record({'title': '另一本书', 'author': '小红', 'summary': ABOUT_A, 'tags': [], 'url': ''}, 30)
        res = agg.handle_issue(record_event(title='精益生产', author='小明', summary=ABOUT_B), 'check')
        self.assertEqual((res['ok'], res['add']), (False, ['similar']))
        self.assertIn('疑似重复', res['comment'])
        self.assertIn('另一本书', res['comment'])
        res = agg.handle_issue(record_event(title='精益生产', author='小明', summary=ABOUT_B), 'approve')       # the maintainer's decision stands
        self.assertEqual((res['ok'], res['add']), (True, ['listed']))

    def test_an_address_of_a_listed_site_in_a_card_is_a_duplicate(self):
        agg.write_json(agg.reg_path(), {'shelves': [{'id': 'shop', 'kind': 'feed', 'url': 'https://shop.example.org/', 'source': 'https://shop.example.org/feed.xml', 'name': 'Shop', 'tags': [], 'added': '2026-10-14'}]})
        agg.ALLOW_LOCAL = False
        res = agg.handle_issue(record_event(url='https://shop.example.org/'), 'check')
        self.assertEqual(res['add'], ['duplicate'])


class TestPostsAndAddressesAreCompared(Base):
    def test_a_post_that_looks_like_a_listed_one_is_marked(self):
        agg.publish_record({'title': '一本书', 'author': '小明', 'summary': ABOUT_LONG, 'tags': [], 'url': ''}, 30)
        body = ABOUT_LONG + '\n\n' + '再补一些别的文字，让正文够长，超过最短长度的限制，这样才能通过检查。' * 2          # it begins with the same 160 characters
        ev = {'issue': {'number': 8, 'body': '### 标题\n\n一篇文章\n\n### 作者（可选）\n\n小红\n\n### 正文\n\n%s\n\n### 权利确认\n\n我是作者并同意公开发布\n' % body}}
        res = agg.handle_issue(ev, 'check')
        self.assertEqual(res['add'], ['similar'], res['comment'])

    def test_an_address_that_reads_like_a_listed_shelf_is_marked(self):
        feed_site(self.site, title='Operations Notes', posts=(('One', '2026-10-01'),))
        e, _ = agg.approve(self.site.url + '/feed.xml')
        self.site.put('/other.xml', self.site.routes['/feed.xml'][2], 'application/rss+xml')           # the same blog, another address
        res = agg.handle_issue(event(self.site.url + '/other.xml'), 'check')
        self.assertEqual((res['ok'], res['add']), (False, ['similar']), res['comment'])
        self.assertIn('Operations Notes', res['comment'])
        res = agg.handle_issue(event(self.site.url + '/other.xml'), 'approve')                         # still the maintainer's call
        self.assertEqual(res['add'], ['listed'])


class TestFunction(SubBase):
    REC = {'kind': 'record', 'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'tags': '书, 供应链', 'url': '', 'rights': True}

    def test_a_card_becomes_an_issue_the_pipeline_understands(self):
        st, body = self.send(self.REC)
        self.assertEqual((st, body['ok'], body['kind']), (200, True, 'record'))
        issue, = self.created()
        self.assertEqual((issue['labels'], issue['title']), (['post-submission'], '[书籍] 精益生产'))
        s = agg.parse_issue({'issue': {'number': 41, 'body': issue['body']}})
        self.assertEqual((s['kind'], s['title'], s['author'], s['summary'], s['tags'], s['url'], s['rights']), ('record', '精益生产', '小明', ABOUT_A, ['书', '供应链'], '', True))
        self.assertIn('ip:' + sub.ip_hash('1.2.3.4', 'salt'), issue['body'])

    def test_the_tick_the_limits_and_the_reader_address(self):
        self.assertEqual(self.send({**self.REC, 'rights': False})[1]['error'], 'rights')
        for bad in ({'title': ''}, {'summary': '短'}, {'url': 'ftp://plain.example/x'}, {'url': 'https://127.0.0.1/'}):
            self.assertEqual(self.send({**self.REC, **bad})[1]['error'], 'record', bad)
        self.assertEqual(self.send({**self.REC, 'url': 'https://books.example.org/#read/local%3Aabc'})[1]['error'], 'reader')
        self.assertEqual(self.created(), [])

    def test_the_check_shows_the_card_and_creates_nothing(self):
        st, body = self.send({**self.REC, 'kind': 'check', 'what': 'record', 'rights': False})
        self.assertEqual((st, body['status'], body['result']['what'], body['result']['title']), (200, 'checked', 'record', '精益生产'))
        self.assertEqual(self.created(), [])

    def test_the_directory_has_something_like_it(self):
        self.directory({'items': [{'url': 'https://dir.example/p/x/', 'shelf': 'posts', 'title': '《精益生产》', 'author': '小明', 'summary': ABOUT_C, 'type': 'book'}]})
        st, body = self.send(self.REC)
        self.assertEqual((st, body['error']), (409, 'similar'))
        self.assertIn('精益生产', body['message'])
        self.directory({'items': [{'url': 'https://dir.example/p/y/', 'shelf': 'posts', 'title': '别的', 'author': '别人', 'summary': ABOUT_B, 'type': 'book'}]})
        self.assertEqual(self.send(self.REC)[1]['error'], 'similar')                            # the summary is almost the same
        self.directory({'items': [{'url': 'https://dir.example/p/z/', 'shelf': 'posts', 'title': '精益生产', 'author': '另一个人', 'summary': ABOUT_C, 'type': 'book'}]})
        self.assertEqual(self.send(self.REC)[0], 200)                                           # the same title, another author: another book

    def test_a_waiting_submission_has_something_like_it(self):
        waiting = sub.record_issue(agg.check_record({'title': '精益生产', 'author': '小明', 'summary': ABOUT_A, 'tags': [], 'url': ''}), 'abc')
        self.open_issues([], [{'number': 5, 'body': waiting, 'created_at': '2026-10-14T00:00:00Z'}])
        st, body = self.send(self.REC)
        self.assertEqual((st, body['error']), (409, 'pending'))

    def test_a_card_with_an_address_is_also_a_question_of_the_address(self):
        self.directory({'shelves': [{'id': 'shop', 'home': 'https://shop.example.org/', 'feed': '', 'kind': 'feed'}]})
        st, body = self.send({**self.REC, 'url': 'http://www.shop.example.org/?utm_source=x'})
        self.assertEqual((st, body['error']), (409, 'listed'))
        self.assertEqual(self.send({**self.REC, 'url': 'https://shop.example.org/book/1'})[0], 200)     # a page of the listed site is its own link

    def test_a_post_like_a_listed_one_is_refused(self):
        self.directory({'items': [{'url': 'https://dir.example/p/x/', 'shelf': 'posts', 'title': '读者的文章', 'author': '小明', 'summary': 'x', 'type': 'article'}]})
        from test_posts_dedupe import POST_BODY
        st, body = self.send({'kind': 'post', 'title': '读者的文章', 'author': '小明', 'body': POST_BODY, 'rights': True})
        self.assertEqual((st, body['error']), (409, 'similar'))


class TestCheckOfAddresses(SubBase):
    def setUp(self):
        super().setUp()
        self.real_probe = sub.probe_for_check
        self.addCleanup(setattr, sub, 'probe_for_check', self.real_probe)
        sub._CHECKS.clear()

    def test_an_address_that_reads_like_a_listed_shelf_is_refused_at_the_gate(self):
        sub.probe_for_check = lambda url: canned_probe()
        self.directory({'shelves': [{'id': 'other', 'name': 'Example blog', 'description': 'About it', 'home': 'https://other.example.org/', 'feed': '', 'kind': 'feed'}]})
        st, body = self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})
        self.assertEqual((st, body['error']), (409, 'similar'))
        self.assertIn('Example blog', body['message'])
        self.directory({})
        self.assertEqual(self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/'})[0], 200)

    def test_an_address_that_could_not_be_read_goes_through_with_a_warning(self):
        unread = canned_probe(warnings=[('读不到这个页面（HTTP 403），请维护者手动确认', 'Could not be read')])
        unread['unread'] = True
        sub.probe_for_check = lambda url: unread
        self.directory({'shelves': [{'id': 'other', 'name': 'Example blog', 'description': 'About it', 'home': 'https://other.example.org/', 'feed': '', 'kind': 'feed'}]})
        st, body = self.send({'kind': 'check', 'what': 'url', 'url': 'https://blog.example.org/section/'})
        self.assertEqual((st, body['result']['unread']), (200, True))                           # what is not read is not compared
        self.assertTrue(body['result']['warnings'])


if __name__ == '__main__':
    unittest.main()
