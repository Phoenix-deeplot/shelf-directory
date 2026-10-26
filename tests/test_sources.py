"""What the owner says about a source: status (fixed | dynamic | trial | paused), rights, topics, platform, a private note; the columns of the page; `set` and `shelves`. No internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
from test_aggregate import Base, feed_site  # noqa: E402


def directory(out=None):
    return json.loads((out or agg.build())['directory.json'])


class Sources(Base):
    def two(self):
        """Two feeds, A (articles 'A one', 'A two') and B ('B one'); -> their ids."""
        feed_site(self.site, title='Blog A', posts=(('A one', '2026-10-01'), ('A two', '2026-10-03')), path='/a.xml')
        feed_site(self.site, title='Blog B', posts=(('B one', '2026-10-02'),), path='/b.xml')
        a, _ = agg.approve(self.site.url + '/a.xml')
        b, _ = agg.approve(self.site.url + '/b.xml')
        return a['id'], b['id']

    def edit(self, sid, **fields):
        reg = agg.load_registry()
        next(e for e in reg['shelves'] if e['id'] == sid).update(fields)
        agg.write_json(agg.reg_path(), reg)

    def entry(self, sid):
        return next(e for e in agg.load_registry()['shelves'] if e['id'] == sid)

    def say(self, fn, *a, **k):
        """-> (result, what it printed)"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = fn(*a, **k)
        return r, buf.getvalue()


class TestTheFieldsInTheDirectory(Sources):
    def test_a_registry_that_says_nothing_builds_as_before_with_the_defaults(self):
        a, b = self.two()
        d = directory()
        self.assertEqual((d['count'], d['paused']), (3, 0))
        self.assertNotIn('columns', d)                                              # config.json has none: the page uses its own
        for s in d['shelves']:
            self.assertEqual((s['status'], s['rights'], s['health']), ('fixed', 'unknown', 'ok'))
            for k in ('topics', 'platform', 'note'):
                self.assertNotIn(k, s)

    def test_the_fields_come_through_and_a_note_is_never_published(self):
        a, b = self.two()
        agg.set_source(a, status='trial', rights='licensed', topics='tech, dev', platform='Zhihu', note='  my\nprivate   words ')
        agg.set_source(b, status='dynamic', rights='official-link')
        self.assertEqual(self.entry(a)['note'], 'my private words')                  # kept in the registry, tidied
        out = agg.build()
        sh = {s['id']: s for s in directory(out)['shelves']}
        self.assertEqual((sh[a]['status'], sh[a]['rights'], sh[a]['topics'], sh[a]['platform']), ('trial', 'licensed', ['tech', 'dev'], 'zhihu'))
        self.assertEqual((sh[b]['status'], sh[b]['rights']), ('dynamic', 'official-link'))
        self.assertNotIn('topics', sh[b])
        self.assertEqual(directory(out)['count'], 3)                                 # a trial shelf is in like any other
        self.assertFalse([rel for rel, text in out.items() if 'private' in text], 'a note must be in no file of docs/')
        self.assertNotIn('"note"', out['directory.json'])

    def test_the_old_status_of_the_last_read_is_called_health(self):
        a, b = self.two()
        self.site.put('/b.xml', 'gone', status=404)
        self.quiet(agg.fetch_all, only=b)                                            # B could not be read this time; what was read before is kept
        sh = {s['id']: s for s in directory()['shelves']}
        self.assertEqual((sh[b]['health'], sh[b]['status']), ('error', 'fixed'))
        self.assertEqual((sh[a]['health'], sh[a]['status']), ('ok', 'fixed'))

    def test_bad_values_in_the_registry_are_left_out_and_said_and_the_build_goes_on(self):
        a, b = self.two()
        feed_site(self.site, title='Blog C', posts=(('C one', '2026-10-04'),), path='/c.xml')
        c = agg.approve(self.site.url + '/c.xml')[0]['id']
        self.edit(a, status='gone', rights=7, platform='Bad Platform!', note='x' * 300)
        self.edit(b, topics=['tech', 'nonsense', 'tech'])                           # the right ones stay, once
        self.edit(c, topics='tech', status=['paused'])
        d, log = self.say(directory)
        sh = {s['id']: s for s in d['shelves']}
        self.assertEqual((d['count'], d['paused']), (4, 0))
        self.assertEqual((sh[a]['status'], sh[a]['rights']), ('fixed', 'unknown'))
        self.assertNotIn('platform', sh[a])
        self.assertEqual(sh[b]['topics'], ['tech'])
        self.assertNotIn('topics', sh[c])
        self.assertEqual(sh[c]['status'], 'fixed')
        for needle in ('%s: status must be' % a, '%s: rights must be' % a, '%s: platform must be' % a, '%s: note is text of at most' % a, '%s: No such topic: nonsense' % b, '%s: topics must be a list' % c):
            self.assertIn(needle, log)

    def test_topics_are_those_of_this_directory_so_an_owners_own_list_counts(self):
        a, _ = self.two()
        agg.write_json(os.path.join(self.tmp, 'registry', 'topics.json'), {'topics': [{'id': 'mine', 'zh': '我的', 'en': 'Mine', 'tags': ['a']}]})
        with self.assertRaises(agg.Refused):
            agg.set_source(a, topics='tech')                                         # not one of the owner's topics
        agg.set_source(a, topics='mine')
        self.assertEqual({s['id']: s for s in directory()['shelves']}[a]['topics'], ['mine'])

    def test_hosted_posts_are_licensed_by_the_author_who_sent_them(self):
        agg.write_json(os.path.join(self.tmp, 'registry', 'config.json'), {'base_url': 'https://pages.example/'})
        agg.publish_post({'title': 'A hosted post', 'author': 'Me', 'tags': ['x'], 'body': 'A text that is long enough to be a post of its own, with more than sixty characters in it.'}, 5)
        sh = {s['id']: s for s in directory()['shelves']}
        self.assertEqual((sh['posts']['status'], sh['posts']['rights'], sh['posts']['health']), ('fixed', 'licensed', 'ok'))


