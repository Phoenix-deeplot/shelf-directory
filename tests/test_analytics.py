"""Validation and storage contract tests for anonymous hot-list analytics."""
import datetime as dt
import unittest
import uuid
from unittest.mock import patch

import analyticsapi
from submission import Reply


SECRET = {"ANALYTICS_HASH_SECRET": "test-only-secret", "ALLOWED_ORIGINS": "https://books.zhitree.cn"}
ANON = str(uuid.uuid4())
SESSION = str(uuid.uuid4())


def event(**overrides):
    value = {"event_id": str(uuid.uuid4()), "event_name": "item_impression",
             "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(), "experiment_id": "hot-layout-v1",
             "variant": "control", "anon_id": ANON, "session_id": SESSION, "module_slug": "featured",
             "content_key": "dir:item-1", "program_id": "source-one", "platform_id": "weibo",
             "locale": "zh-CN", "country_code": "CN", "device_class": "mobile", "active_seconds": 0}
    value.update(overrides)
    return value


class FakeCursor:
    def __init__(self, experiments=None, incomplete=False):
        self.experiments = experiments or []
        self.incomplete = incomplete
        self.rows = []
        self.rowcount = 0
        self.insert_params = []

    def execute(self, query, params=()):
        self.rowcount = 0
        if "information_schema.columns" in query:
            for table, columns in {
                "analytics_experiments": ["experiment_id", "name", "status", "allocation", "variants", "starts_at", "ends_at", "updated_at"],
                "analytics_events": ["event_id", "occurred_at", "experiment_id", "variant", "anon_id_hash", "session_id", "event_name", "module_slug", "content_key", "program_id", "platform_id", "locale", "country_code", "device_class", "active_seconds"],
            }.items():
                for col in columns[:-1] if self.incomplete else columns:
                    self.rows.append({"table_name": table, "column_name": col})
        elif "FROM ops.analytics_experiments WHERE experiment_id=%s" in query:
            self.rows = [x for x in self.experiments if x["experiment_id"] == params[0]]
        elif "FROM ops.analytics_experiments WHERE experiment_id=ANY" in query:
            self.rows = [x for x in self.experiments if x["experiment_id"] in params[0]]
        elif "INSERT INTO ops.analytics_events" in query:
            self.insert_params.append(params)
            self.rowcount = 1
            self.rows = []
        else:
            self.rows = []

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class FakeConnection:
    def __init__(self, cursor): self.cur = cursor
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self.cur


class AnalyticsTests(unittest.TestCase):
    def setUp(self): analyticsapi._RATE_BUCKETS.clear()

    def test_event_allowlist_and_value_limits(self):
        self.assertIsNotNone(analyticsapi._clean_event(event()))
        for bad in (event(event_name="arbitrary"), event(unbounded={"x": 1}), event(active_seconds=901),
                    event(anon_id="192.0.2.2"), event(country_code="USA"), event(device_class="bot")):
            self.assertIsNone(analyticsapi._clean_event(bad))

    def test_public_config_missing_or_inactive_is_off(self):
        cur = FakeCursor()
        with patch.object(analyticsapi, "_connect", return_value=FakeConnection(cur)):
            result = analyticsapi.public_config("hot-layout-v1", SECRET)
        self.assertEqual(result, {"ok": True, "experiment": {"id": "hot-layout-v1", "status": "off", "allocation": {}, "variants": []}})

    def test_public_config_active_variant_shape_and_allocation(self):
        cur = FakeCursor([{"experiment_id": "hot-layout-v1", "status": "active", "allocation": {"control": 50, "treatment": 50},
                           "variants": [{"id": "control", "label": "Control"}, {"id": "treatment", "label": "New"}],
                           "starts_at": None, "ends_at": None}])
        with patch.object(analyticsapi, "_connect", return_value=FakeConnection(cur)):
            result = analyticsapi.public_config("hot-layout-v1", SECRET)
        self.assertEqual(result["experiment"]["allocation"], {"control": 50, "treatment": 50})
        self.assertEqual(result["experiment"]["variants"], [{"id": "control", "label": "Control"}, {"id": "treatment", "label": "New"}])

    def test_ingest_hashes_anonymous_id_and_counts_idempotent_insert(self):
        cur = FakeCursor([{"experiment_id": "hot-layout-v1", "status": "active", "variants": [{"id": "control"}]}])
        with patch.object(analyticsapi, "_connect", return_value=FakeConnection(cur)):
            result = analyticsapi.ingest_events({"events": [event()]}, SECRET, "203.0.113.4", now=1000)
        self.assertEqual(result, {"ok": True, "accepted": 1, "rejected": 0})
        stored = cur.insert_params[0]
        self.assertNotEqual(stored[4], ANON)
        self.assertEqual(len(stored[4]), 64)

    def test_origin_batch_limit_and_schema_missing(self):
        with self.assertRaises(Reply) as ctx:
            analyticsapi.handle_public("POST", {"events": True}, {"events": [event()]}, "https://evil.example", "x", SECRET, 12)
        self.assertEqual(ctx.exception.code, "origin")
        with self.assertRaises(Reply) as ctx:
            analyticsapi.handle_public("POST", {"events": True}, {"events": [event()]}, "https://books.zhitree.cn", "x", SECRET, analyticsapi._MAX_BODY + 1)
        self.assertEqual(ctx.exception.code, "size")
        cur = FakeCursor(incomplete=True)
        with patch.object(analyticsapi, "_connect", return_value=FakeConnection(cur)):
            with self.assertRaises(Reply) as ctx:
                analyticsapi.public_config("hot-layout-v1", SECRET)
        self.assertEqual(ctx.exception.code, "schema")


if __name__ == "__main__": unittest.main()
