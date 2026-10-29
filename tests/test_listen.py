"""Today's listening (listen.py): every source's answer is fake here (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import listen  # noqa: E402

NOW = 1793000000                       # 2026-10-26 15:33 in China
DAY2 = NOW + 86400


def jm(i, lic='http://creativecommons.org/licenses/by-sa/3.0/', audio=None):
    return {'id': str(1000 + i), 'name': 'Jam %d' % i, 'duration': 180 + i, 'artist_name': 'Jam artist %d' % i, 'image': 'http://usercontent.jamendo.com/?type=album&id=%d&width=300' % i,
            'audio': audio or 'https://prod-1.storage.jamendo.com/?trackid=%d&format=mp31' % (1000 + i), 'shareurl': 'https://www.jamendo.com/track/%d' % (1000 + i), 'license_ccurl': lic}


def jamendo_answer(n=30, **kw):
    return json.dumps({'headers': {'status': 'success', 'code': 0}, 'results': [jm(i, **kw) for i in range(n)]})


def deezer_answer(tag, n=50, nopreview=()):
    return json.dumps({'data': [{'id': 100000 + hash(tag) % 1000 * 100 + i, 'title': '%s song %d' % (tag, i), 'duration': 200, 'preview': '' if i in nopreview else 'https://cdnt-preview.dzcdn.net/api/1/1/%s/%d.mp3' % (tag, i),
                                 'link': 'https://www.deezer.com/track/%d' % i, 'artist': {'name': '%s artist %d' % (tag, i)}, 'album': {'cover_medium': 'https://cdn-images.dzcdn.net/images/cover/%s%d/250x250.jpg' % (tag, i)}} for i in range(n)]
                       + [{'id': 'x', 'title': 'bad'}]})


def audius_answer(tag, n=40):
    return json.dumps({'data': [{'id': 'A%s%d' % (tag[:3].upper(), i), 'title': '%s track %d' % (tag, i), 'duration': 200 + i, 'is_streamable': True, 'permalink': '/artist%d/%s-%d' % (i, tag.lower(), i),
                                 'user': {'name': 'au %s %d' % (tag, i)}, 'artwork': {'480x480': 'https://cdn.audius.example/%s%d.jpg' % (tag, i)}} for i in range(n)]
                       + [{'id': 'GONE', 'title': 'deleted', 'is_streamable': False, 'duration': 10}, {'id': '../x', 'title': 'bad'}]})


def apple_chart(base, n=25):
    return json.dumps({'feed': {'results': [{'id': str(base + i), 'name': 'x'} for i in range(n)]}})


def apple_genre_feed(base, n=30):
    return json.dumps({'feed': {'entry': [{'id': {'attributes': {'im:id': str(base + i)}}} for i in range(n)]}})


def apple_lookup(ids, skip=()):
    out = []
    for i in ids:
        if int(i) in skip:
            continue
        out.append({'kind': 'song', 'trackId': int(i), 'trackName': 'Apple song %s' % i, 'artistName': 'Apple artist %s' % i, 'artworkUrl100': 'https://is1-ssl.mzstatic.com/a%s/100x100bb.jpg' % i,
                    'previewUrl': 'https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/v4/x%s.m4a' % i, 'trackViewUrl': 'https://music.apple.com/cn/album/x/%s?i=%s' % (i, i)})
    return json.dumps({'resultCount': len(out), 'results': out + [{'kind': 'podcast', 'trackId': 1, 'trackName': 'not a song'}]})


def netease_answer(n=50, name='榜'):
    return json.dumps({'result': {'name': name, 'tracks': [{'name': '歌%d' % i, 'id': 2000 + i, 'duration': 200000 + i * 1000, 'artists': [{'name': '甲'}, {'name': '乙%d' % i}], 'album': {'name': '专辑', 'picUrl': 'http://p1.music.126.net/a%d.jpg' % i}}
                                                           for i in range(n)] + [{'name': '坏的', 'id': '../x'}]}})


def youtube_answer(n=20):
    return json.dumps({'items': [{'id': 'abcdefghi%02d' % i, 'snippet': {'title': 'Video %d' % i, 'channelTitle': 'Channel %d' % i, 'thumbnails': {'medium': {'url': 'https://i.ytimg.com/vi/x/mq.jpg'}}}, 'statistics': {'viewCount': '100'}} for i in range(n)]
                       + [{'id': 'short', 'snippet': {'title': 'bad'}}]})


REGION_BASE = {'cn': 5000, 'us': 6000, 'gb': 7000, 'jp': 8000, 'kr': 9000, 'tw': 10000, 'hk': 11000}


class Base(unittest.TestCase):
    def setUp(self):
        listen._CACHE.clear()
        self.asked = []
        self.down = set()
        self.slow = {}
        self.old_get, self.old_outer, self.old_group = listen._get, listen.netease_outer, listen.GROUP_SECONDS
        listen._get = self.fake
        listen.netease_outer = lambda sid: int(sid) % 3 != 0                 # every third song is not open to everybody
        self.env = {'JAMENDO_CLIENT_ID': 'jmkey', 'YOUTUBE_API_KEY': 'ytkey'}
        self.jamendo = jamendo_answer()
        self.dz_lists = json.dumps({'data': [{'id': 7001, 'title': 'bedroom rave', 'nb_tracks': 50}, {'id': 7002, 'title': 'Techno Tunnel', 'nb_tracks': 100}, {'id': 3155776842, 'title': 'dup of a fixed one', 'nb_tracks': 100}, {'id': 'x', 'title': 'bad'}]})
        self.au_lists = json.dumps({'data': [{'id': 'PLaaa', 'playlist_name': 'Hot & New', 'track_count': 20}, {'id': '../x', 'playlist_name': 'bad'}]})

    def tearDown(self):
        listen._get, listen.netease_outer, listen.GROUP_SECONDS = self.old_get, self.old_outer, self.old_group

    def fake(self, url, headers=None):
        self.asked.append(url)
        for k in self.down:
            if k in url:
                raise agg.Refused('网站返回了错误（HTTP 500）', 'HTTP 500')
        for k, secs in self.slow.items():
            if k in url:
                time.sleep(secs)
        if 'api.jamendo.com' in url:
            return self.jamendo
        if 'music.163.com/api/playlist' in url:
            return netease_answer(name='网易榜%s' % url.rsplit('=', 1)[1])
        if 'googleapis.com' in url:
            return youtube_answer()
        if 'rss.applemarketingtools.com' in url:
            cc = url.split('/v2/')[1].split('/')[0]
            return apple_chart(REGION_BASE[cc])
        if 'itunes.apple.com/lookup' in url:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return apple_lookup(q['id'][0].split(','))
        if 'itunes.apple.com/' in url and '/rss/topsongs/' in url:
            cc = url.split('.com/')[1].split('/')[0]
            return apple_genre_feed(REGION_BASE[cc] + (100 * int(url.split('genre=')[1].split('/')[0]) if 'genre=' in url else 0))
        if 'api.deezer.com/chart/0/playlists' in url:
            return self.dz_lists
        if 'api.deezer.com/chart/' in url:
            return deezer_answer('dzc%s' % url.split('/chart/')[1].split('/')[0])
        if 'api.deezer.com/playlist/' in url:
            return deezer_answer('dzp%s' % url.split('/playlist/')[1].split('/')[0], 100)
        if 'api.audius.co/v1/playlists/trending' in url:
            return self.au_lists
        if '/playlists/' in url and 'api.audius.co' in url:
            return audius_answer('PL')
        if 'api.audius.co/v1/tracks/trending' in url:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return audius_answer((q.get('genre') or ['all'])[0].replace('/', '').replace('&', '')[:5])
        raise AssertionError(url)

    def index(self, env=None, now=NOW):
        st, ct, body, cache = listen.handle({}, self.env if env is None else env, now)
        self.assertEqual((st, cache), (200, listen.CDN))
        return json.loads(body)

    def group(self, gid, env=None, now=NOW):
        st, ct, body, cache = listen.handle({'g': [gid]}, self.env if env is None else env, now)
        return st, json.loads(body)

    def songs(self, gid, env=None, now=NOW):
        st, d = self.group(gid, env, now)
        self.assertEqual(st, 200, d)
        return d['group']


class Index(Base):
    def test_the_lists_in_the_order_of_their_kinds(self):
        d = self.index()
        ids = [g['id'] for g in d['groups']]
        kinds = [g['kind'] for g in d['groups']]
        self.assertEqual(d['date'], listen.day_text(NOW))
        self.assertEqual(ids[0], 'daily')
        self.assertEqual(kinds, sorted(kinds, key=lambda k: listen.KIND_ORDER[k]))
        for want in ('hot-cn', 'hot-west', 'kr', 'jp', 'dzp-3155776842', 'dzp-1313621735', 'rock', 'pop', 'hiphop', 'electronic', 'jazz', 'classical', 'folk', 'latin', 'chill', 'focus', 'sleep', 'video'):
            self.assertIn(want, ids)
        self.assertTrue(all(set(g) <= {'id', 'name', 'name_en', 'kind', 'net', 'n'} for g in d['groups']))              # (the index says nothing about where the songs come from)
        self.assertTrue(all(g['name'] and g['name_en'] for g in d['groups']))

    def test_the_play_lists_that_are_on_top_today_are_there_and_a_double_is_not(self):
        ids = [g['id'] for g in self.index()['groups']]
        self.assertIn('dzp-7001', ids)
        self.assertIn('dzp-7002', ids)
        self.assertEqual(ids.count('dzp-3155776842'), 1)
        self.assertIn('aup-PLaaa', ids)
        self.assertNotIn('aup-../x', ids)
        g = next(g for g in self.index()['groups'] if g['id'] == 'dzp-7001')
        self.assertEqual((g['name'], g['kind'], g['n']), ('bedroom rave', 'list', 50))

    def test_without_the_keys_and_the_direct_play_only_what_can_be_asked_is_there(self):
        ids = [g['id'] for g in self.index({})['groups']]
        self.assertNotIn('video', ids)                                                  # (needs the YouTube key)
        self.assertFalse([i for i in ids if i.startswith('nep-')])                      # (needs the direct play)
        self.assertIn('chill', ids)                                                     # (Audius still has it)
        self.assertIn('rock', ids)

    def test_with_the_direct_play_netease_charts_are_lists(self):
        d = self.index({**self.env, 'LISTEN_NETEASE_DIRECT': '1'})
        neteases = [g for g in d['groups'] if g['id'].startswith('nep-')]
        self.assertEqual(len(neteases), len(listen.NE_LISTS))
        self.assertTrue(all(g['kind'] == 'list' and g['name'].startswith('网易云 · ') for g in neteases))

    def test_a_deezer_that_does_not_answer_leaves_the_static_lists(self):
        self.down = {'api.deezer.com/chart/0/playlists'}
        ids = [g['id'] for g in self.index()['groups']]
        self.assertNotIn('dzp-7001', ids)
        self.assertIn('dzp-3155776842', ids)
        self.assertIn('aup-PLaaa', ids)

    def test_lists_can_be_turned_off(self):
        ids = [g['id'] for g in self.index({**self.env, 'LISTEN_DISABLE': 'rock, metal,dzp-7001'})['groups']]
        self.assertFalse({'rock', 'metal', 'dzp-7001'} & set(ids))
        self.assertIn('pop', ids)

    def test_turned_off_and_nothing_to_show(self):
        self.assertEqual(listen.handle({}, {'LISTEN': '0'}, NOW)[0], 404)


class Songs(Base):
    def test_a_style_has_thirty_songs_from_all_its_sources_each_song_once(self):
        g = self.songs('rock')
        items = g['items']
        self.assertEqual(len(items), 30)
        self.assertEqual({i['from'] for i in items}, {'Deezer', 'Audius', 'Apple Music'})
        keys = [(i['title'].lower(), i['artist'].lower()) for i in items]
        self.assertEqual(len(set(keys)), 30)
        self.assertTrue(all(i['mode'] == 'stream' and i['audio'].startswith('https://') for i in items))
        self.assertEqual(g['sub'], '来源：Deezer · Audius · Apple Music')
        self.assertEqual(g['sub_en'], 'From Deezer · Audius · Apple Music')
        self.assertEqual((g['id'], g['kind'], g['name']), ('rock', 'style', '摇滚'))

    def test_a_song_carries_what_the_player_and_the_licence_need(self):
        by = {i['from']: i for i in self.songs('rock')['items']}
        dz, au, ap = by['Deezer'], by['Audius'], by['Apple Music']
        self.assertEqual((dz['dur'], dz['preview'], dz['lic']), (30, True, 'Deezer 30 秒试听'))
        self.assertTrue(dz['audio'].startswith('https://cdnt-preview.dzcdn.net/') and dz['url'].startswith('https://www.deezer.com/track/'))
        self.assertTrue(au['dur'] >= 200 and 'preview' not in au and au['lic'] == 'Audius')
        self.assertRegex(au['audio'], r'^https://api\.audius\.co/v1/tracks/A\w+/stream\?app_name=zhitree-books$')
        self.assertTrue(au['url'].startswith('https://audius.co/artist'))
        self.assertTrue(ap['cover'].endswith('300x300bb.jpg') and ap['audio'].startswith('https://audio-ssl.itunes.apple.com/') and ap['url'].startswith('https://music.apple.com/'))

    def test_the_same_day_the_same_list_another_day_another(self):
        a = [i['id'] for i in self.songs('rock')['items']]
        listen._CACHE.clear()
        self.assertEqual(a, [i['id'] for i in self.songs('rock', now=NOW + 3600)['items']])
        self.assertNotEqual(a, [i['id'] for i in self.songs('rock', now=DAY2)['items']])

    def test_a_chart_keeps_the_charts_order(self):
        items = self.songs('hot-west')['items']
        self.assertEqual(len(items), 30)
        self.assertEqual({i['from'] for i in items}, {'Deezer', 'Apple Music'})
        dz = [int(i['title'].split()[-1]) for i in items if i['from'] == 'Deezer']
        self.assertEqual(dz, sorted(dz))                                                  # (the best of each source first)
        self.assertEqual(items[0]['from'], 'Deezer')

    def test_the_daily_picks_are_thirty_songs_of_thirty_artists_from_several_sources(self):
        items = self.songs('daily')['items']
        self.assertEqual(len(items), 30)
        self.assertEqual(len({i['artist'].lower() for i in items}), 30)
        self.assertGreaterEqual(len({i['from'] for i in items}), 3)

    def test_the_first_source_that_gives_a_list_is_the_one_used(self):
        g = self.songs('hot-cn')                                                           # (without the direct play NetEase is off: Apple's charts of China, Taiwan and Hong Kong, one after the other until there are 30)
        self.assertEqual({i['from'] for i in g['items']}, {'Apple Music'})
        self.assertEqual(len(g['items']), 30)
        self.assertEqual(g['sub'], '来源：Apple Music')
        self.assertTrue(g['items'][0]['id'].startswith('ap:50'))                           # (China's chart first)
        g = self.songs('hot-cn', {**self.env, 'LISTEN_NETEASE_DIRECT': '1'})
        self.assertEqual(g['items'][0]['from'], '网易云音乐')
        self.assertEqual(g['sub'].split(' · ')[0], '来源：网易云音乐')
        self.assertEqual(len(g['items']), 30)

    def test_a_first_source_with_next_to_nothing_is_filled_up_from_the_next(self):
        listen.netease_outer = lambda sid: int(sid) < 2002                                 # (two open songs: the rest of the list is filled up from the next sources)
        g = self.songs('hot-cn', {**self.env, 'LISTEN_NETEASE_DIRECT': '1'})
        self.assertEqual([i['from'] for i in g['items'][:3]], ['网易云音乐', '网易云音乐', 'Apple Music'])
        self.assertEqual(len(g['items']), 30)

    def test_a_play_list_has_up_to_fifty_songs_in_its_order(self):
        g = self.songs('dzp-3155776842')
        self.assertEqual(len(g['items']), 50)
        self.assertEqual(g['items'][0]['title'], 'dzp3155776842 song 0')
        g = self.songs('dzp-7001')
        self.assertEqual(g['name'], 'bedroom rave')
        self.assertEqual(len(g['items']), 50)
        g = self.songs('aup-PLaaa')
        self.assertEqual(g['items'][0]['from'], 'Audius')

    def test_a_play_list_that_is_not_one_of_todays_is_not_asked_for(self):
        st, d = self.group('dzp-123456')
        self.assertEqual((st, d['error']), (404, 'none'))
        self.assertFalse([u for u in self.asked if '/playlist/123456' in u])
        self.assertEqual(self.group('nope')[0], 404)
        self.assertEqual(self.group('nep-3778678')[0], 404)                                 # (no direct play: no such list)

    def test_a_song_without_a_preview_or_with_a_strange_address_is_left_out(self):
        listen._CACHE.clear()
        self.old = listen._get
        d = json.loads(deezer_answer('dzc152'))
        d['data'][0]['preview'] = 'https://evil.example/a.mp3'
        d['data'][1]['preview'] = 'javascript:1'
        d['data'][2]['preview'] = ''
        items = listen.deezer_tracks(json.dumps(d))
        self.assertEqual(len(items), 47)
        self.assertFalse([i for i in items if 'evil' in i['audio']])
        a = json.loads(apple_lookup(['5001', '5002', '5003']))
        a['results'][0]['previewUrl'] = 'https://evil.example/x.m4a'
        a['results'][1]['trackViewUrl'] = 'https://evil.example/page'
        got = listen.apple_tracks(['5001', '5002', '5003'], json.dumps(a))
        self.assertEqual([i['id'] for i in got], ['ap:5002', 'ap:5003'])
        self.assertEqual(got[0]['url'], '')                                                  # (it plays; there is no link to a page that is not Apple's)
        au = json.loads(audius_answer('x', 3))
        self.assertEqual(len(listen.audius_tracks(json.dumps(au))), 3)                      # (the deleted one and the one with a strange id are not there)

    def test_a_chart_of_apple_falls_back_to_the_other_feed(self):
        self.down = {'/rss/topsongs/limit=50/json'}
        items = self.songs('hot-cn')['items']                                                # (without the older feed the newer one is asked)
        self.assertEqual({i['from'] for i in items}, {'Apple Music'})
        self.assertTrue([u for u in self.asked if 'rss.applemarketingtools.com' in u])
        listen._CACHE.clear()
        self.down = {'/rss/topsongs/limit=50/json', 'rss.applemarketingtools.com'}
        self.assertEqual(self.group('hot-cn')[0], 502)

    def test_the_old_apple_style_feed_and_the_new_one_both_give_ids(self):
        self.assertEqual(listen.apple_ids(apple_chart(1000, 3)), ['1000', '1001', '1002'])
        self.assertEqual(listen.apple_ids(apple_genre_feed(2000, 2)), ['2000', '2001'])
        self.assertEqual(listen.apple_ids(json.dumps({'feed': {'entry': {'id': {'attributes': {'im:id': '777777'}}}}})), ['777777'])
        self.assertEqual(listen.apple_ids('{"feed": 5}'), [])


class Failures(Base):
    def test_a_source_that_fails_is_left_out_the_rest_make_the_list(self):
        self.down = {'api.audius.co'}
        g = self.songs('rock')
        self.assertEqual({i['from'] for i in g['items']}, {'Deezer', 'Apple Music'})
        self.assertEqual(g['sub'], '来源：Deezer · Apple Music')

    def test_a_slow_source_is_not_waited_for(self):
        listen.GROUP_SECONDS = 0.4
        self.slow = {'api.audius.co': 1.5}
        t0 = time.time()
        g = self.songs('rock')
        self.assertLess(time.time() - t0, 1.2)
        self.assertEqual({i['from'] for i in g['items']}, {'Deezer', 'Apple Music'})

    def test_when_every_source_fails_the_list_says_so(self):
        self.down = {'api.deezer.com', 'api.audius.co', 'itunes.apple.com', 'applemarketingtools'}
        st, d = self.group('rock')
        self.assertEqual((st, d['error']), (502, 'empty'))

    def test_an_old_copy_is_served_when_a_source_stops_answering(self):
        a = [i['id'] for i in self.songs('metal')['items']]
        self.down = {'api.deezer.com', 'api.audius.co'}
        b = [i['id'] for i in self.songs('metal', now=NOW + listen.POOL_SECONDS + 60)['items']]
        self.assertEqual(a, b)

    def test_answers_that_are_not_json_or_not_shaped_are_a_failure_not_a_crash(self):
        for bad in ('<html>', '[]', 'null', '{"data": 5}', '{"feed": 3}'):
            listen._CACHE.clear()
            self.dz = bad
            old = listen._get
            listen._get = lambda url, headers=None, bad=bad: bad if 'deezer' in url else old(url, headers)
            try:
                g = self.songs('metal')
                self.assertEqual({i['from'] for i in g['items']}, {'Audius'})
            finally:
                listen._get = old

    def test_no_key_leaves_out_what_needs_one_and_says_why(self):
        g, report = next(x for x in listen.groups({}, NOW) if x['id'] == 'chill'), []
        items, names = listen.fetch_group(g, {}, NOW, report)
        self.assertEqual(names, ['Audius', 'Apple Music'])
        self.assertIn({'group': 'chill', 'source': 'jamendo:chill', 'ok': False, 'error': 'off'}, report)
        self.assertFalse([u for u in self.asked if 'jamendo' in u])                          # (a source without a key is not even asked)


class Licences(Base):
    def test_a_non_commercial_track_is_left_out_by_default(self):
        self.jamendo = json.dumps({'headers': {'status': 'success'}, 'results': [jm(1, 'http://creativecommons.org/licenses/by-nc-sa/3.0/')] + [jm(i) for i in range(2, 9)]})
        ids = [i['id'] for i in self.songs('sleep', {**self.env})['items'] if i['from'] == 'Jamendo']
        self.assertEqual(len(ids), 7)
        self.assertNotIn('jm:1001', ids)
        listen._CACHE.clear()
        ids = [i['id'] for i in self.songs('sleep', {**self.env, 'LISTEN_ALLOW_NC': '1'})['items'] if i['from'] == 'Jamendo']
        self.assertEqual(len(ids), 8)

    def test_a_track_without_a_known_licence_is_left_out(self):
        self.jamendo = json.dumps({'headers': {'status': 'success'}, 'results': [dict(jm(1), license_ccurl=''), dict(jm(2), license_ccurl='https://example.com/terms')] + [jm(i) for i in range(3, 8)]})
        self.assertEqual(len([i for i in self.songs('sleep')['items'] if i['from'] == 'Jamendo']), 5)

    def test_labels(self):
        self.assertEqual(listen.cc_label('https://creativecommons.org/licenses/by/4.0/'), ('CC BY 4.0', True))
        self.assertEqual(listen.cc_label('http://creativecommons.org/licenses/by-nc-nd/3.0/'), ('CC BY-NC-ND 3.0', False))
        self.assertEqual(listen.cc_label('https://creativecommons.org/publicdomain/zero/1.0/'), ('CC0', True))
        self.assertEqual(listen.cc_label('')[0], '')

    def test_a_sound_that_is_not_from_jamendo_is_not_played(self):
        self.jamendo = json.dumps({'headers': {'status': 'success'}, 'results': [jm(1, audio='https://evil.example/a.mp3'), jm(2, audio='javascript:1'), jm(3, audio='http://mp3d.jamendo.com/x.mp3')] + [jm(i) for i in range(4, 9)]})
        items = [i for i in self.songs('sleep')['items'] if i['from'] == 'Jamendo']
        self.assertEqual(len(items), 6)
        self.assertIn('jm:1003', [i['id'] for i in items])                                    # (an http address of Jamendo is made https)
        self.assertTrue(all(i['audio'].startswith('https://') for i in items))

    def test_jamendo_saying_no_is_a_clean_failure(self):
        self.jamendo = json.dumps({'headers': {'status': 'failed', 'code': 5, 'error_message': 'Jamendo Api Invalid Client Id Error: Your credential is not authorized.'}, 'results': []})
        g, report = next(x for x in listen.groups(self.env, NOW) if x['id'] == 'sleep'), []
        items, names = listen.fetch_group(g, self.env, NOW, report)
        self.assertEqual(names, ['Audius', 'Apple Music'])
        err = [r for r in report if r['source'].startswith('jamendo')][0]['error']
        self.assertIn('Invalid Client Id', err)
        self.assertNotIn('jmkey', json.dumps(report))

    def test_the_key_is_sent_to_the_source_and_nowhere_else(self):
        for gid in ('sleep', 'video'):
            st, ct, body, cache = listen.handle({'g': [gid]}, self.env, NOW)
            self.assertNotIn(b'jmkey', body)
            self.assertNotIn(b'ytkey', body)
        self.assertTrue(any('client_id=jmkey' in u for u in self.asked))
        self.assertTrue(any('key=ytkey' in u for u in self.asked))

    def test_focus_asks_for_instrumental_music_and_the_tags_are_joined_with_plus(self):
        self.songs('focus')
        u = [u for u in self.asked if 'fuzzytags=piano' in u][0]
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
        self.assertEqual(q['vocalinstrumental'], ['instrumental'])
        self.assertEqual(q['fuzzytags'], ['piano classical jazz instrumental'])             # ('+' arrives as a space: that is what Jamendo's "a+b" means)
        self.assertEqual((q['order'], q['groupby']), (['popularity_month'], ['artist_id']))


class Netease(Base):
    def setUp(self):
        super().setUp()
        self.env = {**self.env, 'LISTEN_NETEASE_DIRECT': '1'}
        self.checked = []
        listen.netease_outer = lambda sid: (self.checked.append(sid), int(sid) % 3 != 0)[1]

    def test_open_songs_become_whole_songs_and_the_others_are_left_out(self):
        items = self.songs('nep-3778678')['items']
        self.assertTrue(items and all(i['mode'] == 'stream' and i['audio'] == 'https://music.163.com/song/media/outer/url?id=%s.mp3' % i['id'][3:] for i in items))
        self.assertTrue(all(int(i['id'][3:]) % 3 != 0 for i in items))
        self.assertTrue(all(i['url'].startswith('https://music.163.com/#/song?id=') and i['dur'] > 100 and i['lic'] == '网易云音乐' and 'preview' not in i for i in items))
        self.assertEqual(self.group('nep-3778678')[1]['group']['name'], '网易云 · 热歌榜')

    def test_only_the_top_forty_are_checked_and_once_per_pool(self):
        self.songs('nep-3778678')
        self.assertEqual(len(self.checked), 40)
        self.songs('nep-3778678', now=NOW + 600)
        self.assertEqual(len(self.checked), 40)

    def test_a_chart_with_no_open_song_is_left_out(self):
        listen.netease_outer = lambda sid: False
        st, d = self.group('nep-3778678')
        self.assertEqual(st, 502)
        self.assertTrue(self.songs('hot-cn')['items'][0]['from'] == 'Apple Music')           # (and the list that has a back-up uses it)

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


class Video(Base):
    def test_music_videos_are_embedded_not_streamed(self):
        g = self.songs('video')
        self.assertEqual(g['net'], 'global')
        self.assertEqual(len(g['items']), 20)
        self.assertTrue(all(i['mode'] == 'embed' and i['net'] == 'global' and i['url'].startswith('https://www.youtube.com/watch?v=') for i in g['items']))
        self.assertTrue(any('videoCategoryId=10' in u for u in self.asked))


class Extra(Base):
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

    def test_the_owner_can_add_lists(self):
        self.write({'groups': [{'id': 'x-punk', 'name': '朋克', 'name_en': 'Punk', 'kind': 'style', 'sources': [['deezer', 152], ['audius', 'Punk'], ['apple_genre', 'us', 21], ['apple', 'xx'], ['tidal', 1], ['deezer', 'abc'], 'junk']},
                                {'id': 'punk', 'name': 'bad id', 'kind': 'style', 'sources': [['deezer', 1]]}, {'id': 'x-nothing', 'name': '空', 'kind': 'style', 'sources': []},
                                {'id': 'x-bad-kind', 'name': 'k', 'kind': 'daily', 'sources': [['deezer', 1]]}, 'junk']})
        ids = [g['id'] for g in self.index()['groups']]
        self.assertIn('x-punk', ids)
        self.assertEqual(ids.count('punk'), 0)
        self.assertNotIn('x-nothing', ids)
        self.assertNotIn('x-bad-kind', ids)
        g = self.songs('x-punk')
        self.assertEqual(g['sub'], '来源：Deezer · Audius · Apple Music')
        self.assertEqual(g['name_en'], 'Punk')

    def test_a_broken_file_changes_nothing(self):
        n = len(self.index()['groups'])
        for bad in ({'groups': 'x'}, {'groups': []}, ['a'], {'groups': [{'id': 'x-a'}]}):
            self.write(bad)
            self.assertEqual(len(self.index()['groups']), n)
        with open(os.path.join(self.dir, 'listen.json'), 'w') as f:
            f.write('{not json')
        self.assertEqual(len(self.index()['groups']), n)


class Combine(unittest.TestCase):
    def songs(self, tag, n):
        return [{'title': '%s %d' % (tag, i), 'artist': '%s artist %d' % (tag, i % 3)} for i in range(n)]

    def test_one_from_each_source_in_turn(self):
        out = listen.combine([self.songs('a', 3), self.songs('b', 3)], 's', 6, False, 9)
        self.assertEqual([x['title'] for x in out], ['a 0', 'b 0', 'a 1', 'b 1', 'a 2', 'b 2'])

    def test_no_artist_more_than_allowed_and_no_song_twice(self):
        a = self.songs('a', 9)                                                              # (three artists, three songs each)
        out = listen.combine([a, a[:]], 's', 30, False, 2)
        self.assertEqual(len(out), 6)
        self.assertEqual(len({(x['title'], x['artist']) for x in out}), 6)
        out = listen.combine([a], 's', 30, False, 1)
        self.assertEqual(len(out), 3)

    def test_it_ends_when_the_sources_are_empty_and_counts_are_kept(self):
        self.assertEqual(listen.combine([], 's', 5, True, 2), [])
        self.assertEqual(len(listen.combine([self.songs('a', 50)], 's', 30, True, 99)), 30)

    def test_the_shuffle_is_the_seeds(self):
        a = [x['title'] for x in listen.combine([self.songs('a', 20)], 'one', 20, True, 99)]
        self.assertEqual(a, [x['title'] for x in listen.combine([self.songs('a', 20)], 'one', 20, True, 99)])
        self.assertNotEqual(a, [x['title'] for x in listen.combine([self.songs('a', 20)], 'two', 20, True, 99)])


class Diag(Base):
    def test_diag_says_what_each_source_did_and_nothing_secret(self):
        st, ct, body, cache = listen.handle({'diag': ['1']}, self.env, NOW)
        d = json.loads(body)
        self.assertEqual((st, cache), (200, 'no-store'))
        self.assertEqual(d['keys'], {'JAMENDO_CLIENT_ID': True, 'YOUTUBE_API_KEY': True, 'LISTEN_NETEASE_DIRECT': False})
        self.assertEqual(set(d['songs']), {'daily', 'hot-cn', 'hot-west', 'rock', 'chill'})
        self.assertTrue(all(n >= 20 for n in d['songs'].values()))
        self.assertTrue(any(r['group'] == 'daily' and r['source'] == 'deezer:0' and r['ok'] for r in d['sources']))
        self.assertTrue(any(r['source'].startswith('netease') and r['error'] == 'off' for r in d['sources']))
        self.assertNotIn(b'jmkey', body)
        self.assertNotIn(b'ytkey', body)
        d0 = json.loads(listen.handle({'diag': ['1']}, {}, NOW)[2])
        self.assertEqual(d0['keys'], {'JAMENDO_CLIENT_ID': False, 'YOUTUBE_API_KEY': False, 'LISTEN_NETEASE_DIRECT': False})


class Probe(Base):
    def setUp(self):
        super().setUp()
        self.old_fetch, self.old_call = agg.fetch, listen._call
        self.sounds, self.calls = [], []

        def fetch(url, accept='*/*', max_bytes=0, headers=None, truncate=False, soft_xml=False):
            self.sounds.append((url, (headers or {}).get('Range')))
            if self.sound is None:
                raise agg.Refused('网站返回了错误（HTTP 403）', 'HTTP 403', 403)
            return url, {'content-type': self.sound[0]}, self.sound[1]
        self.sound = ('audio/mpeg', b'x' * 1024)
        agg.fetch = fetch
        listen._call = self.fake_call

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
        self.assertIn('结论：这些来源都能用。', text)
        providers = {u.split('/')[2] for u, _ in self.sounds}
        self.assertEqual(len(self.sounds), len(providers))                                   # (one sound for each provider, not for each list)
        self.assertTrue(all(r == 'bytes=0-1023' for _, r in self.sounds))                    # (a piece of it, not the whole song)
        self.assertGreaterEqual(len(self.sounds), 4)
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
        self.assertIn('jamendo:chill：没读到（off）', text)
        self.assertGreaterEqual(len(self.sounds), 3)                                         # (Deezer, Apple, Audius need no key)

    def test_youtube_key_that_is_not_allowed_says_why(self):
        self.answers = lambda url: (403, {'error': {'code': 403, 'message': 'x', 'errors': [{'reason': 'quotaExceeded'}]}}) if 'googleapis' in url else (200, {})
        text = self.run_probe()[1]
        self.assertIn('YouTube：不行（HTTP 403 quotaExceeded）：今天的配额用完了', text)
        self.assertNotIn('ytkey', text)
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
        ok, text = self.run_probe({**self.env, 'LISTEN_NETEASE_DIRECT': '1'})
        self.assertRegex(text, r'LISTEN_NETEASE_DIRECT\s+1：网易云的歌用站内播放条放整首')
        self.assertIn('声音（网易云音乐）：服务已确认', text)
        self.assertFalse([u for u, _ in self.sounds if 'music.163.com' in u])               # (NetEase's address is not fetched here: the browser test does that)

    def test_nothing_made(self):
        self.down = {'api.deezer.com', 'api.audius.co', 'itunes.apple.com', 'applemarketingtools', 'api.jamendo.com', 'googleapis'}
        ok, text = self.run_probe()
        self.assertFalse(ok)
        self.assertIn('一首能在站内播的歌都没有', text)


if __name__ == '__main__':
    unittest.main()