class TestPausedSources(Sources):
    def test_a_paused_source_is_in_no_list_no_feed_and_no_opml_and_is_counted(self):
        a, b = self.two()
        agg.set_source(a, status='paused')
        out = agg.build()
        d = directory(out)
        self.assertEqual(([s['id'] for s in d['shelves']], d['count'], d['paused']), ([b], 1, 1))
        self.assertEqual([i['title'] for i in d['items']], ['B one'])
        self.assertFalse([rel for rel in out if rel.endswith('/' + a + '.xml') or rel.endswith('/' + a + '.json')], 'no feed of the program')
        for rel, text in out.items():
            if rel.endswith(('.xml', '.json', '.opml')) and rel != 'directory.json':
                self.assertNotIn('A one', text, rel)
        self.assertNotIn(self.site.url + '/a.xml', out['shelves.opml'])
        self.assertIn(self.site.url + '/b.xml', out['shelves.opml'])
        tags = {t['name'] for t in d['tags']}
        self.assertIn('cat', tags)                                                   # B's tags are still counted

    def test_it_is_not_read_by_a_run_but_can_be_read_by_name_and_nothing_is_deleted(self):
        a, b = self.two()
        agg.set_source(a, status='paused')
        before = [h[0] for h in self.site.hits].count('/a.xml')
        lines = []
        self.assertEqual(agg.fetch_all(log=lines.append)[0], 1)                      # one read: B
        self.assertEqual([h[0] for h in self.site.hits].count('/a.xml'), before)
        self.assertTrue(any('skip' in l and a in l and 'paused' in l for l in lines), lines)
        self.assertTrue(os.path.exists(os.path.join(agg.snap_dir(), a + '.json')))   # its history stays
        self.assertEqual(agg.fetch_all(only=a, log=lambda *x: None)[0], 1)           # asked for by name, it is read
        self.assertEqual([h[0] for h in self.site.hits].count('/a.xml'), before + 1)
        self.assertEqual(directory()['paused'], 1)                                   # and is still not shown

    def test_resuming_shows_it_again_with_what_was_read_before(self):
        a, b = self.two()
        agg.set_source(a, status='paused')
        self.assertEqual(directory()['count'], 1)
        agg.set_source(a, status='fixed')
        d = directory()
        self.assertEqual((d['count'], d['paused'], sorted(s['id'] for s in d['shelves'])), (3, 0, sorted([a, b])))

    def test_a_paused_source_does_not_hold_a_page_that_a_later_source_has(self):
        a, b = self.two()
        feed_site(self.site, title='Blog B', posts=(('A one', '2026-10-01'),), path='/b.xml')       # B has the same address as A's first article
        self.quiet(agg.fetch_all, only=b)
        agg.set_source(a, status='paused')
        self.assertEqual(sorted(i['title'] for i in directory()['items']), ['A one'])


