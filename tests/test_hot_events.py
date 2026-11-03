"""Hot-list snapshots and event APIs; all external sources and persistence are isolated."""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import hotapi  # noqa: E402


class MemoryHotStore:
    def __init__(self):
        self.charts = {}
        self.sources = {}
        self.topics = {}
        self.last_refresh = 0
        self.refresh_locked = False

    def save_chart(self, pid, region, fetched_at, rows):
        day = datetime.fromtimestamp(fetched_at, timezone.utc).date()
        key = (pid, region or '', day)
        inserted = key not in self.charts
        if inserted:
            self.charts[key] = (day, datetime.fromtimestamp(fetched_at, timezone.utc), rows)
        return inserted

    def chart_week(self, pid, region, now):
        today = datetime.fromtimestamp(now, timezone.utc).date()
        snapshots = [v for (p, r, d), v in self.charts.items() if p == pid and r == (region or '') and (today.toordinal() - 6) <= d.toordinal() <= today.toordinal()]
        return hotapi._aggregate_week(sorted(snapshots, key=lambda s: s[0]))

    def latest_event_refresh(self):
        return self.last_refresh

    def try_event_refresh_lock(self):
        if self.refresh_locked:
            return None
        self.refresh_locked = True
        return True

    def release_event_refresh_lock(self, lock):
        self.refresh_locked = False

    def save_events(self, candidates, now):
        for topic in hotapi.EVENT_TOPICS:
            self.topics.setdefault(topic['id'], topic)
        for item in candidates:
            key = (item['event_id'], item['url'])
            if key not in self.sources:
                self.sources[key] = {**item, 'first_seen_at': now}
            else:
                self.sources[key].update({**item, 'first_seen_at': self.sources[key]['first_seen_at']})
        self.last_refresh = now
        return len(candidates)

    def list_events(self, lang, limit, now):
        results = []
        for topic in hotapi.EVENT_TOPICS:
            sources = [s for (eid, _), s in self.sources.items() if eid == topic['id'] and now - s.get('published_at', 0) < hotapi.EVENT_RETENTION_DAYS * 86400]
            if not sources:
                continue
            sources.sort(key=lambda s: s.get('published_at', 0), reverse=True)
            recent = sum(1 for s in sources if now - s['first_seen_at'] < 86400)
            previous = sum(1 for s in sources if 86400 <= now - s['first_seen_at'] < 172800)
            results.append({'id': topic['id'], 'title': topic['title_en'] if lang == 'en' else topic['title'],
                            'category': topic['category'], 'last_updated': max(s.get('published_at', 0) for s in sources),
                            'source_count': len({s['source'] for s in sources}),
                            'trend': 'rising' if recent > previous and recent >= 2 else ('cooling' if previous > recent and previous >= 2 else 'steady'),
                            'pinned': False,
                            'items': [{'title': s['title'], 'url': s['url'], 'source': s['source'],
                                       'published_at': s.get('published_at', 0), 'excerpt': s.get('excerpt', '')} for s in sources[:12]]})
        results.sort(key=lambda e: e['last_updated'], reverse=True)
        return results[:limit]


def unpack(response):
    return response[0], json.loads(response[2])


