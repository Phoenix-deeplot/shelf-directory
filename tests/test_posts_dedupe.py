"""Duplicates, forks, identical files, hosted posts and rejections. The same fake web / GitHub as test_aggregate.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
from test_aggregate import Base, JSON_FEED, event, feed_site, repo  # noqa: E402

PAGE_WITH_FEED = '<html><head><title>Home</title><link rel="alternate" type="application/rss+xml" href="%s/feed.xml"></head><body>hi</body></html>'
RSS_ONE = '<rss version="2.0"><channel><title>Home</title><item><title>One</title><link>https://x.example/one</link></item></channel></rss>'


class TestUrlKey(unittest.TestCase):
    def test_same_page_different_spelling(self):
        same = ['https://www.Example.com/post/', 'http://example.com/post', 'https://example.com/post/index.html', 'https://example.com/post?utm_source=x&fbclid=1#top',
                'https://example.com//post', 'https://example.com/post?spm=a.b']
        self.assertEqual(len({agg.url_key(u) for u in same}), 1, [agg.url_key(u) for u in same])

    def test_different_pages_stay_different(self):
        keys = {agg.url_key(u) for u in ('https://example.com/a', 'https://example.com/b', 'https://example.com/a?id=1', 'https://example.com/a?id=2', 'https://other.example/a')}
        self.assertEqual(len(keys), 5)

    def test_parameters_are_sorted(self):
        self.assertEqual(agg.url_key('https://e.com/p?b=2&a=1'), agg.url_key('https://e.com/p?a=1&b=2'))

    def test_youtube_and_github_spellings(self):
        self.assertEqual(agg.url_key('https://youtu.be/dQw4w9WgXcQ'), agg.url_key('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s&list=x'))
        self.assertEqual(agg.url_key('https://www.youtube.com/shorts/abc'), agg.url_key('https://m.youtube.com/watch?v=abc'))
        self.assertEqual(agg.url_key('https://github.com/Owner/Repo.git'), agg.url_key('https://github.com/owner/repo/'))

    def test_garbage_does_not_crash(self):
        for u in ('', 'not a url', 'https://[::1', 'http://'):
            agg.url_key(u)


class TestDuplicates(Base):
    def test_the_same_feed_by_another_spelling_is_the_same_shelf(self):
        feed_site(self.site)
        for variant in ('/feed.xml?utm_source=twitter', '/feed.xml?utm_campaign=x'):          # the fake web answers the same on a tracking parameter
            self.site.routes[variant] = self.site.routes['/feed.xml']
        agg.approve(self.site.url + '/feed.xml')
        r = agg.probe(self.site.url + '/feed.xml?utm_source=twitter')
        self.assertEqual(r['duplicate'][0], 'shelf')
        with self.assertRaises(agg.Refused) as cm:
            agg.approve(self.site.url + '/feed.xml?utm_campaign=x')
        self.assertIn('已经在目录里了', str(cm.exception))

    def test_tracking_parameters_do_not_make_a_home_page_a_plain_page(self):
        """Found on the live repository: books.zhitree.cn?utm_source=x was "checked" as a new single page instead of the listed site."""
        self.site.put('/', PAGE_WITH_FEED % self.site.url)
        self.site.put('/feed.xml', RSS_ONE, 'application/rss+xml')
        agg.approve(self.site.url + '/')
        self.assertEqual(agg.normalize_input(self.site.url + '/?utm_source=twitter&fbclid=1'), self.site.url + '/')
        self.assertEqual(agg.normalize_input(self.site.url + '/p?id=3&utm_medium=x&b=2'), self.site.url + '/p?id=3&b=2')           # real parameters stay, in their order
        r = agg.probe(self.site.url + '?utm_source=twitter')
        self.assertEqual(r['duplicate'][0], 'shelf')

    def test_a_listed_site_is_a_duplicate_even_if_another_spelling_cannot_be_opened(self):
        """Found on the live repository: http://www.<listed site>/ failed to load and was reported as "cannot be listed" instead of "listed"."""
        agg.write_json(agg.reg_path(), {'shelves': [{'id': 'blog-example-org', 'kind': 'feed', 'url': 'https://blog.example.org/', 'source': 'https://blog.example.org/feed.xml', 'name': 'Blog', 'tags': [], 'added': '2026-10-14'}]})
        agg.write_json(os.path.join(agg.snap_dir(), 'blog-example-org.json'), {'id': 'blog-example-org', 'status': 'ok', 'fails': 0, 'shelf': {'name': 'Blog', 'lang': 'en'}, 'items': [{'url': 'https://blog.example.org/a', 'title': 'A', 'summary': '', 'image': '', 'date': '2026-10-01', 'tags': [], 'lang': 'en', 'type': 'article'}]})
        hits_before = len(self.site.hits)
        for url in ('http://www.blog.example.org/', 'https://blog.example.org', 'https://blog.example.org/index.html?utm_source=x', 'https://blog.example.org/feed.xml'):
            r = agg.probe(url)                                                        # nothing is requested: there is no such server here, and none is needed
            self.assertEqual((r['duplicate'], r['entry']['id']), (('shelf', 'blog-example-org'), 'blog-example-org'), url)
        self.assertEqual(len(self.site.hits), hits_before)
        res = agg.handle_issue(event('http://www.blog.example.org/'), 'check')
        self.assertEqual((res['ok'], res['add']), (False, ['duplicate']))
        self.assertIn('已经在目录里了', res['comment'])

    def test_a_single_page_that_a_listed_feed_already_has(self):
        self.site.put('/feed.xml', '<rss version="2.0"><channel><title>T</title><item><title>One</title><link>%s/post/one</link></item></channel></rss>' % self.site.url, 'application/rss+xml')
        agg.approve(self.site.url + '/feed.xml')
        self.site.put('/post/one', '<html><head><title>One</title></head><body>x</body></html>')          # the tracking parameter is dropped before the address is requested
        r = agg.probe(self.site.url + '/post/one?utm_source=a')
        self.assertEqual(r['duplicate'][0], 'item')
        with self.assertRaises(agg.Refused) as cm:
            agg.approve(self.site.url + '/post/one?utm_source=a')
        self.assertIn('已经收录', str(cm.exception))

    def test_a_repository_or_its_parent_folder_is_listed_already(self):
        repo(self.site, {'docs/a.md': '# A', 'docs/sub/b.md': '# B'})
        agg.approve('https://github.com/o/r')
        self.assertEqual(agg.probe('https://github.com/o/r/')['duplicate'][0], 'shelf')           # the very same repository
        for url in ('https://github.com/o/r/tree/main/docs', 'https://github.com/o/r/tree/main/docs/sub'):
            self.assertEqual(agg.probe(url)['duplicate'][0], 'repo', url)                          # a part of it
        self.assertEqual(agg._repo_key({'owner': 'O', 'repo': 'R', 'ref': '', 'path': '/docs/'}), ('o', 'r', '', 'docs'))        # GitHub names are case-blind

    def test_a_folder_listed_does_not_block_another_folder(self):
        repo(self.site, {'docs/a.md': '# A', 'notes/n.md': '# N'})
        agg.approve('https://github.com/o/r/tree/main/docs')
        self.assertIsNone(agg.probe('https://github.com/o/r/tree/main/notes')['duplicate'])

    def test_the_check_comment_says_it_is_a_duplicate(self):
        feed_site(self.site)
        agg.approve(self.site.url + '/feed.xml')
        res = agg.handle_issue(event(self.site.url + '/feed.xml'), 'check')
        self.assertFalse(res['ok'])
        self.assertEqual(res['add'], ['duplicate'])
        self.assertIn('重复', res['comment'])

    def test_a_fork_is_refused_and_the_original_named(self):
        repo(self.site, {'a.md': '# A'})
        self.site.put('/api/repos/o/r', json.dumps({'name': 'r', 'default_branch': 'main', 'fork': True, 'parent': {'full_name': 'up/stream'}, 'private': False}), 'application/json')
        with self.assertRaises(agg.Refused) as cm:
            agg.probe('https://github.com/o/r')
        self.assertIn('up/stream', str(cm.exception))

    def test_the_same_text_twice_in_a_repository_is_listed_once(self):
        repo(self.site, {'docs/a.md': '# Same\n\nbody', 'docs/b.md': '# Same\n\nbody', 'docs/c.md': '# Other'})
        shas = {'docs/a.md': 'aaa', 'docs/b.md': 'aaa', 'docs/c.md': 'ccc'}
        self.site.put('/api/repos/o/r/git/trees/main?recursive=1', json.dumps({'tree': [{'path': p, 'type': 'blob', 'sha': s} for p, s in shas.items()], 'truncated': False}), 'application/json')
        r = agg.probe('https://github.com/o/r')
        self.assertEqual(sorted(i['title'] for i in r['items']), ['Other', 'Same'])

    def test_the_same_file_in_two_repositories_is_listed_once(self):
        for name in ('r1', 'r2'):
            repo(self.site, {'docs/a.md': '# Shared'}, name=name)
            self.site.put('/api/repos/o/%s/git/trees/main?recursive=1' % name, json.dumps({'tree': [{'path': 'docs/a.md', 'type': 'blob', 'sha': 'same-blob'}], 'truncated': False}), 'application/json')
            agg.approve('https://github.com/o/' + name)
        d = json.loads(agg.build()['directory.json'])
        self.assertEqual(len(d['items']), 1)
        self.assertEqual(d['items'][0]['shelf'], 'gh-o-r1')                                  # the earlier shelf keeps it

    def test_an_item_the_owner_keeps_out_of_directories(self):
        d = json.loads(JSON_FEED)
        d['items'][0]['_shelf'] = {'type': 'note', 'listed': False}
        _, items = agg.parse_feed(json.dumps(d).encode(), {}, 'https://a.example/feed.json')
        self.assertEqual([i['title'] for i in items], ['Second'])

    def test_the_same_link_in_two_shelves_is_one_item_in_the_directory(self):
        feed_site(self.site, path='/a.xml')
        feed_site(self.site, title='Other', path='/b.xml')
        agg.approve(self.site.url + '/a.xml')
        agg.approve(self.site.url + '/b.xml')
        self.assertEqual(json.loads(agg.build()['directory.json'])['count'], 1)


