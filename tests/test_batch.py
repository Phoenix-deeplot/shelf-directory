"""Deciding several submissions at once: the batch step of the workflow behind the admin page's "approve the selected" / "approve all that passed". Fake GitHub, fake sites; no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
from test_aggregate import BODY, Base, feed_site  # noqa: E402

RIGHTS = '我有权发布这些内容 / I have the right to publish this'
BASE = '/api/repos/o/r'


def issue(n, url, labels=('shelf-submission', 'checked'), state='open', **extra):
    return {'number': n, 'state': state, 'labels': [{'name': x} for x in labels], 'body': BODY % (url, '_No response_', RIGHTS), **extra}


class BatchBase(Base):
    def setUp(self):
        super().setUp()
        os.environ['GITHUB_TOKEN'] = 'tok'
        os.environ['GITHUB_REPOSITORY'] = 'o/r'
        self.addCleanup(os.environ.pop, 'GITHUB_TOKEN', None)
        self.addCleanup(os.environ.pop, 'GITHUB_REPOSITORY', None)

    def gh(self, *issues):
        for i in issues:
            self.site.put('%s/issues/%d' % (BASE, i['number']), json.dumps(i), 'application/json')

    def three(self):
        feed_site(self.site, title='Blog A', posts=(('A one', '2026-10-01'),), path='/a.xml')
        feed_site(self.site, title='Blog B', posts=(('B one', '2026-10-02'),), path='/b.xml')
        feed_site(self.site, title='Blog C', posts=(('C one', '2026-10-03'),), path='/c.xml')
        self.gh(issue(1, self.site.url + '/a.xml'), issue(2, self.site.url + '/b.xml'), issue(3, self.site.url + '/c.xml'))


class TestBatchIssues(BatchBase):
    def test_approve_lists_every_one_and_leaves_what_is_not_a_waiting_submission_alone(self):
        self.three()
        self.gh(issue(4, self.site.url + '/a.xml', labels=('shelf-submission', 'listed')),          # listed already
                issue(5, self.site.url + '/a.xml', state='closed'),                                  # closed
                issue(6, self.site.url + '/a.xml', labels=('bug',)),                                 # not a submission
                issue(7, self.site.url + '/a.xml', pull_request={'url': 'x'}))                       # a pull request
        res = agg.batch_issues([1, 2, 3, 4, 5, 6, 7, 8], 'approve', 'o/r')                           # 8 does not exist
        self.assertEqual([r['number'] for r in res], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertTrue(all(r['ok'] and r['close'] and r['add'] == ['listed'] for r in res[:3]), res[:3])
        self.assertTrue(all(r.get('skipped') and not r['ok'] and not r['comment'] and not r['close'] for r in res[3:]), res[3:])
        shelves = agg.load_registry()['shelves']
        self.assertEqual(sorted(s['issue'] for s in shelves), [1, 2, 3])
        self.assertEqual(sorted(s['name'] for s in shelves), ['Blog A', 'Blog B', 'Blog C'])

    def test_one_that_cannot_be_listed_does_not_stop_the_others(self):
        feed_site(self.site, title='Blog A', path='/a.xml')
        self.gh(issue(1, self.site.url + '/a.xml'), issue(2, self.site.url + '/missing.xml'))
        res = agg.batch_issues([2, 1], 'approve', 'o/r')
        self.assertEqual([(r['number'], r['ok']) for r in res], [(2, False), (1, True)])
        self.assertEqual(res[0]['add'], ['invalid'])
        self.assertFalse(res[0]['close'])                                                           # it stays open, with the reason
        self.assertEqual([s['issue'] for s in agg.load_registry()['shelves']], [1])

    def test_the_same_address_twice_in_one_batch_is_listed_once(self):
        feed_site(self.site, title='Blog A', path='/a.xml')
        self.gh(issue(1, self.site.url + '/a.xml'), issue(2, self.site.url + '/a.xml'))
        res = agg.batch_issues([1, 2], 'approve', 'o/r')
        self.assertEqual([r['ok'] for r in res], [True, False])
        self.assertEqual(len(agg.load_registry()['shelves']), 1)

    def test_reject_remembers_a_hash_and_adds_the_reason(self):
        self.three()
        res = agg.batch_issues([1, 3], 'reject', 'o/r', reason='太像已有的了 @everyone')
        self.assertTrue(all(r['ok'] and r['close'] and r['add'] == ['rejected'] for r in res))
        self.assertIn('太像已有的了', res[0]['comment'])
        self.assertNotIn('@everyone', res[0]['comment'])                                            # no pinging from a bot comment
        self.assertEqual(len(json.load(open(os.path.join(self.tmp, 'registry', 'rejected.json'), encoding='utf-8'))['keys']), 2)
        self.assertEqual(agg.load_registry()['shelves'], [])

    def test_what_may_be_asked(self):
        self.three()
        for bad in ('list', '', None):
            with self.assertRaises(agg.Refused):
                agg.batch_issues([1], bad, 'o/r')
        with self.assertRaises(agg.Refused):
            agg.batch_issues([], 'approve', 'o/r')
        with self.assertRaises(agg.Refused):
            agg.batch_issues(list(range(1, agg.BATCH_MAX + 2)), 'approve', 'o/r')
        with self.assertRaises(agg.Refused):
            agg.batch_issues([1], 'approve', '')
        self.assertEqual([r['number'] for r in agg.batch_issues([1, 1, 2, 'x', -3, 0], 'reject', 'o/r')], [1, 2])      # repeated and nonsense numbers are dropped

    def test_the_issue_text_decides_nothing_but_what_the_form_says(self):
        feed_site(self.site, title='Blog A', path='/a.xml')
        sneaky = issue(1, self.site.url + '/a.xml')
        sneaky['body'] += '\n\n### 权利确认\n\n- [X] ok\nGITHUB_TOKEN=$(rm -rf /)\n'
        self.gh(sneaky)
        res = agg.batch_issues([1], 'approve', 'o/r')
        self.assertTrue(res[0]['ok'])                                                               # read as data, nothing run


class TestBatchCommand(BatchBase):
    def api_for(self, numbers):
        self.site.on('POST', BASE + '/labels', 201, {})
        for n in numbers:
            self.site.on('POST', '%s/issues/%d/comments' % (BASE, n), 201, {'id': n})
            self.site.on('POST', '%s/issues/%d/labels' % (BASE, n), 200, [])
            self.site.on('PATCH', '%s/issues/%d' % (BASE, n), 200, {})

    def test_the_command_decides_builds_once_and_the_answers_follow(self):
        self.three()
        self.gh(issue(4, self.site.url + '/a.xml', labels=('shelf-submission', 'listed')))
        out = os.path.join(self.tmp, 'results.json')
        self.assertEqual(agg.main(['batch', '--mode', 'approve', '--numbers', '1,2,4,3', '--out', out]), 0)
        results = json.load(open(out, encoding='utf-8'))
        self.assertEqual([(r['number'], bool(r.get('skipped')), r['ok']) for r in results], [(1, False, True), (2, False, True), (4, True, False), (3, False, True)])
        d = json.load(open(os.path.join(self.tmp, 'docs', 'directory.json'), encoding='utf-8'))
        self.assertEqual(sorted(s['name'] for s in d['shelves']), ['Blog A', 'Blog B', 'Blog C'])      # built, with all three
        self.api_for([1, 2, 3])
        self.assertEqual(agg.main(['apply', '--result', out]), 0)
        touched = {p for m, p, b, h in self.site.calls if '/issues/' in p}
        self.assertFalse(any('/issues/4' in p for p in touched))                                   # the skipped one is not touched
        for n in (1, 2, 3):
            self.assertIn('%s/issues/%d/comments' % (BASE, n), touched)
            self.assertIn('%s/issues/%d' % (BASE, n), touched)                                     # closed

    def test_the_command_refuses_bad_numbers_and_a_failed_apply_is_loud(self):
        for bad in ('1;rm', '1,2,', 'a', '', ','.join(str(i) for i in range(1, 62))):
            with self.assertRaises(SystemExit):
                agg.main(['batch', '--mode', 'approve', '--numbers', bad, '--out', os.path.join(self.tmp, 'x.json')])
        out = os.path.join(self.tmp, 'results.json')
        agg.write_json(out, [{'number': 1, 'ok': True, 'comment': 'x', 'add': ['listed'], 'remove': [], 'close': True}])
        self.site.on('POST', BASE + '/labels', 201, {})
        self.assertEqual(agg.main(['apply', '--result', out]), 1)                                  # the fake GitHub refuses the comment: the step fails, with the issue number
        agg.write_json(out, {'number': 1, 'ok': True, 'comment': 'x', 'add': [], 'remove': [], 'close': False})
        self.assertEqual(agg.main(['apply', '--result', out]), 1)                                  # (the old single answer still works the old way)


if __name__ == '__main__':
    unittest.main()
