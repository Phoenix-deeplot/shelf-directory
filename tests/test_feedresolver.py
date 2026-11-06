"""Subscription resolution contract, using real feed parsing and fake HTTP transport."""
import json
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from test_feedapi import FeedBase, RSS, PAGE
import aggregate as agg
import feedapi
import feedresolver as resolver
from submission import Reply


class TestProtocols(unittest.TestCase):
    def test_feed_protocols(self):
        for value in ('feed://example.org/feed', 'feed:https://example.org/feed',
                      'webcal://example.org/feed', 'itpc://example.org/feed', 'podcast://example.org/feed'):
            target, info = resolver.resolve(value)
            self.assertEqual(target, 'https://example.org/feed')
            self.assertEqual(info['method'], 'protocol')

    def test_no_unknown_protocol_or_private_target(self):
        for value in ('spotify:show:123', 'javascript:alert(1)', 'file:///tmp/feed',
                      'feed://127.0.0.1/feed', 'feed://user:secret@example.org/feed'):
            with self.assertRaises((Reply, agg.Refused)):
                resolver.resolve(value)

    def test_rsshub_requires_owner_instance_and_valid_route(self):
        with self.assertRaises(Reply) as e:
            resolver.resolve('https://space.bilibili.com/2267573')
        self.assertEqual(e.exception.body()['error'], 'rsshub_required')
        for value in ('rsshub://bilibili/user/video/2267573', 'https://space.bilibili.com/2267573/video'):
            target, info = resolver.resolve(value, {'RSSHUB_BASE': 'https://feeds.example.org'})
            self.assertEqual(target, 'https://feeds.example.org/bilibili/user/video/2267573')
            self.assertEqual(info['method'], 'rsshub')
        for route in ('rsshub://../admin', 'rsshub://bilibili/%2e%2e/admin', 'rsshub://x?key=secret'):
            with self.assertRaises(Reply):
                resolver.resolve(route, {'RSSHUB_BASE': 'https://feeds.example.org'})
        with self.assertRaises(agg.Refused):
            resolver.resolve('rsshub://bilibili/user/video/1', {'RSSHUB_BASE': 'https://localhost'})

    def test_platform_links_do_not_claim_to_be_feeds(self):
        for value in ('https://open.spotify.com/show/abc', 'https://music.163.com/#/playlist?id=1',
                      'https://x.com/home', 'https://www.zhihu.com/question/123'):
            with self.assertRaises(Reply) as e:
                resolver.resolve(value)
            self.assertEqual(e.exception.body()['error'], 'platform_adapter')

    def test_rsshub_creator_and_collection_links(self):
        env = {'RSSHUB_BASE': 'https://feeds.example.org'}
        cases = {
            'https://x.com/example/status/123': ('/twitter/user/example', 'X', 'creator'),
            'https://twitter.com/example': ('/twitter/user/example', 'X', 'creator'),
            'https://www.xiaohongshu.com/user/profile/593032945e87e77791e03696': ('/xiaohongshu/user/593032945e87e77791e03696/notes', 'Xiaohongshu', 'creator'),
            'https://www.zhihu.com/people/diygod/activities': ('/zhihu/people/activities/diygod', 'Zhihu', 'creator'),
            'https://www.zhihu.com/collection/26444956': ('/zhihu/collection/26444956', 'Zhihu', 'collection'),
        }
        for source, (path, platform, scope) in cases.items():
            with self.subTest(source=source):
                target, info = resolver.resolve(source, env)
                self.assertEqual(target, env['RSSHUB_BASE'] + path)
                self.assertEqual((info['platform'], info['scope'], info['auth_required']), (platform, scope, True))

    def test_youtube_playlist_maps_to_native_feed(self):
        target, info = resolver.resolve('https://www.youtube.com/playlist?list=PL1234567890')
        self.assertEqual(target, 'https://www.youtube.com/feeds/videos.xml?playlist_id=PL1234567890')
        self.assertEqual((info['method'], info['scope']), ('youtube_playlist', 'playlist'))

    def test_social_creator_adapters_require_configured_rsshub(self):
        for source in ('https://x.com/example', 'https://www.xiaohongshu.com/user/profile/593032945e87e77791e03696',
                       'https://www.zhihu.com/people/diygod', 'https://www.zhihu.com/collection/26444956'):
            with self.subTest(source=source), self.assertRaises(Reply) as err:
                resolver.resolve(source)
            self.assertEqual(err.exception.body()['error'], 'rsshub_required')


