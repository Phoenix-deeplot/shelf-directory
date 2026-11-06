"""Music sources (music_sources.py): search, find the whole song, the pool of a recommender, the lists of a page that asks for `caps=resolve`. Every answer is a fixed sample (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest tests.test_music_sources
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
import music_sources as ms  # noqa: E402
import test_listen as tl  # noqa: E402

NOW = tl.NOW
KUWO = 'http://car-er.kuwo.cn/token123/1793000000/shape.flac'


def bodian_search_answer(rows=None):
    rows = rows if rows is not None else [('Shape of You', 'Ed Sheeran'), ('Shape of You (Live)', 'Ed Sheeran'), ('Shape of You 伴奏', '某人'), ('Perfect', 'Ed Sheeran')]
    return json.dumps({'code': 200, 'text': 'ok', 'data': [{'name': n, 'artist': a, '_singer': a, 'detail_page': 'https://www.kuwo.cn/play_detail/%d' % i, 'pic': 'http://img1.kuwo.cn/star/albumcover/%d.jpg' % i} for i, (n, a) in enumerate(rows)]})


def bodian_detail_answer(name='Shape of You', artist='Ed Sheeran', play=KUWO, lrc='[00:01.00]The club isn\'t the best place\n[00:05.00]to find a lover'):
    return json.dumps({'code': 200, 'data': {'name': name, 'artist': artist, 'cover': 'http://img1.kuwo.cn/star/albumcover/1.jpg', 'detail_page': 'https://www.kuwo.cn/play_detail/1', 'play_url': play, 'lrc': lrc}})


def dy_answer(n=65):
    return json.dumps({'code': 200, 'message': 'ok', 'total': n, 'data': [{'id': i, 'title': '抖音歌%d' % i, 'artist': '歌手%d' % (i % 7), 'pic': 'http://p3.douyinpic.com/%d.jpg' % i, 'url': 'https://x', 'lyric': 'x'} for i in range(n)]})


def deezer_search_answer():
    return json.dumps({'data': [{'id': 31001, 'title': 'Shape of You', 'duration': 234, 'preview': 'https://cdnt-preview.dzcdn.net/api/1/1/shape.mp3', 'link': 'https://www.deezer.com/track/31001', 'artist': {'name': 'Ed Sheeran'},
                                 'album': {'cover_medium': 'https://cdn-images.dzcdn.net/images/cover/shape/250x250.jpg'}},
                                {'id': 31002, 'title': 'Shape of You (Remix)', 'duration': 200, 'preview': 'https://cdnt-preview.dzcdn.net/api/1/1/remix.mp3', 'link': 'https://www.deezer.com/track/31002', 'artist': {'name': 'Someone'}, 'album': {}}]})


def itunes_search_answer():
    return json.dumps({'resultCount': 2, 'results': [{'kind': 'song', 'trackName': 'Shape of You', 'artistName': 'Ed Sheeran', 'trackTimeMillis': 233712, 'artworkUrl100': 'https://is1-ssl.mzstatic.com/a/100x100bb.jpg',
                                                      'previewUrl': 'https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/shape.m4a', 'trackViewUrl': 'https://music.apple.com/us/album/shape/1?i=2'},
                                                     {'kind': 'feature-movie', 'trackName': 'not a song'}]})


def audius_search_answer():
    return json.dumps({'data': [{'id': 'ASHAPE1', 'title': 'Shape of You', 'duration': 232, 'is_streamable': True, 'permalink': '/ed/shape-of-you', 'user': {'name': 'Ed Sheeran'}, 'artwork': {'480x480': 'https://cdn.audius.example/s.jpg'}},
                                {'id': 'ASHAPE2', 'title': 'Shape of You cover', 'duration': 200, 'is_streamable': True, 'permalink': '/x/y', 'user': {'name': 'fan'}}]})


def netease_search_answer():
    return json.dumps({'result': {'songs': [{'id': 4001, 'name': 'Shape of You', 'duration': 233000, 'artists': [{'name': 'Ed Sheeran'}], 'album': {'picUrl': 'http://p1.music.126.net/s.jpg'}}, {'id': '../x', 'name': 'bad'}]}})


class Base(tl.Base):
    def setUp(self):
        super().setUp()
        ms.HEALTH.clear()
        ms._DY.clear()
        listen.STATS['stale'] = 0
        listen.COVERS.clear()
        self.env = {}                                          # (no Jamendo key, no NetEase unless a test turns them on)
        self.heads = []
        self.head_ok = (True, 31000000)
        self.bodian_detail = bodian_detail_answer()
        self.bodian_rows = bodian_search_answer()
        self.old_head = ms.head_ok
        ms.head_ok = self.fake_head

    def tearDown(self):
        ms.head_ok = self.old_head
        super().tearDown()

    def fake_head(self, url, timeout=3):
        self.heads.append(url)
        return self.head_ok

    def fake(self, url, headers=None):
        if 'api.xcvts.cn/api/music/bdyy' in url:
            self.asked.append(url)
            self.maybe_slow(url)
            return self.bodian_detail if '&n=' in url else self.bodian_rows
        if 'api.xcvts.cn/api/hotlist/dy/music' in url:
            self.asked.append(url)
            self.maybe_slow(url)
            return dy_answer()
        if 'api.deezer.com/search' in url:
            self.asked.append(url)
            self.maybe_slow(url)
            return deezer_search_answer()
        if 'itunes.apple.com/search' in url:
            self.asked.append(url)
            self.maybe_slow(url)
            return itunes_search_answer()
        if 'api.audius.co/v1/tracks/search' in url:
            self.asked.append(url)
            self.maybe_slow(url)
            return audius_search_answer()
        if 'music.163.com/api/search' in url:
            self.asked.append(url)
            return netease_search_answer()
        return super().fake(url, headers)

    def maybe_slow(self, url):
        for k in self.down:
            if k in url:
                raise agg.Refused('网站返回了错误（HTTP 500）', 'HTTP 500')
        for k, secs in self.slow.items():
            if k in url:
                time.sleep(secs)

    def call(self, **q):
        st, ct, body, cache = listen.handle({k: [str(v)] for k, v in q.items()}, self.env, NOW)
        return st, json.loads(body), cache

    def asked_to(self, part):
        return [u for u in self.asked if part in u]


class Parsers(Base):
    def test_bodian_rows(self):
        rows = ms.bodian_rows(self.bodian_rows)
        self.assertEqual([r['title'] for r in rows], ['Shape of You', 'Shape of You (Live)', 'Shape of You 伴奏', 'Perfect'])
        self.assertEqual(rows[0]['n'], 1)
        self.assertTrue(rows[0]['cover'].startswith('https://'))

    def test_bodian_rows_empty_and_bad(self):
        self.assertEqual(ms.bodian_rows(json.dumps({'code': 200, 'data': []})), [])
        for bad in ('not json', json.dumps({'code': 500}), json.dumps({'data': 'x'}), json.dumps([1, 2])):
            with self.assertRaises((agg.Refused, ValueError)):
                ms.bodian_rows(bad)

    def test_bodian_detail_is_https_and_only_from_kuwo(self):
        d = ms.bodian_detail(bodian_detail_answer())
        self.assertEqual(d['audio'], 'https://car-er.kuwo.cn/token123/1793000000/shape.flac')
        self.assertIn('[00:01.00]', d['lrc'])
        self.assertEqual(ms.bodian_detail(bodian_detail_answer(play='http://evil.example/x.flac'))['audio'], '')
        self.assertEqual(ms.bodian_detail(bodian_detail_answer(play='http://car-er.kuwo.cn.evil.example/x.flac'))['audio'], '')
        self.assertEqual(ms.bodian_detail(bodian_detail_answer(play=''))['audio'], '')
        with self.assertRaises(agg.Refused):
            ms.bodian_detail(json.dumps({'code': 404, 'data': None}))

    def test_dychart_rows(self):
        rows = ms.dychart_rows(dy_answer(5))
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r['resolve'] and r['cands'] == [] and r['audio'] == '' and r['from'] == '抖音热歌榜' for r in rows))
        self.assertEqual([r['hot'] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual(len({r['id'] for r in rows}), 5)
        self.assertEqual(rows[0]['id'], ms.dychart_rows(dy_answer(5))[0]['id'])                 # (the same song, the same id, on any day)
        with self.assertRaises(agg.Refused):
            ms.dychart_rows(json.dumps({'data': []}))
        with self.assertRaises(agg.Refused):
            ms.dychart_rows(json.dumps({'data': 'x'}))
        with self.assertRaises((agg.Refused, ValueError)):
            ms.dychart_rows('<html>')

    def test_search_rows_of_the_other_sources(self):
        d = ms.deezer_search_rows(deezer_search_answer())
        self.assertEqual((d[0]['title'], d[0]['cand']['k'], d[0]['cand']['p']), ('Shape of You', 'preview', 'deezer'))
        a = ms.itunes_search_rows(itunes_search_answer())
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]['dur'], a[0]['cand']['p'], a[0]['cand']['k']), (233, 'apple', 'preview'))
        self.assertIn('300x300', a[0]['cover'])
        u = ms.audius_search_rows(audius_search_answer())
        self.assertEqual((u[0]['cand']['p'], u[0]['cand']['k']), ('audius', 'full'))
        n = ms.netease_search_rows(netease_search_answer())
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]['artist'], 'Ed Sheeran')
        self.assertEqual(n[0]['cand']['ne_id'], 4001)
        for fn in (ms.deezer_search_rows, ms.itunes_search_rows, ms.audius_search_rows, ms.netease_search_rows):
            with self.assertRaises((agg.Refused, ValueError)):
                fn('<html>')
        self.assertEqual(ms.itunes_search_rows(json.dumps({'results': []})), [])
        self.assertEqual(ms.netease_search_rows(json.dumps({'result': {}})), [])


class Matching(Base):
    def test_same_song(self):
        S = ms.same_song
        self.assertTrue(S('Shape of You', 'Ed Sheeran', 233, 'Shape Of You (Radio Edit)', 'Ed Sheeran', 234))
        self.assertTrue(S('晴天', '周杰伦', 269, '晴天', '周杰伦', 270))
        self.assertTrue(S('Love Story', 'Taylor Swift', 0, 'Love Story', 'Taylor Swift feat. Someone', 0))
        self.assertFalse(S('Love Story', 'Taylor Swift', 0, 'Love Story', 'Andy Williams', 0))
        self.assertFalse(S('Shape of You', 'Ed Sheeran', 233, 'Perfect', 'Ed Sheeran', 233))
        self.assertFalse(S('Shape of You', 'Ed Sheeran', 233, 'Shape of You', 'Ed Sheeran', 300))          # (a different length: not the same recording)
        self.assertTrue(S('Shape of You', 'Ed Sheeran', 30, 'Shape of You', 'Ed Sheeran', 233))             # (a preview says nothing about the length)

    def test_the_same_artist_written_two_ways(self):
        S = ms.same_song
        self.assertTrue(S('晴天', '周杰倫', 269, '晴天', '周杰伦', 269))                                      # (traditional and simplified characters)
        self.assertTrue(S('七里香', '陳奕迅', 0, '七里香', '陈奕迅', 0))
        self.assertTrue(S('断了的弦', 'Jay 周杰伦', 298, '断了的弦', '周杰伦', 298))
        self.assertFalse(S('晴天', '周杰伦', 269, '晴天', '田各田各', 200))
        self.assertFalse(S('Perfect', 'a', 0, 'Perfect', 'Ed Sheeran', 0))                                   # (a one-letter artist is inside every name: it must not match them all)

    def test_the_artist_in_front_of_the_title_is_not_the_title(self):
        self.assertEqual(ms.clean_title('周杰伦-七里香', '周杰伦'), '七里香')
        self.assertEqual(ms.clean_title('周杰伦 - 完 美主 义', 'Jay 周杰伦'), '完 美主 义')
        self.assertEqual(ms.clean_title('Shape of You - Remastered 2017', 'Ed Sheeran'), 'Shape of You')
        self.assertEqual(ms.clean_title('Shape of You (Live at the Apollo)'), 'Shape of You')
        self.assertTrue(ms.same_song('周杰伦-断了的弦', 'Jay 周杰伦', 298, '断了的弦', '周杰伦', 0))

    def test_wanted_looks_at_words_not_at_letters(self):
        self.assertTrue(ms.wanted('Alive', 'alive'))
        self.assertTrue(ms.wanted('Alive', 'x'))                                                             # ("live" is not in "Alive")
        self.assertTrue(ms.wanted('Olivia', 'olivia'))
        self.assertFalse(ms.wanted('Someone (Bootleg)', 'someone'))

    def test_wanted(self):
        self.assertFalse(ms.wanted('Shape of You (Live)', 'shape of you'))
        self.assertFalse(ms.wanted('Shape of You - Remix', 'shape of you'))
        self.assertFalse(ms.wanted('晴天 伴奏', '晴天'))
        self.assertTrue(ms.wanted('Shape of You (Live)', 'shape of you live'))
        self.assertTrue(ms.wanted('Shape of You', 'shape of you'))

    def test_merge_makes_one_song_of_the_same_song(self):
        rows = [('deezer', ms.deezer_search_rows(deezer_search_answer())), ('apple', ms.itunes_search_rows(itunes_search_answer())), ('audius', ms.audius_search_rows(audius_search_answer())),
                ('bodian', ms.provider_rows('bodian', 'Shape of You', {}, 'en'))]
        songs = ms.merge(rows, 'shape of you')
        top = songs[0]
        self.assertEqual(top['title'], 'Shape of You')
        self.assertEqual({c['p'] for c in top['cands']}, {'deezer', 'apple', 'audius', 'bodian'})
        self.assertEqual(top['cands'][0]['k'], 'full')                                                      # (whole songs before previews)
        self.assertEqual([c['k'] for c in top['cands']][-2:], ['preview', 'preview'])
        self.assertEqual(top['dur'], 233)
        self.assertTrue(top['audio'].startswith('https://'))
        titles = [s['title'] for s in songs]
        self.assertNotIn('Shape of You (Remix)', titles)                                                    # live, remix, cover and 伴奏 are left out unless asked for
        self.assertNotIn('Shape of You cover', titles)
        self.assertNotIn('Shape of You (Live)', titles)
        self.assertNotIn('Shape of You 伴奏', titles)
        self.assertNotIn('Perfect', titles)                                                                  # (none of the words of the query: a chance find)
        self.assertIn('Perfect', [s['title'] for s in ms.merge(rows, 'ed sheeran')])                        # (the artist's other song, when the artist is what was asked for)
        self.assertEqual(len({s['id'] for s in songs}), len(songs))
        self.assertTrue(all(s['id'].startswith('sr:') for s in songs))

    def test_several_rows_of_one_source_for_one_song_are_one_sound(self):
        rows = ms.provider_rows('bodian', 'x', {}, 'zh')
        rows = rows + [dict(rows[0]), dict(rows[0])]
        songs = ms.merge([('bodian', rows)], 'shape of you')
        self.assertEqual(len([s for s in songs if s['title'] == 'Shape of You']), 1)
        self.assertEqual(len(songs[0]['cands']), 1)

    def test_the_songs_with_the_words_of_the_query_come_first_and_the_chance_finds_are_left_out(self):
        by_chance = ms.audius_search_rows(json.dumps({'data': [{'id': 'AAAA1', 'title': 'Totally Else', 'duration': 200, 'is_streamable': True, 'user': {'name': 'someone'}}]}))
        rows = [('audius', by_chance), ('deezer', ms.deezer_search_rows(deezer_search_answer()))]
        songs = ms.merge(rows, 'shape of you')
        self.assertEqual([s['title'] for s in songs], ['Shape of You'])
        songs = ms.merge([('audius', by_chance)], 'zzz')                                                      # (when nothing has the words, what there is stays)
        self.assertEqual([s['title'] for s in songs], ['Totally Else'])
        a = ms.deezer_search_rows(deezer_search_answer())
        b = [dict(a[0], title='Shape of You Live Tribute', artist='Zed')]
        self.assertEqual(ms.relevance(['Shape of You Ed Sheeran'], 'shape ed'), 1.0)
        self.assertEqual(ms.relevance(['Shape of You Ed Sheeran'], 'shape zzz'), 0.5)
        self.assertEqual(ms.relevance(['晴天 周杰倫', '晴天 周杰伦'], '晴天 周杰伦'), 1.0)                       # (the spelling of any source counts)
        self.assertEqual(b[0]['title'], 'Shape of You Live Tribute')

    def test_the_song_that_is_the_title_comes_before_the_medley(self):
        rows = [('apple', [ms._row('晴天', '周杰倫', 269, '', ms.cand('apple', 'preview', 'https://audio-ssl.itunes.apple.com/x.m4a'))]),
                ('bodian', [ms._row('晴天+告白气球', '周杰伦', 0, '', ms.cand('bodian', 'full', resolve=True)), ms._row('晴天', '周杰伦', 0, '', ms.cand('bodian', 'full', resolve=True))])]
        songs = ms.merge(rows, '晴天 周杰伦')
        self.assertEqual([s['title'] for s in songs], ['晴天', '晴天+告白气球'])
        self.assertEqual({c['p'] for c in songs[0]['cands']}, {'apple', 'bodian'})

    def test_a_song_with_only_a_preview_says_so_and_one_with_no_address_asks_for_resolve(self):
        songs = ms.merge([('deezer', ms.deezer_search_rows(deezer_search_answer()))], 'shape of you')
        self.assertTrue(songs[0]['preview'])
        songs = ms.merge([('bodian', ms.provider_rows('bodian', 'Perfect', {}, 'en'))], 'perfect')
        self.assertTrue(all(s.get('resolve') and s['audio'] == '' and not s.get('preview') for s in songs))

    def test_cands_of_the_readers_side_come_first(self):
        cs = [dict(ms.cand('a', 'full', 'https://x/1'), net='global'), dict(ms.cand('b', 'full', 'https://x/2'), net='cn')]
        self.assertEqual([c['p'] for c in ms.sort_cands(cs, 'cn')], ['b', 'a'])
        self.assertEqual([c['p'] for c in ms.sort_cands(cs, 'global')], ['a', 'b'])


class Search(Base):
    def test_every_source_is_asked_and_the_answer_is_merged(self):
        songs, partial = ms.search('Shape of You', self.env, 'en', 'global')
        self.assertFalse(partial)
        self.assertEqual({c['p'] for c in songs[0]['cands']}, {'deezer', 'apple', 'audius', 'bodian'})
        for part in ('api.xcvts.cn/api/music/bdyy', 'api.deezer.com/search', 'itunes.apple.com/search', 'api.audius.co/v1/tracks/search'):
            self.assertEqual(len(self.asked_to(part)), 1, part)
        self.assertEqual(self.asked_to('api.jamendo.com'), [])                                            # (no key: not asked)
        self.assertIn('country=us', self.asked_to('itunes.apple.com/search')[0])

    def test_jamendo_is_asked_with_a_key(self):
        self.env = {'JAMENDO_CLIENT_ID': 'jmkey'}
        self.jamendo = tl.jamendo_answer(3)
        songs, partial = ms.search('Jam 1', self.env, 'zh', 'cn')
        self.assertEqual(len(self.asked_to('api.jamendo.com')), 1)
        self.assertIn('namesearch=Jam%201', self.asked_to('api.jamendo.com')[0])
        self.assertTrue(any(c['p'] == 'jamendo' for s in songs for c in s['cands']))
        self.assertIn('country=tw', self.asked_to('itunes.apple.com/search')[0])                          # (China's store answers no search; Taiwan's has the Chinese songs)

    def test_one_source_failing_does_not_fail_the_search(self):
        self.down = {'api.deezer.com/search', 'api.audius.co/v1/tracks/search'}
        songs, partial = ms.search('Shape of You', self.env, 'en', 'global')
        self.assertTrue(partial)
        self.assertEqual({c['p'] for c in songs[0]['cands']}, {'apple', 'bodian'})

    def test_a_slow_source_is_left_out_and_the_answer_says_partial(self):
        self.slow = {'api.audius.co/v1/tracks/search': 1.5}
        t0 = time.time()
        songs, partial = ms.search('Shape of You', self.env, 'en', 'global', deadline=0.4)
        self.assertLess(time.time() - t0, 1.2)
        self.assertTrue(partial)
        self.assertNotIn('audius', {c['p'] for s in songs for c in s['cands']})
        self.assertIn('deezer', {c['p'] for s in songs for c in s['cands']})

    def test_nothing_found_is_an_answer_not_a_failure(self):
        for body in (json.dumps({'data': [], 'total': 0}),):
            self.assertEqual(ms.deezer_search_rows(body), [])
            self.assertEqual(ms.audius_search_rows(body), [])
        self.assertEqual(ms.audius_search_rows(json.dumps({'data': [{'id': 'GONE1', 'title': 'x', 'is_streamable': False}]})), [])
        orig = deezer_search_answer
        globals()['deezer_search_answer'] = lambda: json.dumps({'data': [], 'total': 0})
        try:
            for _ in range(4):
                songs, partial = ms.search('晴天 周杰伦', self.env, 'zh', 'cn')
        finally:
            globals()['deezer_search_answer'] = orig
        self.assertEqual(ms.HEALTH['deezer']['fail'], 0)
        self.assertFalse(ms.paused('deezer'))                                                                # (an empty answer four times does not rest the source)
        self.assertEqual(ms.HEALTH['deezer']['ok'], 4)

    def test_all_sources_failing_is_an_empty_answer_not_a_crash(self):
        self.down = {'api.deezer.com/search', 'api.audius.co/v1/tracks/search', 'itunes.apple.com/search', 'api.xcvts.cn'}
        songs, partial = ms.search('Shape of You', self.env, 'en', 'global')
        self.assertEqual((songs, partial), ([], True))

    def test_bodian_off_means_bodian_is_never_asked(self):
        self.env = {'LISTEN_BODIAN': '0'}
        songs, partial = ms.search('Shape of You', self.env, 'en', 'global')
        self.assertEqual(self.asked_to('api.xcvts.cn'), [])
        self.assertNotIn('bodian', {c['p'] for s in songs for c in s['cands']})

    def test_the_endpoint(self):
        st, d, cache = self.call(search='Shape of You', lang='en')
        self.assertEqual(st, 200)
        self.assertTrue(d['ok'])
        self.assertEqual(d['q'], 'Shape of You')
        self.assertNotIn('partial', d)
        self.assertEqual(d['items'][0]['title'], 'Shape of You')
        self.assertIn('s-maxage=600', cache)
        self.assertEqual(self.call(search='   ')[0], 400)                                                  # (nothing to search for)
        self.down = {'api.deezer.com/search'}
        st, d, cache = self.call(search='Shape of You', lang='en')
        self.assertTrue(d['partial'])
        self.assertNotIn('s-maxage=600', cache)                                                          # (a partial answer is not kept long)

    def test_the_query_is_cleaned(self):
        st, d, _ = self.call(search='a\x00b<script>' + 'x' * 200)
        self.assertEqual(st, 200)
        self.assertLessEqual(len(d['q']), 60)
        self.assertNotIn('<', d['q'])
        self.assertNotIn('\x00', d['q'])


class Resolve(Base):
    def test_the_whole_song_with_its_lyrics(self):
        cands, lyrics = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'zh', 'cn')
        self.assertEqual(cands[0]['p'], 'bodian')                                                           # (a reader in China: the source of China first)
        self.assertEqual(cands[0]['k'], 'full')
        self.assertTrue(cands[0]['audio'].startswith('https://car-er.kuwo.cn/'))
        self.assertTrue(cands[0]['big'])
        self.assertEqual(cands[0]['size'], 31000000)
        self.assertTrue(cands[0]['lyr'])
        self.assertEqual(self.heads, ['https://car-er.kuwo.cn/token123/1793000000/shape.flac'])           # (the address that was checked is the one that is given)
        self.assertEqual({c['p'] for c in cands}, {'bodian', 'audius', 'apple', 'deezer'})
        self.assertEqual(lyrics['fmt'], 'lrc')
        self.assertIn('lover', lyrics['text'])
        self.assertEqual(lyrics['from'], '波点音乐')
        self.assertEqual(ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')[0][0]['p'], 'audius')       # (a reader abroad: Audius first, the lossless file of China after)

    def test_an_address_that_does_not_answer_is_not_given(self):
        self.head_ok = (False, 0)
        cands, lyrics = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        self.assertNotIn('bodian', {c['p'] for c in cands})
        self.assertIn('audius', {c['p'] for c in cands})
        self.assertIsNotNone(lyrics)                                                                         # (the lyrics are still the song's)

    def test_a_live_version_is_not_taken_for_the_song(self):
        self.bodian_rows = bodian_search_answer([('Shape of You (Live)', 'Ed Sheeran'), ('Shape of You 伴奏', 'Ed Sheeran')])
        cands, lyrics = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        self.assertNotIn('bodian', {c['p'] for c in cands})
        self.assertEqual(self.asked_to('&n='), [])

    def test_one_source_down_the_others_still_answer(self):
        self.down = {'api.xcvts.cn'}
        cands, lyrics = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        self.assertNotIn('bodian', {c['p'] for c in cands})
        self.assertTrue(cands)
        self.assertIsNone(lyrics)

    def test_all_down_is_an_empty_list(self):
        self.down = {'api.xcvts.cn', 'api.deezer.com/search', 'api.audius.co/v1/tracks/search', 'itunes.apple.com/search'}
        self.assertEqual(ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global'), ([], None))

    def test_a_slow_source_does_not_hold_the_answer(self):
        self.slow = {'api.xcvts.cn': 2.0}
        t0 = time.time()
        cands, lyrics = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global', deadline=0.5)
        self.assertLess(time.time() - t0, 1.5)
        self.assertNotIn('bodian', {c['p'] for c in cands})
        self.assertIn('audius', {c['p'] for c in cands})

    def test_netease_only_when_turned_on_and_only_a_song_open_to_everybody(self):
        cands, _ = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'cn')
        self.assertEqual(self.asked_to('music.163.com'), [])
        self.env = {'LISTEN_NETEASE_DIRECT': '1'}
        listen.netease_outer = lambda sid: True
        cands, _ = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'cn')
        ne = [c for c in cands if c['p'] == 'netease']
        self.assertEqual(len(ne), 1)
        self.assertEqual(ne[0]['audio'], 'https://music.163.com/song/media/outer/url?id=4001.mp3')
        self.assertNotIn('ne_id', ne[0])
        listen.netease_outer = lambda sid: False
        cands, _ = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'cn')
        self.assertNotIn('netease', {c['p'] for c in cands})

    def test_the_breaker_rests_a_source_after_three_failures(self):
        self.down = {'api.xcvts.cn'}
        for _ in range(3):
            ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        n = len(self.asked_to('api.xcvts.cn'))
        self.assertEqual(n, 3)
        self.assertTrue(ms.paused('bodian'))
        ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        self.assertEqual(len(self.asked_to('api.xcvts.cn')), n)                                              # (not asked while it rests)
        report = {p['id']: p for p in ms.providers_report(self.env)}
        self.assertEqual(report['bodian']['fail'], 3)
        self.assertGreater(report['bodian']['resting_for'], 500)
        ms.HEALTH['bodian']['until'] = time.time() - 1                                                        # (ten minutes later)
        self.down = set()
        cands, _ = ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        self.assertIn('bodian', {c['p'] for c in cands})
        self.assertEqual(ms.HEALTH['bodian']['streak'], 0)

    def test_a_format_that_changed_counts_as_a_failure(self):
        self.bodian_rows = json.dumps({'code': 200, 'data': 'nope'})
        for _ in range(3):
            ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        self.assertTrue(ms.paused('bodian'))

    def test_the_endpoint(self):
        st, d, cache = self.call(resolve=1, t='Shape of You', a='Ed Sheeran', d=233, id='dz:31001', lang='zh')
        self.assertEqual(st, 200)
        self.assertTrue(d['ok'])
        self.assertEqual(d['id'], 'dz:31001')
        self.assertEqual(d['ttl'], 600)                                                                       # (a Bodian address does not live long)
        self.assertEqual(d['cands'][0]['p'], 'bodian')
        self.assertEqual(d['lyrics']['fmt'], 'lrc')
        self.assertIn('s-maxage=60', cache)
        self.assertNotIn('size', json.dumps(d['lyrics']))

    def test_the_endpoint_with_nothing_found_is_not_cached(self):
        self.down = {'api.xcvts.cn', 'api.deezer.com/search', 'api.audius.co/v1/tracks/search', 'itunes.apple.com/search'}
        st, d, cache = self.call(resolve=1, t='Shape of You', a='Ed Sheeran')
        self.assertEqual((st, d['cands'], cache), (200, [], 'no-store'))
        self.assertNotIn('lyrics', d)

    def test_the_endpoint_needs_a_title(self):
        st, d, cache = self.call(resolve=1, a='Ed Sheeran')
        self.assertEqual((st, d['ok'], cache), (400, False, 'no-store'))
        st, d, _ = self.call(resolve=1, t='x', id='../../etc/passwd')
        self.assertEqual(d['id'], '')                                                                         # (an id that is not one is dropped, not echoed)


class Pool(Base):
    def test_the_pool_of_each_side(self):
        for side in ('cn', 'global'):
            st, d, cache = self.call(pool=side, lang='zh' if side == 'cn' else 'en')
            self.assertEqual(st, 200, d)
            self.assertTrue(d['items'])
            self.assertIn('s-maxage=3600', cache)
            for x in d['items']:
                self.assertEqual(set(x), {'id', 'title', 'artist', 'cover', 'dur', 'rank', 'market', 'tags', 'from'})        # (nothing about a reader goes in or out)
            self.assertEqual(len({(x['title'], x['artist']) for x in d['items']}), len(d['items']))
        cn = self.call(pool='cn')[1]['items']
        self.assertIn('cn', {x['market'] for x in cn})
        self.assertTrue(all(x['rank'] >= 1 for x in cn))
        gl = self.call(pool='global', lang='en')[1]['items']
        self.assertIn('west', {x['market'] for x in gl})
        self.assertIn('rock', {t for x in gl for t in x['tags']})

    def test_a_bad_side(self):
        st, d, _ = self.call(pool='mars')
        self.assertEqual((st, d['ok']), (400, False))

    def test_the_pool_is_stale_when_the_sources_stop_answering(self):
        first = self.call(pool='global', lang='en')[1]
        self.assertNotIn('stale', first)
        later = NOW + listen.POOL_SECONDS + 3600
        self.down = {'api.deezer.com', 'itunes.apple.com', 'api.audius.co'}
        st, ct, body, cache = listen.handle({'pool': ['global'], 'lang': ['en']}, self.env, later)
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertTrue(d['stale'])
        self.assertEqual(len(d['items']), len(first['items']))


class Catalogue(Base):
    def test_a_page_without_caps_sees_exactly_what_it_saw(self):
        d = self.call()[1]
        ids = [g['id'] for g in d['groups']]
        self.assertNotIn('dy-hot', ids)
        self.assertNotIn('region', d)
        self.assertTrue(all(not ({'region', 'cover', 'resolve'} & set(g)) for g in d['groups']))
        kinds = {g['id']: g['kind'] for g in d['groups']}
        self.assertEqual((kinds['chill'], kinds['focus'], kinds['sleep']), ('style', 'style', 'style'))
        self.assertNotIn('workout', ids)
        self.assertEqual(self.call(g='dy-hot')[0], 404)
        st, d, _ = self.call(g='hot-cn')
        item = d['group']['items'][0]
        self.assertFalse({'cands', 'hot', 'market'} & set(item))
        self.assertNotIn('stale', d)

    def test_a_page_with_caps_resolve_sees_the_new_lists(self):
        d = self.call(caps='resolve', lang='zh')[1]
        self.assertEqual(d['region'], 'cn')
        by = {g['id']: g for g in d['groups']}
        self.assertEqual(by['dy-hot']['region'], 'cn')
        self.assertTrue(by['dy-hot']['resolve'])
        self.assertEqual([by[i]['kind'] for i in ('chill', 'focus', 'sleep', 'workout', 'commute', 'study')], ['scene'] * 6)
        self.assertEqual((by['hot-cn']['region'], by['hot-west']['region'], by['kr']['region'], by['jp']['region'], by['rock']['region']), ('cn', 'west', 'jp-kr', 'jp-kr', 'global'))
        self.assertEqual(by['pod-us']['region'], 'west')
        kinds = [g['kind'] for g in d['groups']]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: listen.KIND_ORDER[k]))
        self.assertTrue(all(g.get('region') or g['id'] == 'daily' for g in d['groups']))

    def test_the_lists_of_the_readers_side_come_first(self):
        def charts(**q):
            return [g['id'] for g in self.call(caps='resolve', **q)[1]['groups'] if g['kind'] == 'chart' and g['type'] == 'music']
        cn = charts(region='cn', lang='en')
        gl = charts(region='global', lang='zh')
        self.assertIn('dy-hot', cn)
        self.assertLess(cn.index('hot-cn'), cn.index('hot-west'))
        self.assertLess(gl.index('hot-west'), gl.index('hot-cn'))
        self.assertEqual(self.call(caps='resolve', region='global', lang='zh')[1]['region'], 'global')
        self.assertEqual(self.call(caps='resolve', region='auto', lang='en')[1]['region'], 'global')           # (auto: by the reader's language, no IP lookup)
        self.assertEqual(self.call(caps='resolve', region='auto', lang='zh')[1]['region'], 'cn')
        self.assertEqual(self.call(caps='resolve', region='mars', lang='zh')[1]['region'], 'cn')

    def test_play_lists_have_their_covers(self):
        self.dz_lists = json.dumps({'data': [{'id': 7001, 'title': 'bedroom rave', 'nb_tracks': 50, 'picture_medium': 'https://cdn-images.dzcdn.net/images/playlist/aa/250x250.jpg'}]})
        self.au_lists = json.dumps({'data': [{'id': 'PLaaa', 'playlist_name': 'Hot & New', 'track_count': 20, 'artwork': {'480x480': 'https://cdn.audius.example/pl.jpg'}}]})
        by = {g['id']: g for g in self.call(caps='resolve')[1]['groups']}
        self.assertTrue(by['dzp-7001']['cover'].startswith('https://cdn-images.dzcdn.net/'))
        self.assertEqual(by['aup-PLaaa']['cover'], 'https://cdn.audius.example/pl.jpg')
        self.assertNotIn('cover', by['rock'])
        old = {g['id']: g for g in self.call()[1]['groups']}
        self.assertNotIn('cover', old['dzp-7001'])

    def test_the_douyin_chart_has_names_and_no_sounds(self):
        st, d, _ = self.call(g='dy-hot', caps='resolve')
        self.assertEqual(st, 200, d)
        g = d['group']
        self.assertTrue(g['resolve'])
        self.assertEqual(g['sub'], '来源：抖音热歌榜')
        self.assertEqual(len(g['items']), 21)                                                               # (the sample has 7 artists, and a chart keeps three songs of one artist at most)
        self.assertTrue(all(x['audio'] == '' and x['cands'] == [] and x['resolve'] for x in g['items']))
        self.assertLessEqual(max(sum(1 for x in g['items'] if x['artist'] == a) for a in {x['artist'] for x in g['items']}), 3)
        self.assertEqual([x['hot'] for x in g['items']][:3], [1, 2, 3])
        self.assertEqual(g['items'][0]['market'], 'cn')

    def test_the_douyin_chart_is_off_with_bodian_off(self):
        self.env = {'LISTEN_BODIAN': '0'}
        ids = [g['id'] for g in self.call(caps='resolve')[1]['groups']]
        self.assertNotIn('dy-hot', ids)
        self.assertEqual(self.call(g='dy-hot', caps='resolve')[0], 404)
        self.assertEqual(self.asked_to('api.xcvts.cn'), [])

    def test_the_douyin_chart_fails_alone(self):
        self.down = {'api.xcvts.cn'}
        self.assertEqual(self.call(g='dy-hot', caps='resolve')[0], 502)
        st, d, _ = self.call(g='hot-cn', caps='resolve')
        self.assertEqual(st, 200)

    def test_the_songs_of_a_list_have_their_sounds_their_place_and_their_market(self):
        st, d, _ = self.call(g='hot-cn', caps='resolve')
        self.assertEqual(st, 200, d)
        items = d['group']['items']
        self.assertEqual([x['hot'] for x in items][:3], [1, 2, 3])
        self.assertTrue(all(x['market'] == 'cn' for x in items))
        for x in items:
            self.assertTrue(x['cands'], x)
            self.assertEqual(x['cands'][0]['audio'], x['audio'])
            self.assertIn(x['cands'][0]['k'], ('preview', 'full'))
            self.assertEqual(x['cands'][0]['p'] in ('apple',), x['cands'][0]['k'] == 'preview')
        st, d, _ = self.call(g='rock', caps='resolve')
        self.assertTrue(all('market' not in x for x in d['group']['items']))                                  # (global: no market)

    def test_a_song_on_two_charts_of_one_list_has_both_sounds(self):
        a = [{'id': 'dz:1', 'title': 'Same', 'artist': 'X', 'cover': '', 'dur': 30, 'mode': 'stream', 'audio': 'https://cdnt-preview.dzcdn.net/1.mp3', 'url': '', 'lic': 'Deezer 30 秒试听', 'from': 'Deezer', 'preview': True}]
        b = [{'id': 'au:1', 'title': 'Same', 'artist': 'X', 'cover': '', 'dur': 200, 'mode': 'stream', 'audio': 'https://api.audius.co/v1/tracks/1/stream', 'url': '', 'lic': 'Audius', 'from': 'Audius'}]
        g = listen.STATIC[0]
        out = ms.with_sounds(listen.combine([a, b], 'x', 10, False, 2), [a, b], dict(g, kind='chart'))
        self.assertEqual(len(out), 1)
        self.assertEqual([c['p'] for c in out[0]['cands']], ['audius', 'deezer'])                            # (the whole song before the preview)
        self.assertEqual(a[0].get('cands'), None)                                                           # (the cached lists are not changed)

    def test_the_stale_flag(self):
        self.assertNotIn('stale', self.call(g='hot-west', caps='resolve')[1])
        later = NOW + listen.POOL_SECONDS + 3600
        self.down = {'api.deezer.com', 'itunes.apple.com'}
        st, ct, body, cache = listen.handle({'g': ['hot-west'], 'caps': ['resolve']}, self.env, later)
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertTrue(d['stale'])
        st, ct, body, cache = listen.handle({'g': ['hot-west']}, self.env, later + 1)
        self.assertNotIn('stale', json.loads(body))                                                         # (an old page does not get the new field)


class Diag(Base):
    def test_providers_in_diag(self):
        ms.resolve('Shape of You', 'Ed Sheeran', 233, self.env, 'en', 'global')
        d = self.call(diag=1)[1]
        by = {p['id']: p for p in d['providers']}
        self.assertEqual(set(by), {'bodian', 'dychart', 'netease', 'audius', 'jamendo', 'apple', 'deezer'})
        self.assertTrue(by['bodian']['on'])
        self.assertGreaterEqual(by['bodian']['ok'], 1)
        self.assertIn('last_ok_at', by['bodian'])
        self.assertFalse(by['netease']['on'])
        self.assertFalse(by['jamendo']['on'])
        for k in ('keys', 'groups', 'songs', 'sources'):
            self.assertIn(k, d)                                                                              # (the old fields are still there)

    def test_bodian_off_is_off_in_diag(self):
        self.env = {'LISTEN_BODIAN': '0'}
        by = {p['id']: p for p in self.call(diag=1)[1]['providers']}
        self.assertFalse(by['bodian']['on'])
        self.assertFalse(by['dychart']['on'])

    def test_no_secret_in_diag(self):
        self.env = {'JAMENDO_CLIENT_ID': 'SECRETKEY123', 'YOUTUBE_API_KEY': 'SECRETYT456'}
        self.jamendo = tl.jamendo_answer(3)
        ms.search('Jam', self.env, 'zh', 'cn')
        st, ct, body, cache = listen.handle({'diag': ['1']}, self.env, NOW)
        text = body.decode('utf-8')
        self.assertNotIn('SECRETKEY123', text)
        self.assertNotIn('SECRETYT456', text)
        self.assertTrue(json.loads(text)['providers'][4]['on'])


class OffSwitch(Base):
    def test_listen_off_turns_every_new_question_off(self):
        self.env = {'LISTEN': '0'}
        for q in ({'search': 'x'}, {'resolve': 1, 't': 'x'}, {'pool': 'cn'}):
            self.assertEqual(self.call(**q)[0], 404)
        self.assertEqual(self.asked, [])


if __name__ == '__main__':
    unittest.main()
