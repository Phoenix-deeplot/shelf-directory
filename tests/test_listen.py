"""Today's listening (listen.py): every source's answer is fake here (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import listen  # noqa: E402

NOW = 1793000000                       # 2026-10-26 ... in China
DAY2 = NOW + 86400


def jm(i, lic='http://creativecommons.org/licenses/by-sa/3.0/', audio=None, name=None):
    return {'id': str(1000 + i), 'name': name or 'Track %d' % i, 'duration': 180 + i, 'artist_name': 'Artist %d' % i, 'image': 'http://usercontent.jamendo.com/?type=album&id=%d&width=300' % i,
            'audio': audio or 'https://prod-1.storage.jamendo.com/?trackid=%d&format=mp31' % (1000 + i), 'shareurl': 'https://www.jamendo.com/track/%d' % (1000 + i), 'license_ccurl': lic}


def jamendo_answer(n=30, **kw):
    return json.dumps({'headers': {'status': 'success', 'code': 0, 'results_count': n}, 'results': [jm(i, **kw) for i in range(n)]})


def netease_answer(n=50, name='新歌榜'):
    return json.dumps({'result': {'name': name, 'tracks': [{'name': '歌%d' % i, 'id': 2000 + i, 'duration': 200000 + i * 1000, 'artists': [{'name': '甲'}, {'name': '乙'}], 'album': {'name': '专辑', 'picUrl': 'http://p1.music.126.net/a%d.jpg' % i}} for i in range(n)]
                                 + [{'name': '坏的', 'id': '../x'}]}})


def apple_chart(n=25):
    return json.dumps({'feed': {'results': [{'id': str(5000 + i), 'name': '歌%d' % i} for i in range(n)]}})


def apple_lookup(n=25, skip=()):
    return json.dumps({'resultCount': n, 'results': [{'kind': 'song', 'trackId': 5000 + i, 'trackName': 'Apple 歌%d' % i, 'artistName': '艺人%d' % i, 'artworkUrl100': 'https://is1-ssl.mzstatic.com/a%d/100x100bb.jpg' % i,
                                                     'previewUrl': 'https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/v4/x%d.m4a' % i, 'trackViewUrl': 'https://music.apple.com/cn/album/x/%d?i=%d' % (i, 5000 + i)}
                                                    for i in range(n) if i not in skip] + [{'kind': 'podcast', 'trackId': 1, 'trackName': 'not a song'}]})


def youtube_answer(n=20):
    return json.dumps({'items': [{'id': 'abcdefghi%02d' % i, 'snippet': {'title': 'Video %d' % i, 'channelTitle': 'Channel', 'thumbnails': {'medium': {'url': 'https://i.ytimg.com/vi/x/mq.jpg'}}}, 'statistics': {'viewCount': '100'}} for i in range(n)]
                       + [{'id': 'short', 'snippet': {'title': 'bad'}}]})


class Base(unittest.TestCase):
    def setUp(self):
        listen._CACHE.clear()
        self.asked = []
        self.down = set()
        self.old = listen._get
        listen._get = self.fake
        self.env = {'JAMENDO_CLIENT_ID': 'jmkey', 'YOUTUBE_API_KEY': 'ytkey'}
        self.jamendo = jamendo_answer()
        self.apple_chart = apple_chart()
        self.apple_lookup = apple_lookup()

    def tearDown(self):
        listen._get = self.old

    def fake(self, url, headers=None):
        self.asked.append(url)
        for k in self.down:
            if k in url:
                raise agg.Refused('网站返回了错误（HTTP 500）', 'HTTP 500')
        if 'api.jamendo.com' in url:
            return self.jamendo
        if 'music.163.com' in url:
            return netease_answer(name='榜%s' % url.rsplit('=', 1)[1])
        if 'googleapis.com' in url:
            return youtube_answer()
        if 'rss.applemarketingtools.com' in url:
            return self.apple_chart
        if 'itunes.apple.com/lookup' in url:
            return self.apple_lookup
        raise AssertionError(url)

    def day(self, now=NOW, env=None):
        scenes, report = listen.build(self.env if env is None else env, now)
        return {s['id']: s for s in scenes}, report


class Scenes(Base):
    def test_every_scene_is_made_when_all_sources_answer(self):
        s, report = self.day()
        self.assertEqual(list(s), ['hits', 'apple', 'relax', 'focus', 'sleep', 'lists', 'world'])
        self.assertTrue(all(r['ok'] for r in report))
        self.assertEqual(len(s['hits']['items']), 12)
        self.assertEqual(len(s['relax']['items']), 12)
        self.assertEqual(len(s['lists']['items']), 4)
        self.assertEqual(len(s['world']['items']), 12)                               # 2 Spotify play lists + 10 videos

    def test_a_streamed_track_carries_what_the_player_needs(self):
        t = self.day()[0]['relax']['items'][0]
        self.assertEqual(t['mode'], 'stream')
        self.assertTrue(t['audio'].startswith('https://prod-1.storage.jamendo.com/'))
        self.assertTrue(t['cover'].startswith('https://usercontent.jamendo.com/'))        # (http is made https)
        self.assertEqual(t['lic'], 'CC BY-SA 3.0')
        self.assertEqual(t['from'], 'Jamendo')
        self.assertGreater(t['dur'], 100)
        self.assertTrue(t['id'].startswith('jm:'))

    def test_netease_songs_are_embedded_and_the_chart_changes_with_the_day(self):
        a = self.day(NOW)[0]['hits']
        b = self.day(DAY2)[0]['hits']
        self.assertNotEqual(a['sub'], b['sub'])
        self.assertTrue(all(i['mode'] == 'embed' and i['url'].startswith('https://music.163.com/#/song?id=') for i in a['items']))
        self.assertEqual(a['items'][0]['artist'], '甲 / 乙')
        self.assertEqual(a['items'][0]['dur'], 200 + (int(a['items'][0]['id'][3:]) - 2000))
        self.assertTrue(a['items'][0]['cover'].startswith('https://p1.music.126.net/'))
        self.assertNotIn('ne:../x', [i['id'] for i in a['items']])                      # (an id that is not a number is not a song)
        ids = [int(i['id'][3:]) for i in a['items']]
        self.assertEqual(ids, sorted(ids))                                              # (in the chart's own order)

    def test_the_same_day_is_the_same_list_and_another_day_another(self):
        a = [i['id'] for i in self.day(NOW)[0]['relax']['items']]
        listen._CACHE.clear()
        self.assertEqual(a, [i['id'] for i in self.day(NOW + 3600)[0]['relax']['items']])
        self.assertNotEqual(a, [i['id'] for i in self.day(DAY2)[0]['relax']['items']])
        la = [i['id'] for i in self.day(NOW)[0]['lists']['items']]
        self.assertNotEqual(la, [i['id'] for i in self.day(DAY2)[0]['lists']['items']])

    def test_the_day_starts_at_midnight_in_china(self):
        midnight = 1793030400                                                               # 16:00 UTC = 00:00 in China
        self.assertEqual(listen.day_text(midnight - 2), listen.day_text(midnight - 1))
        self.assertNotEqual(listen.day_text(midnight - 1), listen.day_text(midnight))
        self.assertEqual(listen.day_text(midnight), listen.day_text(midnight + 3600))

    def test_playlists_have_a_page_address_and_spotify_is_marked_global(self):
        s = self.day()[0]
        for i in s['lists']['items']:
            self.assertEqual((i['mode'], i['kind']), ('embed', 'list'))
            self.assertRegex(i['url'], r'^https://music\.163\.com/#/playlist\?id=\d+$')
            self.assertNotIn('net', i)
        sp = [i for i in s['world']['items'] if i.get('kind') == 'list']
        self.assertEqual(len(sp), 2)
        self.assertTrue(all(i['url'].startswith('https://open.spotify.com/playlist/') and i['net'] == 'global' for i in sp))
        yt = [i for i in s['world']['items'] if i['from'] == 'YouTube']
        self.assertEqual(len(yt), 10)
        self.assertTrue(all(i['net'] == 'global' and i['url'].startswith('https://www.youtube.com/watch?v=') for i in yt))
        self.assertEqual(s['world']['net'], 'global')


class Licences(Base):
    def test_a_non_commercial_track_is_left_out_by_default(self):
        self.jamendo = json.dumps({'headers': {'status': 'success'}, 'results': [jm(1, 'http://creativecommons.org/licenses/by-nc-sa/3.0/')] + [jm(i) for i in range(2, 8)]})
        s, report = self.day()
        self.assertNotIn('jm:1001', [i['id'] for i in s['relax']['items']])
        self.assertEqual(len(s['relax']['items']), 6)
        self.assertEqual([r for r in report if r['scene'] == 'relax'][0]['left_out_for_licence'], 1)
        s2, _ = self.day(env={**self.env, 'LISTEN_ALLOW_NC': '1'})
        listen._CACHE.clear()
        self.assertEqual(len(s2['relax']['items']), 7)

    def test_a_track_without_a_known_licence_is_left_out(self):
        self.jamendo = json.dumps({'headers': {'status': 'success'}, 'results': [dict(jm(1), license_ccurl=''), dict(jm(2), license_ccurl='https://example.com/terms')] + [jm(i) for i in range(3, 8)]})
        ids = [i['id'] for i in self.day()[0]['relax']['items']]
        self.assertEqual(len(ids), 5)
        self.assertNotIn('jm:1001', ids)

    def test_labels(self):
        self.assertEqual(listen.cc_label('https://creativecommons.org/licenses/by/4.0/'), ('CC BY 4.0', True))
        self.assertEqual(listen.cc_label('http://creativecommons.org/licenses/by-nc-nd/3.0/'), ('CC BY-NC-ND 3.0', False))
        self.assertEqual(listen.cc_label('https://creativecommons.org/publicdomain/zero/1.0/'), ('CC0', True))
        self.assertEqual(listen.cc_label('')[0], '')

    def test_a_sound_that_is_not_from_jamendo_is_not_played(self):
        self.jamendo = json.dumps({'headers': {'status': 'success'}, 'results': [jm(1, audio='https://evil.example/a.mp3'), jm(2, audio='javascript:1'), jm(3, audio='http://mp3d.jamendo.com/x.mp3')] + [jm(i) for i in range(4, 9)]})
        items = self.day()[0]['relax']['items']
        self.assertEqual(len(items), 6)
        self.assertTrue(all(re_ok(i['audio']) for i in items))
        self.assertIn('jm:1003', [i['id'] for i in items])                                  # (an http address of Jamendo is made https)


def re_ok(u):
    import re
    return bool(re.match(r'https://[a-z0-9.-]*jamendo\.com[/?]', u))


class Failures(Base):
    def test_no_key_leaves_those_scenes_out_and_the_rest_stay(self):
        s, report = self.day(env={})
        self.assertEqual(list(s), ['hits', 'apple', 'lists', 'world'])
        self.assertEqual(len(s['world']['items']), 2)                                      # (only the Spotify play lists: they need no key)
        off = {(r['scene'], r['source']) for r in report if not r['ok']}
        self.assertIn(('relax', 'jamendo'), off)
        self.assertIn(('world', 'youtube'), off)
        self.assertFalse(self.asked and any('api.jamendo.com' in u or 'googleapis.com' in u for u in self.asked))          # (a source without a key is not even asked)

    def test_a_source_that_fails_leaves_its_scene_out(self):
        self.down = {'api.jamendo.com'}
        s, report = self.day()
        self.assertEqual(list(s), ['hits', 'apple', 'lists', 'world'])
        self.assertTrue([r for r in report if r['source'] == 'jamendo' and not r['ok']])

    def test_an_old_copy_is_served_when_the_source_stops_answering(self):
        a = [i['id'] for i in self.day(NOW)[0]['relax']['items']]
        self.down = {'api.jamendo.com'}
        b = [i['id'] for i in self.day(NOW + listen.POOL_SECONDS + 60)[0]['relax']['items']]           # (the copy is old now, the source is down)
        self.assertEqual(b, a)                                                                            # (the same day: the same list, from the copy)
        listen._CACHE.clear()
        self.assertNotIn('relax', self.day(NOW + 10 * 86400)[0])                                          # (no copy at all: no scene)

    def test_jamendo_saying_no_is_a_clean_failure(self):
        self.jamendo = json.dumps({'headers': {'status': 'failed', 'code': 5, 'error_message': 'Jamendo Api Invalid Client Id Error: Your credential is not authorized.'}, 'results': []})
        s, report = self.day()
        self.assertNotIn('relax', s)
        err = [r for r in report if r['source'] == 'jamendo'][0]['error']
        self.assertIn('Invalid Client Id', err)
        self.assertNotIn('jmkey', json.dumps(report))

    def test_an_answer_that_is_not_json_or_not_shaped_is_a_failure_not_a_crash(self):
        for bad in ('<html>', '[]', '{"headers": 5}', 'null'):
            listen._CACHE.clear()
            self.jamendo = bad
            self.assertNotIn('relax', self.day()[0])

    def test_scenes_can_be_turned_off(self):
        s, _ = self.day(env={**self.env, 'LISTEN_DISABLE': 'world, sleep'})
        self.assertNotIn('world', s)
        self.assertNotIn('sleep', s)
        self.assertIn('focus', s)


class Asking(Base):
    def test_the_key_is_sent_to_the_source_and_nowhere_else(self):
        status, ctype, body, cache = listen.handle({}, self.env, NOW)
        self.assertEqual(status, 200)
        self.assertNotIn(b'jmkey', body)
        self.assertNotIn(b'ytkey', body)
        self.assertTrue(any('client_id=jmkey' in u for u in self.asked))
        self.assertTrue(any('key=ytkey' in u for u in self.asked))

    def test_focus_asks_for_instrumental_music_and_the_tags_are_joined_with_plus(self):
        self.day()
        u = [u for u in self.asked if 'fuzzytags=piano' in u][0]
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
        self.assertEqual(q['vocalinstrumental'], ['instrumental'])
        self.assertEqual(q['fuzzytags'], ['piano classical jazz instrumental'])             # ('+' arrives as a space: that is what Jamendo's "a+b" means)
        self.assertEqual(q['order'], ['popularity_month'])
        self.assertEqual(q['groupby'], ['artist_id'])

    def test_a_source_is_asked_once_for_many_requests(self):
        self.day()
        n = len(self.asked)
        self.day(NOW + 600)
        self.day(NOW + 1200)
        self.assertEqual(len(self.asked), n)

    def test_the_answer_shape_and_headers(self):
        status, ctype, body, cache = listen.handle({}, self.env, NOW)
        d = json.loads(body)
        self.assertEqual((status, d['ok'], d['date']), (200, True, listen.day_text(NOW)))
        self.assertIn('s-maxage', cache)
        self.assertEqual([s['id'] for s in d['scenes']][:2], ['hits', 'apple'])
        self.assertTrue(all(i['id'] and i['title'] and i['mode'] in ('stream', 'embed') for s in d['scenes'] for i in s['items']))

    def test_diag_says_what_each_source_did_and_nothing_secret(self):
        status, _, body, cache = listen.handle({'diag': ['1']}, self.env, NOW)
        d = json.loads(body)
        self.assertEqual((status, cache), (200, 'no-store'))
        self.assertEqual(d['keys'], {'JAMENDO_CLIENT_ID': True, 'YOUTUBE_API_KEY': True})
        self.assertTrue(any(r['source'] == 'jamendo' and r['ok'] and r['pool'] == 30 for r in d['sources']))
        self.assertNotIn(b'jmkey', body)
        d0 = json.loads(listen.handle({'diag': ['1']}, {}, NOW)[2])
        self.assertEqual(d0['keys'], {'JAMENDO_CLIENT_ID': False, 'YOUTUBE_API_KEY': False})

    def test_nothing_to_play_is_said_so(self):
        env = {'LISTEN_DISABLE': 'hits,apple,relax,focus,sleep,lists,world'}
        status, _, body, cache = listen.handle({}, env, NOW)
        self.assertEqual((status, cache), (502, 'no-store'))
        self.assertEqual(json.loads(body)['error'], 'empty')

    def test_turned_off(self):
        self.assertEqual(listen.handle({}, {'LISTEN': '0'}, NOW)[0], 404)


class Probe(Base):
    old_outer = staticmethod(listen.netease_outer)

    def setUp(self):
        super().setUp()
        self.old_fetch, self.old_call = agg.fetch, listen._call
        self.calls = []
        listen._call = self.fake_call
        self.sound = ('audio/mpeg', b'x' * 1024)
        self.sounds = []

        def fetch(url, accept='*/*', max_bytes=0, headers=None, truncate=False, soft_xml=False):
            self.sounds.append((url, (headers or {}).get('Range')))
            if self.sound is None:
                raise agg.Refused('网站返回了错误（HTTP 403）', 'HTTP 403', 403)
            return url, {'content-type': self.sound[0]}, self.sound[1]
        agg.fetch = fetch

    def tearDown(self):
        agg.fetch, listen._call = self.old_fetch, self.old_call
        super().tearDown()

    def fake_call(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        return self.answers(url)

    def answers(self, url):
        if 'googleapis.com' in url:
            return 200, {'items': [{}, {}, {}]}
        if 'accounts.spotify.com' in url:
            return 200, {'access_token': 'tok'}
        if '/v1/search' in url:
            return 200, {'tracks': {'items': [{'preview_url': None}, {'preview_url': None}]}}
        return 403, {}

    def run_probe(self, env=None):
        out = []
        ok = listen.probe(self.env if env is None else env, NOW, out.append)
        return ok, '\n'.join(out)

    def test_everything_works(self):
        ok, text = self.run_probe()
        self.assertTrue(ok)
        self.assertRegex(text, r'JAMENDO_CLIENT_ID\s+已设置')
        self.assertIn('可以，audio/mpeg', text)
        self.assertIn('结论：这些组都能用。', text)
        self.assertEqual(len(self.sounds), 4)                                            # (one sound for each group that streams: Apple and the three Jamendo ones)
        self.assertTrue(all(r == 'bytes=0-1023' for _, r in self.sounds))                # (a piece of it, not the whole track)
        self.assertNotIn('jmkey', text)
        self.assertNotIn('ytkey', text)

    def test_a_sound_that_cannot_be_fetched_is_the_answer(self):
        self.sound = None
        ok, text = self.run_probe()
        self.assertFalse(ok)
        self.assertIn('失败', text)
        self.assertIn('有问题', text)

    def test_a_page_where_a_sound_should_be_is_said(self):
        self.sound = ('text/html', b'<html>')
        ok, text = self.run_probe()
        self.assertFalse(ok)
        self.assertIn('不像声音', text)

    def test_no_keys_says_so_and_still_works_for_the_rest(self):
        ok, text = self.run_probe({})
        self.assertTrue(ok)
        self.assertRegex(text, r'JAMENDO_CLIENT_ID\s+没设置')
        self.assertIn('no key', text)
        self.assertEqual(len(self.sounds), 1)                                             # (only Apple’s previews: no key needed)

    def test_youtube_key_that_is_not_allowed_says_why(self):
        self.answers = lambda url: (403, {'error': {'code': 403, 'message': 'x', 'errors': [{'reason': 'quotaExceeded'}]}}) if 'googleapis' in url else (200, {})
        ok, text = self.run_probe()
        self.assertIn('YouTube：不行（HTTP 403 quotaExceeded）：今天的配额用完了', text)
        self.assertNotIn('ytkey', text)
        self.answers = lambda url: (400, {'error': {'code': 400, 'message': 'API key not valid.', 'errors': [{'reason': 'badRequest'}], 'details': [{'reason': 'API_KEY_INVALID'}]}}) if 'googleapis' in url else (200, {})
        self.assertIn('YouTube：不行（HTTP 400 badRequest）', self.run_probe()[1])
        self.answers = lambda url: (403, {'error': {'code': 403, 'message': 'x', 'details': [{'reason': 'SERVICE_DISABLED'}]}}) if 'googleapis' in url else (200, {})
        self.assertIn('没有启用 YouTube Data API v3', self.run_probe()[1])

    def test_youtube_key_that_works(self):
        text = self.run_probe()[1]
        self.assertIn('YouTube：钥匙有效，音乐分类的热门读到 3 条', text)
        self.assertTrue(any('videoCategoryId=10' in u for u, _, _ in self.calls))

    def test_spotify_that_gives_no_preview_says_so(self):
        text = self.run_probe({**self.env, 'SPOTIFY_CLIENT_ID': 'sid', 'SPOTIFY_CLIENT_SECRET': 'ssecret'})[1]
        self.assertIn('令牌换到了', text)
        self.assertIn('带试听地址的 0 首', text)
        self.assertIn('“新发行”接口 HTTP 403（被拒', text)
        self.assertIn('只能用它的官方嵌入播放器', text)
        self.assertNotIn('ssecret', text)
        self.assertNotIn('tok', text.replace('token', '').replace('令牌', ''))
        tokens = [c for c in self.calls if 'accounts.spotify.com' in c[0]]
        self.assertEqual(len(tokens), 1)
        self.assertTrue(tokens[0][2]['Authorization'].startswith('Basic '))                 # (the secret goes to Spotify, in the header it asks for, and nowhere else)

    def test_spotify_with_previews_can_be_connected(self):
        self.answers = lambda url: (200, {'tracks': {'items': [{'preview_url': 'https://p.scdn.co/x'}]}}) if '/v1/search' in url else (200, {'access_token': 't'}) if 'accounts.spotify' in url else (200, {})
        text = self.run_probe({**self.env, 'SPOTIFY_CLIENT_ID': 'sid', 'SPOTIFY_CLIENT_SECRET': 'ssecret'})[1]
        self.assertIn('带试听地址的 1 首', text)
        self.assertIn('有，可以接', text)

    def test_spotify_with_a_wrong_secret(self):
        self.answers = lambda url: (400, {'error': 'invalid_client'}) if 'accounts.spotify' in url else (200, {})
        text = self.run_probe({**self.env, 'SPOTIFY_CLIENT_ID': 'sid', 'SPOTIFY_CLIENT_SECRET': 'bad'})[1]
        self.assertIn('换不到令牌（HTTP 400）', text)

    def test_no_spotify_key_is_skipped_not_called(self):
        self.run_probe()
        self.assertFalse([c for c in self.calls if 'spotify' in c[0]])

    def test_netease_direct_is_checked_not_fetched(self):
        listen.netease_outer = lambda sid: True
        try:
            ok, text = self.run_probe({**self.env, 'LISTEN_NETEASE_DIRECT': '1'})
        finally:
            listen.netease_outer = self.old_outer
        self.assertRegex(text, r'LISTEN_NETEASE_DIRECT\s+1：网易云的歌用站内播放条放整首')
        self.assertIn('网易云直连：服务已确认', text)
        self.assertEqual(len(self.sounds), 4)                                                # (Apple and the three Jamendo groups; NetEase's address is not fetched here: the browser test does that)

    def test_nothing_made(self):
        self.down = {'music.163.com'}
        ok, text = self.run_probe({'LISTEN_DISABLE': 'apple,lists,world,relax,focus,sleep'})
        self.assertFalse(ok)
        self.assertIn('一组都没做出来', text)


class Apple(Base):
    def test_the_chart_becomes_streamed_previews_in_the_charts_order(self):
        s, report = self.day()
        a = s['apple']
        self.assertEqual(len(a['items']), 12)
        t = a['items'][0]
        self.assertEqual((t['mode'], t['dur'], t['preview'], t['from'], t['lic']), ('stream', 30, True, 'Apple Music', 'Apple Music 30 秒试听'))
        self.assertTrue(t['audio'].startswith('https://audio-ssl.itunes.apple.com/'))
        self.assertTrue(t['url'].startswith('https://music.apple.com/'))
        self.assertTrue(t['cover'].endswith('300x300bb.jpg'))                                    # (a bigger picture than the 100 pixels of the answer)
        ids = [int(i['id'][3:]) for i in a['items']]
        self.assertEqual(ids, sorted(ids))
        self.assertIn('Apple Music', a['sub'])
        self.assertIn('30 秒试听', a['sub'])
        self.assertTrue([r for r in report if r['source'] == 'apple' and r['ok'] and r['count'] == 12])

    def test_the_country_changes_with_the_day_and_is_asked_for(self):
        regions = []
        for k in range(4):
            self.asked.clear()
            listen._CACHE.clear()
            s, _ = self.day(NOW + k * 86400)
            regions.append(next(u.split('/v2/')[1].split('/')[0] for u in self.asked if 'applemarketingtools' in u))
            self.assertTrue(any('itunes.apple.com/lookup' in u and 'country=%s' % regions[-1] in u for u in self.asked))
            self.assertIn({'cn': '中国', 'us': '美国', 'jp': '日本', 'kr': '韩国'}[regions[-1]], s['apple']['sub'])
        self.assertEqual(sorted(regions), ['cn', 'jp', 'kr', 'us'])

    def test_a_song_without_a_preview_or_with_a_strange_address_is_left_out(self):
        d = json.loads(apple_lookup())
        d['results'][0]['previewUrl'] = ''
        d['results'][1]['previewUrl'] = 'https://evil.example/a.m4a'
        d['results'][2]['previewUrl'] = 'javascript:1'
        d['results'][3]['trackViewUrl'] = 'https://evil.example/page'
        self.apple_lookup = json.dumps(d)
        items = self.day()[0]['apple']['items']
        ids = {i['id'] for i in items}
        self.assertFalse(ids & {'ap:5000', 'ap:5001', 'ap:5002'})
        weird = [i for i in listen.apple_tracks([str(5000 + k) for k in range(25)], self.apple_lookup) if i['id'] == 'ap:5003'][0]
        self.assertEqual(weird['url'], '')                                                        # (the song plays; there is no link to a page that is not Apple's)

    def test_a_chart_that_cannot_be_read_leaves_the_scene_out(self):
        self.down = {'applemarketingtools'}
        s, report = self.day()
        self.assertNotIn('apple', s)
        self.assertIn('hits', s)
        self.apple_chart = '{"feed": {"results": []}}'
        self.down = set()
        listen._CACHE.clear()
        self.assertNotIn('apple', self.day()[0])

    def test_the_scene_can_be_configured(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_listen_tmp')
        os.makedirs(d, exist_ok=True)
        old = agg.reg_path
        agg.reg_path = lambda: os.path.join(d, 'shelves.json')
        try:
            with open(os.path.join(d, 'listen.json'), 'w', encoding='utf-8') as f:
                json.dump({'scenes': [{'id': 'jp', 'name': '日本', 'apple': {'regions': ['JP', 'xx', 'tw'], 'count': 5}}]}, f)
            c = listen.scenes_config()
            self.assertEqual(c[0]['apple'], {'regions': ['jp', 'tw'], 'count': 5})
            self.assertEqual(len(self.day()[0]['jp']['items']), 5)
        finally:
            agg.reg_path = old
            os.remove(os.path.join(d, 'listen.json'))
            os.rmdir(d)


class NeteaseDirect(Base):
    def setUp(self):
        super().setUp()
        self.old_outer = listen.netease_outer
        self.outer = []
        listen.netease_outer = lambda sid: (self.outer.append(sid), int(sid) % 3 != 0)[1]          # (every third song is not open to everybody)
        self.env = {**self.env, 'LISTEN_NETEASE_DIRECT': '1'}

    def tearDown(self):
        listen.netease_outer = self.old_outer
        super().tearDown()

    def test_open_songs_become_streamed_tracks_and_the_others_are_left_out(self):
        s, report = self.day()
        items = s['hits']['items']
        self.assertEqual(len(items), 12)
        self.assertTrue(all(i['mode'] == 'stream' and i['audio'] == 'https://music.163.com/song/media/outer/url?id=%s.mp3' % i['id'][3:] for i in items))
        self.assertTrue(all(int(i['id'][3:]) % 3 != 0 for i in items))
        self.assertTrue(all(i['url'].startswith('https://music.163.com/#/song?id=') and i['dur'] > 100 and i['lic'] == '网易云音乐' for i in items))
        self.assertEqual([r for r in report if r['source'] == 'netease'][0]['mode'], 'direct')

    def test_only_the_top_forty_are_checked_and_once_per_pool(self):
        self.day()
        self.assertEqual(len(self.outer), 40)
        self.day(NOW + 600)
        self.assertEqual(len(self.outer), 40)

    def test_off_by_default_nothing_is_checked_and_the_songs_are_embedded(self):
        self.env = {k: v for k, v in self.env.items() if k != 'LISTEN_NETEASE_DIRECT'}
        s, report = self.day()
        self.assertEqual(self.outer, [])
        self.assertTrue(all(i['mode'] == 'embed' for i in s['hits']['items']))
        self.assertEqual([r for r in report if r['source'] == 'netease'][0]['mode'], 'their player')

    def test_a_chart_with_no_open_song_leaves_the_scene_out(self):
        listen.netease_outer = lambda sid: False
        s, report = self.day()
        self.assertNotIn('hits', s)
        self.assertTrue([r for r in report if r['source'] == 'netease' and not r['ok']])

    def test_the_check_reads_a_redirect_to_the_sound_server_and_nothing_else(self):
        import http.client
        old = http.client.HTTPSConnection
        replies = {'1': (302, 'http://m701.music.126.net/2026/x.mp3'), '2': (302, 'http://music.163.com/404'), '3': (200, ''), '4': (302, 'https://evil.example/x.mp3')}

        class Conn:
            def __init__(self, host, timeout=0):
                assert host == 'music.163.com'

            def request(self, method, path, headers=None):
                assert method == 'HEAD' and path.endswith('.mp3')
                self.sid = path.split('id=')[1].split('.')[0]

            def getresponse(self):
                st, loc = replies[self.sid]

                class R:
                    status = st

                    def getheader(self, k):
                        return loc
                return R()

            def close(self):
                pass
        http.client.HTTPSConnection = Conn
        try:
            self.assertEqual([self.old_outer(i) for i in '1234'], [True, False, False, False])
        finally:
            http.client.HTTPSConnection = old


class Config(Base):
    def setUp(self):
        super().setUp()
        self.dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_listen_tmp')
        os.makedirs(self.dir, exist_ok=True)
        self.old_reg = agg.reg_path
        agg.reg_path = lambda: os.path.join(self.dir, 'shelves.json')

    def tearDown(self):
        agg.reg_path = self.old_reg
        p = os.path.join(self.dir, 'listen.json')
        if os.path.exists(p):
            os.remove(p)
        os.rmdir(self.dir)
        super().tearDown()

    def write(self, obj):
        with open(os.path.join(self.dir, 'listen.json'), 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_the_owner_can_replace_the_scenes(self):
        self.write({'scenes': [{'id': 'mine', 'name': '我的', 'playlists': {'pick': 2, 'items': [{'src': 'netease', 'id': '71384707', 'title': 'A'}, {'src': 'spotify', 'id': '37i9dQZF1DXcBWIGoYBM5M', 'title': 'B'}, {'src': 'netease', 'id': 'x', 'title': 'bad id'},
                                                                                                    {'src': 'tidal', 'id': '123456', 'title': 'bad src'}]}},
                                {'id': 'Bad Id', 'name': 'x', 'jamendo': {'tags': ['rock']}}, {'id': 'nothing', 'name': '空'}, 'junk']})
        s, _ = self.day()
        self.assertEqual(list(s), ['mine'])
        self.assertEqual(len(s['mine']['items']), 2)
        self.assertEqual(s['mine']['name_en'], '我的')

    def test_a_broken_file_gives_the_built_in_scenes(self):
        for bad in ({'scenes': 'x'}, {'scenes': []}, ['a'], {'scenes': [{'id': 'x'}]}):
            self.write(bad)
            self.assertEqual([s['id'] for s in listen.scenes_config()], [s['id'] for s in listen.DEFAULT_SCENES])
        with open(os.path.join(self.dir, 'listen.json'), 'w') as f:
            f.write('{not json')
        self.assertEqual(len(listen.scenes_config()), len(listen.DEFAULT_SCENES))

    def test_junk_numbers_do_not_crash(self):
        self.write({'scenes': [{'id': 'hits2', 'name': 'n', 'netease': {'charts': [3778678, 'x'], 'count': 'many'}}, {'id': 'yt2', 'name': 'y', 'youtube': {'count': None}}]})
        c = listen.scenes_config()
        self.assertEqual(c[0]['netease'], {'charts': [3778678], 'count': 12})
        self.assertEqual(c[1]['youtube'], {'count': 10})


if __name__ == '__main__':
    unittest.main()