class TestResolution(FeedBase):
    def setUp(self):
        super().setUp()
        self.site.put('/show.xml', RSS, 'application/rss+xml')
        self.site.put('/releases.atom', RSS, 'application/rss+xml')
        self.site.put('/robots.txt', '', 'text/plain', status=404)

    def apple(self, rows):
        original = agg.fetch

        def fetch(url, *args, **kwargs):
            if url.startswith('https://itunes.apple.com/lookup?'):
                return url, {'content-type': 'application/json'}, json.dumps({'results': rows}).encode()
            if url == 'https://github.com/owner/project/releases.atom':
                return original(self.u + '/releases.atom', *args, **kwargs)
            return original(url, *args, **kwargs)
        return patch.object(agg, 'fetch', side_effect=fetch)

    def test_podlink_uses_id_without_fetching_limited_landing_page(self):
        with self.apple([{'kind': 'podcast', 'collectionId': 1714394532, 'feedUrl': self.u + '/show.xml'}]):
            st, result = self.discover('https://pod.link/1714394532')
        self.assertEqual(st, 200)
        self.assertEqual(result['kind'], 'feed')
        self.assertEqual(result['feed'], self.u + '/show.xml')
        self.assertEqual(result['resolution']['scope'], 'show')
        self.assertTrue(result['items'])
        self.assertTrue(all('pod.link' not in hit[0] for hit in self.site.hits))
        st, result = self.update([{'url': result['feed']}])
        self.assertEqual(st, 200)
        self.assertEqual(result['results'][0]['status'], 'ok')

    def test_apple_episode_url_follows_show_not_episode(self):
        with self.apple([{'kind': 'podcast', 'collectionId': 1714394532, 'feedUrl': self.u + '/show.xml'}]):
            st, result = self.discover('https://podcasts.apple.com/cn/podcast/a/id1714394532?i=100')
        self.assertEqual((st, result['resolution']['scope']), (200, 'show'))

    def test_missing_or_mismatched_show_is_actionable(self):
        for rows in ([], [{'kind': 'song', 'collectionId': 1714394532, 'feedUrl': self.u + '/show.xml'}],
                     [{'kind': 'podcast', 'collectionId': 1, 'feedUrl': self.u + '/show.xml'}]):
            with self.apple(rows):
                st, result = self.discover('https://pod.link/1714394532')
            self.assertEqual((st, result['error']), (422, 'feed_missing'))

    def test_lookup_private_feed_is_not_requested(self):
        with self.apple([{'kind': 'podcast', 'collectionId': 1714394532, 'feedUrl': 'https://127.0.0.1/private'}]):
            with patch.object(agg, 'ALLOW_LOCAL', False):
                with self.assertRaises(agg.Refused):
                    resolver.resolve('https://pod.link/1714394532')
        self.assertEqual(self.site.hits, [])

    def test_nonstandard_json_api_needs_adapter(self):
        self.site.put('/api.json', '{"data":[{"name":"Not a feed"}]}', 'application/json')
        st, result = self.discover(self.u + '/api.json')
        self.assertEqual((st, result['error']), (422, 'api_adapter'))

    def test_github_updates_are_releases(self):
        with self.apple([]):
            st, result = self.discover('https://github.com/owner/project')
        self.assertEqual(st, 200)
        self.assertEqual(result['feed'], 'https://github.com/owner/project/releases.atom')
        self.assertEqual(result['resolution']['scope'], 'releases')

    def test_article_explicit_feed_declaration(self):
        self.site.put('/post/a', PAGE.replace('</head>', '<link rel="alternate" type="application/rss+xml" href="/show.xml"></head>'))
        st, result = self.discover(self.u + '/post/a')
        self.assertEqual((st, result['kind'], result['feed']), (200, 'feed', self.u + '/show.xml'))
        self.assertEqual(result['resolution']['scope'], 'site')

    def test_rsshub_is_verified_not_just_a_constructed_url(self):
        original = agg.fetch
        with patch.object(agg, 'fetch', side_effect=lambda url, *a, **k: original(self.u + '/show.xml', *a, **k)):
            st, result = feedapi.handle(
                {'action': 'discover', 'url': 'https://space.bilibili.com/2267573'}, 'test', {'RSSHUB_BASE': 'https://feeds.example.org'})
        self.assertEqual((st, result['kind']), (200, 'feed'))
        with patch.object(agg, 'fetch', return_value=('https://feeds.example.org/x', {'content-type': 'text/html'}, b'<html>Login required</html>')):
            st, result = feedapi.handle({'action': 'discover', 'url': 'rsshub://bilibili/user/video/1'}, 'test', {'RSSHUB_BASE': 'https://feeds.example.org'})
        self.assertEqual(st, 422)
        self.assertNotEqual(result.get('kind'), 'feed')

    def test_channel_id_from_youtube_page(self):
        candidate = resolver.page_candidate(('https://www.youtube.com/@test', {'content-type': 'text/html'}, b'<script>{"channelId":"UC_x5XG1OV2P6uZZ5FSM9Ttw"}</script>'))
        self.assertEqual(candidate, 'https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw')

    def test_short_social_link_redirects_to_a_creator_route(self):
        final = 'https://www.xiaohongshu.com/user/profile/593032945e87e77791e03696'
        candidate = resolver.page_candidate((final, {'content-type': 'text/html'}, b'<html>creator</html>'))
        self.assertEqual(candidate, final)
        target = 'https://feeds.example.org/xiaohongshu/user/593032945e87e77791e03696/notes'
        original = agg.fetch

        def fetch(url, *args, **kwargs):
            if url == 'https://xhslink.com/share':
                return final, {'content-type': 'text/html'}, b'<html>creator</html>'
            if url == target:
                return target, {'content-type': 'application/rss+xml'}, RSS.encode()
            return original(url, *args, **kwargs)

        with patch.object(agg, 'fetch', side_effect=fetch):
            st, result = feedapi.handle({'action': 'discover', 'url': 'https://xhslink.com/share'}, 'test', {'RSSHUB_BASE': 'https://feeds.example.org'})
        self.assertEqual((st, result['kind'], result['resolution']['platform'], result['resolution']['auth_required']), (200, 'feed', 'Xiaohongshu', True))
        self.assertEqual(result['feed'], target)

    def test_youtube_playlist_feed_is_verified(self):
        target = 'https://www.youtube.com/feeds/videos.xml?playlist_id=PL1234567890'
        with patch.object(agg, 'fetch', return_value=(target, {'content-type': 'application/rss+xml'}, RSS.encode())):
            st, result = feedapi.handle({'action': 'discover', 'url': 'https://www.youtube.com/playlist?list=PL1234567890'}, 'test', {})
        self.assertEqual((st, result['kind'], result['resolution']['scope']), (200, 'feed', 'playlist'))
        self.assertEqual(result['feed'], target)


if __name__ == '__main__':
    unittest.main()