POST_BODY = '# 我的文章\n\n这是读者写的一篇文章，里面有一些内容，足够长，可以通过长度检查。还有第二句话，让它更像一篇真正的文章。[链接](https://example.com/x)\n\n### 小节\n\n小节里的内容也要完整保留。\n'


def post_event(title='读者的文章', author='小明', tags='笔记, 供应链', body=POST_BODY, rights='我是作者并同意公开发布 / I am the author and agree to publish', number=21):
    text = '### 标题\n\n%s\n\n### 作者（可选）\n\n%s\n\n### 标签（可选）\n\n%s\n\n### 正文\n\n%s\n\n### 权利确认\n\n%s\n' % (title, author or '_No response_', tags or '_No response_', body, rights)
    return {'issue': {'number': number, 'body': text}}


class TestPosts(Base):
    def test_a_heading_inside_the_text_does_not_cut_the_post(self):
        sub = agg.parse_issue(post_event())
        self.assertEqual((sub['kind'], sub['title'], sub['author'], sub['tags'], sub['rights']), ('post', '读者的文章', '小明', ['笔记', '供应链'], True))
        self.assertIn('### 小节', sub['body'])
        self.assertTrue(sub['body'].rstrip().endswith('小节里的内容也要完整保留。'))
        body_with_known_heading = POST_BODY + '\n### 标题\n\n正文里恰好有一行叫“标题”的小标题。\n'
        self.assertIn('正文里恰好有一行', agg.parse_issue(post_event(body=body_with_known_heading))['body'])

    def test_an_address_form_is_not_a_post(self):
        self.assertEqual(agg.parse_issue(event('https://a.example/'))['kind'], 'shelf')

    def test_check_describes_the_post(self):
        res = agg.handle_issue(post_event(), 'check')
        self.assertTrue(res['ok'], res['comment'])
        self.assertEqual((res['add'], res['remove']), (['checked'], ['invalid', 'duplicate']))
        self.assertIn('读者的文章', res['comment'])
        self.assertIn('example.com', res['comment'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'posts')))                    # nothing is written before the approval

    def test_hard_limits(self):
        cases = {'no title': post_event(title='_No response_'), 'too short': post_event(body='太短了'), 'too long': post_event(body='字' * (agg.POST_BODY_MAX + 1)),
                 'long title': post_event(title='题' * 200), 'no rights': post_event(rights='_No response_')}
        for name, ev in cases.items():
            res = agg.handle_issue(ev, 'check')
            self.assertFalse(res['ok'], name)
            self.assertEqual(res['add'], ['invalid'], name)

    def test_blocked_words(self):
        with open(os.path.join(self.tmp, 'registry', 'blockwords.txt'), 'w', encoding='utf-8') as f:
            f.write('# words\n赌博\n')
        res = agg.handle_issue(post_event(body=POST_BODY + '\n欢迎来赌博网站'), 'check')
        self.assertFalse(res['ok'])
        self.assertIn('不允许的词', res['comment'])

    def test_link_spam_is_flagged_not_refused(self):
        links = ' '.join('https://spam%d.example/x' % i for i in range(30))
        res = agg.handle_issue(post_event(body=links + ' ' + '正常的文字。' * 30), 'check')
        self.assertTrue(res['ok'])
        self.assertIn('链接很多', res['comment'])

    def test_approve_writes_the_file_and_the_build_makes_the_page(self):
        os.environ['SHELF_BASE_URL'] = 'https://dir.example/'
        self.addCleanup(os.environ.pop, 'SHELF_BASE_URL', None)
        res = agg.handle_issue(post_event(), 'approve')
        self.assertTrue(res['ok'] and res['close'], res['comment'])
        self.assertEqual(res['add'], ['listed'])
        files = os.listdir(os.path.join(self.tmp, 'posts'))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith('p21-'))
        text = open(os.path.join(self.tmp, 'posts', files[0]), encoding='utf-8').read()
        self.assertIn('title: "读者的文章"', text)
        out = agg.build()
        slug = files[0][:-3]
        page = out['p/%s/index.html' % slug]
        self.assertIn('<h1>读者的文章</h1>', page)
        self.assertIn('rel="nofollow ugc noopener noreferrer"', page)
        self.assertIn('noindex', page)
        self.assertNotIn('<script', page)
        d = json.loads(out['directory.json'])
        post = next(i for i in d['items'] if i['shelf'] == 'posts')
        self.assertEqual((post['url'], post['title'], post['type'], post['tags']), ('https://dir.example/p/%s/' % slug, '读者的文章', 'article', ['笔记', '供应链']))
        self.assertEqual(next(s for s in d['shelves'] if s['id'] == 'posts')['name'], '社区投稿')
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'docs', 'p', slug, 'index.html')))

    def test_a_post_cannot_inject_into_its_page(self):
        os.environ['SHELF_BASE_URL'] = 'https://dir.example/'
        self.addCleanup(os.environ.pop, 'SHELF_BASE_URL', None)
        evil = POST_BODY + '\n<script>alert(1)</script>\n\n![x](https://t.example/p.gif)\n\n[js](javascript:alert(1))'
        agg.handle_issue(post_event(title='<img src=x onerror=alert(1)>', author='"><script>x</script>', body=evil), 'approve')
        page = next(v for k, v in agg.build().items() if k.startswith('p/') and k.endswith('index.html'))
        self.assertNotRegex(page, r'<(script|img|iframe)\b')
        self.assertNotIn('javascript:', page)

    def test_the_same_text_twice_is_refused(self):
        agg.handle_issue(post_event(number=21), 'approve')
        reformatted = POST_BODY.replace('这是读者', '这是**读者**').replace('\n\n', '\n\n\n') + '   \n'              # other marks and spacing, the same text
        again = agg.handle_issue(post_event(number=22, title='另一个标题', body=reformatted), 'check')
        self.assertFalse(again['ok'])
        self.assertIn('已经投过', again['comment'])

    def test_posts_without_a_base_address_are_not_published(self):
        agg.handle_issue(post_event(), 'approve')
        d = json.loads(agg.build()['directory.json'])
        self.assertEqual(d['count'], 0)

    def test_remove_a_post(self):
        os.environ['SHELF_BASE_URL'] = 'https://dir.example/'
        self.addCleanup(os.environ.pop, 'SHELF_BASE_URL', None)
        agg.handle_issue(post_event(), 'approve')
        slug = os.listdir(os.path.join(self.tmp, 'posts'))[0][:-3]
        agg.build()
        agg.remove('post:' + slug)
        out = agg.build()
        self.assertFalse(any(k.startswith('p/') for k in out))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'docs', 'p')))
        with self.assertRaises(agg.Refused):
            agg.remove('post:' + slug)
        with self.assertRaises(agg.Refused):
            agg.remove('post:../x')