class TestChartPeriods(unittest.TestCase):
    def setUp(self):
        self.store = MemoryHotStore()
        self.env = {'DATABASE_URL': 'isolated-test-database'}
        self.start = 1793577600  # 2026-11-02 00:00:00 UTC

    def query(self, period='day', now=None):
        rows = [{'title': 'Alpha', 'url': 'https://example.test/a', 'heat': '', 'note': '', 'alt': '', 'date': ''},
                {'title': 'Beta', 'url': 'https://example.test/b', 'heat': '', 'note': '', 'alt': '', 'date': ''}]
        with patch.object(hotapi, '_store', return_value=self.store), patch.object(hotapi, 'entries', return_value=(rows, now or self.start)):
            return unpack(hotapi.handle({'p': ['weibo'], 'format': ['json'], 'period': [period]}, self.env, now=now or self.start))

    def test_default_and_day_keep_the_current_rows_and_save_one_snapshot_per_utc_date(self):
        status, data = self.query('day')
        self.assertEqual(status, 200)
        self.assertEqual((data['period'], data['items'][0]['title']), ('day', 'Alpha'))
        self.assertTrue(data['history']['available'])
        self.assertEqual(len(self.store.charts), 1)
        self.query('day', self.start + 3600)
        self.assertEqual(len(self.store.charts), 1)  # same UTC date is idempotent

    def test_week_uses_persisted_rank_snapshots_not_the_current_upstream_response(self):
        self.query('day', self.start)
        next_day = self.start + 86400
        self.query('day', next_day)
        with patch.object(hotapi, '_store', return_value=self.store), patch.object(hotapi, 'entries', side_effect=AssertionError('week must not fetch a live list')):
            status, data = unpack(hotapi.handle({'p': ['weibo'], 'format': ['json'], 'period': ['week']}, self.env, now=next_day))
        self.assertEqual(status, 200)
        self.assertEqual((data['period'], data['history']['snapshots'], data['history']['complete']), ('week', 2, False))
        self.assertEqual(data['items'][0]['appearances'], 2)
        self.assertIn('weekly_score', data['items'][0])

    def test_week_without_database_or_snapshots_is_an_empty_non_fabricated_result(self):
        with patch.object(hotapi, '_store', return_value=None):
            status, data = unpack(hotapi.handle({'p': ['weibo'], 'format': ['json'], 'period': ['week']}, {}, now=self.start))
        self.assertEqual(status, 200)
        self.assertEqual((data['items'], data['history']['available']), ([], False))


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.store = MemoryHotStore()
        self.env = {'DATABASE_URL': 'isolated-test-database', 'HOT_REFRESH_SECRET': 'a-long-test-secret-value'}
        self.now = 1793577600
        self.items = [
            {'event_id': 'iran-israel', 'title': 'Iran and Israel update', 'url': 'https://news-a.example/1', 'source': 'news-a.example', 'published_at': self.now - 60, 'excerpt': 'Short excerpt.'},
            {'event_id': 'iran-israel', 'title': 'Iran Israel developments', 'url': 'https://news-b.example/2', 'source': 'news-b.example', 'published_at': self.now - 120, 'excerpt': ''},
            {'event_id': 'ai-news', 'title': 'OpenAI model update', 'url': 'https://news-c.example/ai', 'source': 'news-c.example', 'published_at': self.now - 180, 'excerpt': ''},
        ]

    def test_event_refresh_requires_bearer_secret_and_is_throttled(self):
        fetcher = Mock(return_value=self.items)
        with patch.object(hotapi, '_store', return_value=self.store):
            status, denied = unpack(hotapi.handle_event_refresh('GET', {}, self.env, now=self.now, fetcher=fetcher))
            self.assertEqual((status, denied['error']), (401, 'unauthorized'))
            self.assertFalse(fetcher.called)

            headers = {'authorization': 'Bearer ' + self.env['HOT_REFRESH_SECRET']}
            status, first = unpack(hotapi.handle_event_refresh('GET', headers, self.env, now=self.now, fetcher=fetcher))
            status2, cached = unpack(hotapi.handle_event_refresh('GET', headers, self.env, now=self.now + 30, fetcher=fetcher))
            status3, forced = unpack(hotapi.handle_event_refresh('POST', headers, self.env, {'force': ['1']}, now=self.now + 31, fetcher=fetcher))
        self.assertEqual((status, first['candidate_count'], first['stored_count']), (200, 3, 3))
        self.assertEqual((status2, cached['cached'], status3, forced['cached'], fetcher.call_count), (200, True, 200, False, 2))

    def test_events_have_stable_contract_localized_titles_and_linked_sources(self):
        self.store.save_events(self.items, self.now)
        with patch.object(hotapi, '_store', return_value=self.store):
            status, zh = unpack(hotapi.handle_events({'lang': ['zh'], 'limit': ['4']}, self.env, self.now))
            status_en, en = unpack(hotapi.handle_events({'lang': ['en-US'], 'limit': ['1']}, self.env, self.now))
        self.assertEqual(status, 200)
        self.assertEqual(set(zh), {'ok', 'updated', 'events'})
        iran = next(e for e in zh['events'] if e['id'] == 'iran-israel')
        self.assertEqual((iran['title'], iran['category'], iran['source_count'], iran['trend']), ('伊以局势', 'world', 2, 'rising'))
        self.assertEqual(set(iran['items'][0]), {'title', 'url', 'source', 'published_at', 'excerpt'})
        self.assertEqual((status_en, en['events'][0]['title']), (200, 'Iran–Israel tensions'))

    def test_public_events_refreshes_on_first_read_then_obeys_cache_window(self):
        fetcher = Mock(return_value=self.items)
        with patch.object(hotapi, '_store', return_value=self.store), patch.object(hotapi, '_event_candidates', fetcher):
            status, first = unpack(hotapi.handle_events({'lang': ['zh']}, self.env, self.now))
            status2, second = unpack(hotapi.handle_events({'lang': ['zh']}, self.env, self.now + 30))
        self.assertEqual((status, first['updated'], len(first['events'])), (200, self.now, 2))
        self.assertEqual((status2, second['updated'], fetcher.call_count), (200, self.now, 1))

    def test_public_events_serves_stale_rows_when_refresh_fails_or_another_instance_holds_lock(self):
        self.store.save_events(self.items, self.now - hotapi.EVENT_CACHE_SECONDS - 1)
        fetcher = Mock(side_effect=hotapi.agg.Refused('offline', 'offline'))
        with patch.object(hotapi, '_store', return_value=self.store), patch.object(hotapi, '_event_candidates', fetcher):
            status, stale = unpack(hotapi.handle_events({}, self.env, self.now))
        self.assertEqual((status, stale['updated'], len(stale['events'])), (200, self.now - hotapi.EVENT_CACHE_SECONDS - 1, 2))
        self.assertFalse(self.store.refresh_locked)

        self.store.refresh_locked = True
        with patch.object(hotapi, '_store', return_value=self.store), patch.object(hotapi, '_event_candidates', Mock(side_effect=AssertionError('lock busy should not fetch'))):
            status_busy, busy = unpack(hotapi.handle_events({}, self.env, self.now + 1))
        self.assertEqual((status_busy, busy['updated'], len(busy['events'])), (200, self.now - hotapi.EVENT_CACHE_SECONDS - 1, 2))

    def test_unconfigured_event_store_is_explicit(self):
        with patch.object(hotapi, '_store', return_value=None):
            status, data = unpack(hotapi.handle_events({}, {}, self.now))
        self.assertEqual((status, data['error']), (503, 'database'))

    def test_gdelt_and_curated_feeds_are_normalized_without_images_or_long_text(self):
        payload = {'articles': [
            {'title': 'Iran and Israel exchange updates', 'url': 'https://world.example/news/1?utm_source=x', 'domain': 'world.example', 'seendate': '20261102T010203Z', 'socialimage': 'https://world.example/large.jpg'},
            {'title': 'Russia and Ukraine developments', 'url': 'https://world.example/news/2', 'domain': 'world.example', 'seendate': '20261102T010203Z'},
            {'title': 'Gold price rises in global markets', 'url': 'https://finance.example/gold', 'domain': 'finance.example', 'seendate': '20261102T010203Z'},
            {'title': 'Unrelated celebrity news', 'url': 'https://culture.example/story', 'domain': 'culture.example', 'seendate': '20261102T010203Z'},
        ]}
        with patch.object(hotapi.agg, 'fetch', return_value=('https://api.gdeltproject.org/', {'content-type': 'application/json'}, json.dumps(payload).encode())), \
                patch.object(hotapi, 'entries', return_value=([], self.now)):
            results = hotapi._event_candidates({}, self.now)
        self.assertEqual({x['event_id'] for x in results}, {'iran-israel', 'russia-ukraine', 'gold-market'})
        self.assertTrue(all('img' not in x and len(x['excerpt']) <= 280 for x in results))
        iran = next(x for x in results if x['event_id'] == 'iran-israel')
        self.assertEqual((iran['source'], iran['url'], iran['published_at']), ('world.example', 'https://world.example/news/1', self.now + 3723))


class TestHotMigration(unittest.TestCase):
    def test_migration_is_additive_and_repeatable(self):
        source = (Path(__file__).resolve().parents[1] / 'migrations' / '007_hotlist_events.sql').read_text(encoding='utf-8').lower()
        self.assertIn('create schema if not exists hot', source)
        for table in ('chart_snapshots', 'event_topics', 'event_sources', 'event_refresh_state'):
            self.assertIn('create table if not exists hot.' + table, source)
        self.assertNotIn('drop table', source)
        self.assertNotIn('drop schema', source)


if __name__ == '__main__':
    unittest.main()
