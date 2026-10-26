"""Podcasts: an episode's sound (address, type, length) comes through RSS / Atom / JSON Feed, the show's cover with it, and a feed longer than the size limit is read as far as it goes. Fake sites; no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
from test_feedapi import FeedBase  # noqa: E402

HEAD = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Learn English</title><link>https://show.example/</link><language>en</language><itunes:image href="https://show.example/cover.jpg"/>
'''
PODCAST = HEAD + '''<item><title>Ep 3</title><link>https://show.example/ep3</link><pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate>
  <enclosure url="https://cdn.example/ep3.mp3?x=1" length="123" type="audio/mpeg; charset=binary"/><itunes:duration>12:34</itunes:duration><itunes:image href="https://show.example/ep3.jpg"/></item>
<item><title>Ep 2, a page of its own is missing</title><guid isPermaLink="false">abc-2</guid><pubDate>Sun, 18 Oct 2026 08:00:00 GMT</pubDate>
  <enclosure url="https://cdn.example/ep2.m4a" length="9"/><itunes:duration>3600</itunes:duration></item>
<item><title>Ep 1 is a video</title><link>https://show.example/ep1</link><pubDate>Sat, 17 Oct 2026 08:00:00 GMT</pubDate><enclosure url="https://cdn.example/ep1.mp4" type="video/mp4"/></item>
<item><title>Ep 0 only on http</title><link>https://show.example/ep0</link><pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate><enclosure url="http://cdn.example/ep0.mp3" type="audio/mpeg"/></item>
<item><title>A script is not a sound</title><link>https://show.example/bad</link><pubDate>Thu, 15 Oct 2026 08:00:00 GMT</pubDate><enclosure url="javascript:alert(1)" type="audio/mpeg"/></item>
<item><title>A PDF is not a sound</title><link>https://show.example/pdf</link><pubDate>Wed, 14 Oct 2026 08:00:00 GMT</pubDate><enclosure url="https://cdn.example/notes.pdf" type="application/pdf"/></item>
<item><title>An article</title><link>https://show.example/article</link><pubDate>Tue, 13 Oct 2026 08:00:00 GMT</pubDate></item>
</channel></rss>'''
H = {'content-type': 'application/rss+xml'}


def parse(body, base='https://show.example/feed.xml', headers=H):
    shelf, items = agg.parse_feed(body.encode('utf-8') if isinstance(body, str) else body, headers, base)
    return shelf, {i['title']: i for i in items}


class TestSound(unittest.TestCase):
    def test_seconds(self):
        for raw, want in (('3600', 3600), ('1:02:03', 3723), ('62:03', 3723), ('754.5', 754), (3600.0, 3600), (' 90 ', 90), ('', 0), (None, 0), ('x', 0), ('-5', 0), ('0', 0), ('1:2:3:4', 0), ('9999999', 0), ('1:xx', 0)):
            self.assertEqual(agg._seconds(raw), want, raw)

    def test_rss_the_sound_the_length_and_the_covers(self):
        shelf, it = parse(PODCAST)
        self.assertEqual(shelf['image'], 'https://show.example/cover.jpg')
        e3 = it['Ep 3']
        self.assertEqual(e3['audio'], {'url': 'https://cdn.example/ep3.mp3?x=1', 'type': 'audio/mpeg', 'dur': 754})
        self.assertEqual((e3['type'], e3['image'], e3['url']), ('audio', 'https://show.example/ep3.jpg', 'https://show.example/ep3'))
        e2 = it['Ep 2, a page of its own is missing']
        self.assertEqual(e2['audio'], {'url': 'https://cdn.example/ep2.m4a', 'type': '', 'dur': 3600})            # no type: the file's own ending says it is a sound
        self.assertEqual((e2['url'], e2['type']), ('https://cdn.example/ep2.m4a', 'audio'))                       # and with no page, the sound is the address
        self.assertEqual(it['Ep 0 only on http']['audio']['url'], 'http://cdn.example/ep0.mp3')                    # kept as it is: the page decides what it can play
        for name in ('Ep 1 is a video', 'A script is not a sound', 'A PDF is not a sound', 'An article'):
            self.assertNotIn('audio', it[name], name)
        self.assertEqual(it['Ep 1 is a video']['type'], 'video')

    def test_atom_and_json_feed(self):
        atom = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><title>A show</title><logo>https://show.example/logo.png</logo>
          <entry><title>One</title><link rel="alternate" href="https://show.example/one"/><link rel="enclosure" type="audio/ogg" href="/one.ogg"/><itunes:duration>5:00</itunes:duration><updated>2026-10-19T08:00:00Z</updated></entry>
          <entry><title>Two</title><link rel="alternate" href="https://show.example/two"/><link rel="enclosure" type="application/zip" href="/two.zip"/><updated>2026-10-18T08:00:00Z</updated></entry></feed>'''
        shelf, it = parse(atom, headers={'content-type': 'application/atom+xml'})
        self.assertEqual(shelf['image'], 'https://show.example/logo.png')
        self.assertEqual(it['One']['audio'], {'url': 'https://show.example/one.ogg', 'type': 'audio/ogg', 'dur': 300})
        self.assertEqual(it['One']['type'], 'audio')
        self.assertNotIn('audio', it['Two'])
        js = json.dumps({'version': 'https://jsonfeed.org/version/1.1', 'title': 'J', 'icon': 'https://show.example/i.png', 'items': [
            {'id': '1', 'url': 'https://show.example/j1', 'title': 'J one', 'date_published': '2026-10-19T08:00:00Z', 'attachments': [{'url': 'https://cdn.example/j1.mp3', 'mime_type': 'audio/mpeg', 'duration_in_seconds': 61}]},
            {'id': '2', 'url': 'https://show.example/j2', 'title': 'J two', 'date_published': '2026-10-18T08:00:00Z', 'attachments': [{'url': 'https://cdn.example/j2.pdf', 'mime_type': 'application/pdf'}]}]})
        shelf, it = parse(js, headers={'content-type': 'application/feed+json'})
        self.assertEqual(shelf['image'], 'https://show.example/i.png')
        self.assertEqual(it['J one']['audio'], {'url': 'https://cdn.example/j1.mp3', 'type': 'audio/mpeg', 'dur': 61})
        self.assertNotIn('audio', it['J two'])

    def test_a_feed_without_sound_is_as_it_was(self):
        shelf, it = parse(HEAD.replace('<itunes:image href="https://show.example/cover.jpg"/>', '') + '<item><title>A</title><link>https://show.example/a</link></item></channel></rss>')
        self.assertEqual(shelf['image'], '')
        self.assertNotIn('audio', it['A'])
        self.assertEqual(it['A']['type'], 'article')


class TestLongFeeds(FeedBase):
    def long_rss(self, n, tail=''):
        rows = ''.join('<item><title>Episode %d</title><link>https://show.example/e%d</link><pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate><description>%s</description>'
                       '<enclosure url="https://cdn.example/e%d.mp3" type="audio/mpeg"/><itunes:duration>600</itunes:duration></item>\n' % (i, i, 'about the episode ' * 40, i) for i in range(n, 0, -1))
        return HEAD + rows + tail

    def test_a_podcasts_whole_history_is_read_as_far_as_it_goes_with_whole_items(self):
        body = self.long_rss(3500, '</channel></rss>')
        self.assertGreater(len(body.encode()), agg.MAX_BYTES)
        self.site.put('/long.xml', body, 'application/rss+xml')
        st, out = self.update([{'url': self.u + '/long.xml'}])
        r = out['results'][0]
        self.assertEqual((st, r['status'], r['title']), (200, 'ok', 'Learn English'))
        self.assertEqual(len(r['items']), agg.FEED_KEEP)
        self.assertTrue(all(i['audio']['url'].startswith('https://cdn.example/e') and i['audio']['dur'] == 600 for i in r['items']))
        self.assertEqual(r['image'], 'https://show.example/cover.jpg')
        self.assertTrue(any(i['title'] == 'Episode 3500' for i in r['items']))                                    # the newest are first, and they are there

    def test_the_same_through_discover(self):
        self.site.put('/long.xml', self.long_rss(3500, '</channel></rss>'), 'application/rss+xml')
        st, body = self.discover(self.u + '/long.xml')
        self.assertEqual((st, body['kind'], body['title'], body['image']), (200, 'feed', 'Learn English', 'https://show.example/cover.jpg'))
        self.assertTrue(body['items'][0]['audio']['url'].startswith('https://cdn.example/'))

    def test_atom_cut_at_the_limit(self):
        rows = ''.join('<entry><title>Entry %d</title><link rel="alternate" href="https://show.example/e%d"/><updated>2026-10-19T08:00:00Z</updated><summary>%s</summary></entry>\n' % (i, i, 'text ' * 120) for i in range(4000))
        self.site.put('/long.atom', '<feed xmlns="http://www.w3.org/2005/Atom"><title>Long</title>' + rows + '</feed>', 'application/atom+xml')
        st, out = self.update([{'url': self.u + '/long.atom'}])
        self.assertEqual((st, out['results'][0]['status'], len(out['results'][0]['items'])), (200, 'ok', agg.FEED_KEEP))

    def test_the_limit_still_holds_for_everything_that_is_not_a_feed(self):
        self.site.put('/big.html', b'x' * (agg.MAX_BYTES + 10), 'text/html')
        self.site.put('/garbage.xml', b'<rss><channel>' + b'x' * (agg.MAX_BYTES + 10), 'application/rss+xml')      # cut, and no whole item before the cut: not a feed
        for path in ('/big.html',):
            st, body = self.discover(self.u + path)
            self.assertEqual((st, body['error']), (422, 'unreachable'), path)
            self.assertIn('太大', body['message'])
        st, out = self.update([{'url': self.u + '/garbage.xml'}])
        self.assertEqual(out['results'][0]['status'], 'error')

    def test_what_the_service_hands_out_has_the_sound_only_when_there_is_one(self):
        self.site.put('/p.xml', PODCAST, 'application/rss+xml')
        st, out = self.update([{'url': self.u + '/p.xml'}])
        items = {i['title']: i for i in out['results'][0]['items']}
        self.assertEqual(items['Ep 3']['audio'], {'url': 'https://cdn.example/ep3.mp3?x=1', 'type': 'audio/mpeg', 'dur': 754})
        self.assertEqual(set(items['Ep 3']['audio']), {'url', 'type', 'dur'})
        self.assertNotIn('audio', items['An article'])
        self.assertEqual([h[0] for h in self.site.hits if h[0] != '/robots.txt'], ['/p.xml'])                      # nothing but the feed was asked for: the sound is not fetched here


if __name__ == '__main__':
    unittest.main()