class TestReject(Base):
    def test_a_rejected_address_is_not_taken_again(self):
        feed_site(self.site)
        res = agg.handle_issue(event(self.site.url + '/feed.xml'), 'reject')
        self.assertEqual((res['ok'], res['close'], res['add']), (True, True, ['rejected']))
        self.assertTrue(agg.rejected(agg.url_key(self.site.url + '/feed.xml')))
        self.assertTrue(agg.rejected(agg.url_key(self.site.url + '/feed.xml/?utm_source=x')))      # also by another spelling
        with self.assertRaises(agg.Refused):
            agg.probe(self.site.url + '/feed.xml')
        raw = open(os.path.join(self.tmp, 'registry', 'rejected.json'), encoding='utf-8').read()
        self.assertNotIn('127.0.0.1', raw)                                                          # only hashes are kept

    def test_the_text_of_a_rejected_post_is_cleared_in_the_issue(self):
        res = agg.handle_issue(post_event(number=7), 'reject')
        self.assertTrue(res['redact'])
        self.assertFalse(agg.handle_issue(event('https://a.example/x'), 'reject')['redact'])             # an address has nothing to hide
        s = self.site
        os.environ['GITHUB_TOKEN'] = 'tok'
        self.addCleanup(os.environ.pop, 'GITHUB_TOKEN', None)
        base = '/api/repos/o/r'
        s.put(base + '/issues/7/comments?per_page=100', '[]', 'application/json')
        for m, p in (('POST', '/issues/7/comments'), ('POST', '/labels'), ('POST', '/issues/7/labels'), ('PATCH', '/issues/7')):
            s.on(m, base + p, 200, {})
        s.on('DELETE', base + '/issues/7/labels/checked', 200, [])
        s.on('DELETE', base + '/issues/7/labels/invalid', 200, [])
        s.on('DELETE', base + '/issues/7/labels/approved', 200, [])
        s.on('DELETE', base + '/issues/7/labels/duplicate', 200, [])
        self.assertEqual(agg.apply_result(res, 'o/r'), [])
        patches = [b for m, p, b, h in s.calls if (m, p) == ('PATCH', base + '/issues/7')]
        self.assertEqual(len(patches), 2)
        self.assertIn('已清除', patches[0]['body'])
        self.assertNotIn('读者', patches[0]['body'])
        self.assertEqual(patches[1]['state'], 'closed')                                                  # cleared first, then closed

    def test_a_rejected_post_is_not_taken_again(self):
        agg.handle_issue(post_event(number=5), 'reject')
        again = agg.handle_issue(post_event(number=6, title='换个标题'), 'check')
        self.assertFalse(again['ok'])
        self.assertIn('不能收录', again['comment'])
        raw = open(os.path.join(self.tmp, 'registry', 'rejected.json'), encoding='utf-8').read()
        self.assertNotIn('读者', raw)

    def test_the_command(self):
        ev, out = os.path.join(self.tmp, 'e.json'), os.path.join(self.tmp, 'o.json')
        agg.write_json(ev, event('https://a.example/x'))
        self.assertEqual(agg.main(['reject', '--event', ev, '--out', out]), 0)
        self.assertEqual(json.load(open(out, encoding='utf-8'))['add'], ['rejected'])


if __name__ == '__main__':
    unittest.main()