class TestColumns(Sources):
    def cols(self, columns):
        agg.write_json(os.path.join(self.tmp, 'registry', 'config.json'), {'columns': columns} if columns is not None else {})
        log = []
        return agg.load_columns(log=log.append), log

    def test_no_columns_in_the_config_none_in_the_directory(self):
        self.assertEqual(self.cols(None), (None, []))
        a, b = self.two()
        self.assertNotIn('columns', directory())

    def test_valid_columns_are_written_as_they_are_tidied(self):
        got, log = self.cols([{'id': 'tech', 'zh': ' AI 与科技 ', 'en': 'AI & Tech', 'topics': ['tech', 'dev', 'tech']},
                              {'id': 'video', 'zh': '视频', 'en': 'Video', 'platforms': [' YouTube ', 'bilibili', 'youtube'], 'types': ['video'], 'topics': []},
                              {'id': 'plain', 'zh': '随便', 'en': 'Any'}])
        self.assertEqual(got, [{'id': 'tech', 'zh': 'AI 与科技', 'en': 'AI & Tech', 'topics': ['tech', 'dev']},
                               {'id': 'video', 'zh': '视频', 'en': 'Video', 'platforms': ['youtube', 'bilibili'], 'types': ['video']},
                               {'id': 'plain', 'zh': '随便', 'en': 'Any'}])
        self.assertEqual(log, [])
        a, b = self.two()
        self.assertEqual(directory()['columns'], got)

    def test_a_column_that_is_not_right_is_left_out_and_said(self):
        good = {'id': 'ok', 'zh': '好', 'en': 'Good'}
        got, log = self.cols([good, {'id': 'ok', 'zh': '二', 'en': 'Two'},                                   # the id twice
                              {'id': 'Bad Id', 'zh': 'x', 'en': 'x'}, {'id': 'x' * 21, 'zh': 'x', 'en': 'x'}, {'id': '', 'zh': 'x', 'en': 'x'}, {'zh': 'x', 'en': 'x'}, {'id': 'tail\n', 'zh': 'x', 'en': 'x'},
                              {'id': 'noen', 'zh': '没有英文'}, {'id': 'blank', 'zh': '  ', 'en': 'Blank'}, {'id': 'long', 'zh': '长' * 25, 'en': 'Long'}, {'id': 'long2', 'zh': 'x', 'en': 'y' * 25},
                              {'id': 'topic', 'zh': 'x', 'en': 'x', 'topics': ['tech', 'nonsense']}, {'id': 'topic2', 'zh': 'x', 'en': 'x', 'topics': 'tech'},
                              {'id': 'plat', 'zh': 'x', 'en': 'x', 'platforms': ['You Tube']}, {'id': 'plat2', 'zh': 'x', 'en': 'x', 'platforms': 'youtube'},
                              {'id': 'type', 'zh': 'x', 'en': 'x', 'types': ['movie']}, {'id': 'type2', 'zh': 'x', 'en': 'x', 'types': [5]},
                              'junk', 7, None, ['list']])
        self.assertEqual(got, [good])
        self.assertEqual(len(log), 20)
        self.assertTrue(all(l.startswith('build: columns[') and 'left out' in l for l in log), log)

    def test_at_most_twelve(self):
        got, log = self.cols([{'id': 'c%d' % n, 'zh': '栏%d' % n, 'en': 'C%d' % n} for n in range(14)])
        self.assertEqual([c['id'] for c in got], ['c%d' % n for n in range(12)])
        self.assertEqual(len(log), 2)

    def test_nothing_fit_to_use_is_no_columns_and_the_build_goes_on(self):
        for bad in ([], [{'id': 'Bad'}], {'id': 'tech'}, 'tech', 5):
            self.assertIsNone(self.cols(bad)[0], bad)
        a, b = self.two()
        d, log = self.say(directory)
        self.assertNotIn('columns', d)
        self.assertEqual(d['count'], 3)

    def test_the_topics_of_a_column_are_those_of_this_directory(self):
        agg.write_json(os.path.join(self.tmp, 'registry', 'topics.json'), {'topics': [{'id': 'mine', 'zh': '我的', 'en': 'Mine', 'tags': ['a']}]})
        got, log = self.cols([{'id': 'a', 'zh': 'x', 'en': 'x', 'topics': ['mine']}, {'id': 'b', 'zh': 'x', 'en': 'x', 'topics': ['tech']}])
        self.assertEqual([c['id'] for c in got], ['a'])
        self.assertEqual(len(log), 1)

    def test_the_default_columns_of_this_repository_are_valid(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'registry', 'config.json'), encoding='utf-8') as f:
            written = json.load(f)['columns']
        got, log = self.cols(written)
        self.assertEqual((got, log), (written, []))
        self.assertEqual([c['id'] for c in got], ['fun', 'tech', 'learn', 'biz', 'work', 'video', 'read', 'world'])
        self.assertEqual(sorted(t for c in got for t in c.get('topics', [])), sorted(x['id'] for x in agg.DEFAULT_TOPICS))      # every topic is in exactly one column


class TestSetAndShelves(Sources):
    def test_set_changes_only_what_is_given_and_keeps_the_order_of_the_file(self):
        a, b = self.two()
        keys = list(self.entry(a))
        e = agg.set_source(a, status='dynamic', platform='wechat')
        self.assertEqual(list(e)[:len(keys)], keys)                                  # what was there stays where it was; the new ones follow
        self.assertEqual((e['status'], e['platform']), ('dynamic', 'wechat'))
        self.assertNotIn('rights', e)
        agg.set_source(a, rights='licensed', note='n')
        self.assertEqual((self.entry(a)['status'], self.entry(a)['platform'], self.entry(a)['rights'], self.entry(a)['note']), ('dynamic', 'wechat', 'licensed', 'n'))
        self.assertEqual(self.entry(b).get('status'), None)                          # the other one is not touched

    def test_empty_topics_platform_and_note_take_the_value_away_but_a_status_of_nothing_is_no_change(self):
        a, _ = self.two()
        agg.set_source(a, status='trial', rights='licensed', topics='tech', platform='zhihu', note='n')
        agg.set_source(a, status='', rights='', topics='', platform='', note='')
        e = self.entry(a)
        self.assertEqual((e['status'], e['rights']), ('trial', 'licensed'))
        for k in ('topics', 'platform', 'note'):
            self.assertNotIn(k, e)
        agg.set_source(a, topics='tech', note='n')
        agg.set_source(a, topics=[], note='   ')                                     # a list of nothing, a note of blanks
        for k in ('topics', 'note'):
            self.assertNotIn(k, self.entry(a))

    def test_a_bad_value_refuses_and_writes_nothing(self):
        a, _ = self.two()
        agg.set_source(a, status='trial')
        before = open(agg.reg_path(), encoding='utf-8').read()
        for kw in ({'status': 'Paused'}, {'status': 'gone'}, {'rights': 'maybe'}, {'topics': 'tech,nonsense'}, {'topics': ['TECH']}, {'platform': 'Zhi Hu'}, {'platform': '-zhihu'}, {'platform': 'z' * 31},
                   {'note': 'x' * 201}, {'status': 'paused', 'rights': 'maybe'}):
            with self.assertRaises(agg.Refused, msg=kw):
                agg.set_source(a, **kw)
        with self.assertRaises(agg.Refused):
            agg.set_source('nothing-here', status='paused')
        for odd in ('', '../x', 'A', 'a b', None):
            with self.assertRaises(agg.Refused):
                agg.set_source(odd, status='paused')
        self.assertEqual(open(agg.reg_path(), encoding='utf-8').read(), before)

    def test_the_longest_note_is_allowed_and_blanks_are_tidied(self):
        a, _ = self.two()
        agg.set_source(a, note='字' * 100 + '  \n  ' + '字' * 99)                    # 200 characters once the blanks are one space
        self.assertEqual((len(self.entry(a)['note']), '\n' in self.entry(a)['note']), (agg.NOTE_MAX, False))
        agg.set_source(a, note='x' * agg.NOTE_MAX)
        self.assertEqual(len(self.entry(a)['note']), agg.NOTE_MAX)
        with self.assertRaises(agg.Refused):
            agg.set_source(a, note='字' * 100 + ' ' + '字' * 100)                  # 201

    def test_the_command_line(self):
        a, b = self.two()
        _, out = self.say(agg.main, ['set', a, '--status', 'trial', '--rights', 'licensed', '--topics', 'tech,dev', '--platform', 'zhihu', '--note', 'hello'])
        self.assertIn('%s: status trial, rights licensed, topics tech,dev, platform zhihu, note kept' % a, out)
        self.assertEqual(self.entry(a)['topics'], ['tech', 'dev'])
        self.assertEqual({s['id']: s for s in json.load(open(os.path.join(self.tmp, 'docs', 'directory.json'), encoding='utf-8'))['shelves']}[a]['status'], 'trial')     # it builds
        self.assertEqual(agg.main(['set', a, '--topics=', '--platform=', '--note=-starts with a dash']), 0)
        e = self.entry(a)
        self.assertEqual((e.get('topics'), e.get('platform'), e['note']), (None, None, '-starts with a dash'))
        self.assertEqual(agg.main(['set', a, '--status=paused']), 0)
        self.assertEqual(json.load(open(os.path.join(self.tmp, 'docs', 'directory.json'), encoding='utf-8'))['paused'], 1)

    def test_the_command_line_refuses_without_writing(self):
        a, _ = self.two()
        before = open(agg.reg_path(), encoding='utf-8').read()
        err = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', write_through=True)               # main() reconfigures its stderr: a plain StringIO cannot be
        with contextlib.redirect_stderr(err):
            self.assertEqual(agg.main(['set', a, '--status', 'gone']), 1)
            self.assertEqual(agg.main(['set', 'no-such-shelf', '--status', 'paused']), 1)
            self.assertEqual(agg.main(['set', a, '--topics', 'tech,nonsense']), 1)
            with self.assertRaises(SystemExit):                                      # nothing to set
                agg.main(['set', a])
            with self.assertRaises(SystemExit):
                agg.main(['set', a, '--status', ''])
        self.assertIn('Refused', err.buffer.getvalue().decode('utf-8'))
        self.assertEqual(open(agg.reg_path(), encoding='utf-8').read(), before)

    def test_shelves_lists_every_source_with_the_defaults_filled_in_and_aligned(self):
        a, b = self.two()
        agg.set_source(a, status='paused', rights='official-link', topics='tech,dev', platform='wechat')
        self.edit(a, name='中文名字的来源')                                          # wide characters: the columns must still line up
        _, out = self.say(agg.main, ['shelves'])
        lines = out.rstrip('\n').split('\n')
        self.assertEqual(len(lines), 1 + 2 + 1)
        self.assertEqual(lines[0].split(), ['id', '名称', '状态', '版权', '领域', '平台', '篇数'])
        a_line = next(l for l in lines if l.startswith(a))
        self.assertEqual(a_line.split()[1:], ['中文名字的来源', 'paused', 'official-link', 'tech,dev', 'wechat', '2'])
        self.assertEqual(lines[3], '2 个来源，其中 1 个暂停')
        col = agg._width(lines[0][:lines[0].index('状态')])
        for l in lines[1:3]:                                                         # the columns start at the same place however wide the characters are
            word = 'paused' if 'paused' in l else 'fixed'
            self.assertEqual(agg._width(l[:l.index(word)]), col, l)
        b_line = next(l for l in lines if l.startswith(b))
        self.assertIn('fixed', b_line)
        self.assertIn('unknown', b_line)

    def test_a_source_can_be_given_its_fields_when_it_is_listed(self):
        feed_site(self.site, title='New one', path='/n.xml')
        e, _ = agg.approve(self.site.url + '/n.xml', fields={'status': 'trial', 'rights': 'official-link', 'topics': ['tech'], 'platform': 'youtube', 'note': 'watch it'})
        got = self.entry(e['id'])
        self.assertEqual((got['status'], got['rights'], got['topics'], got['platform'], got['note']), ('trial', 'official-link', ['tech'], 'youtube', 'watch it'))
        feed_site(self.site, title='Other', path='/o.xml')
        hits = len(self.site.hits)
        for bad in ({'status': 'gone'}, {'topics': ['nonsense']}, {'platform': 'No Good'}):
            with self.assertRaises(agg.Refused):
                agg.approve(self.site.url + '/o.xml', fields=bad)
        self.assertEqual((len(self.site.hits), len(agg.load_registry()['shelves'])), (hits, 1))        # refused before anything was read or written
        self.assertEqual(agg.main(['approve', self.site.url + '/o.xml', '--status', 'trial', '--topics', 'tech,dev', '--note', '']), 0)
        last = agg.load_registry()['shelves'][-1]
        self.assertEqual((last['status'], last['topics']), ('trial', ['tech', 'dev']))
        self.assertNotIn('note', last)                                               # an empty one is not written

    def test_an_ordinary_approval_adds_nothing_to_the_registry(self):
        feed_site(self.site, title='Plain', path='/p.xml')
        e, _ = agg.approve(self.site.url + '/p.xml')
        for k in ('status', 'rights', 'topics', 'platform', 'note'):
            self.assertNotIn(k, self.entry(e['id']))


if __name__ == '__main__':
    unittest.main()
