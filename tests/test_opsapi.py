"""Authentication boundary tests for operations analytics actions."""
import unittest
from unittest.mock import patch

import opsapi
import analyticsapi
from submission import Reply


class OpsAnalyticsAuthTests(unittest.TestCase):
    def test_analytics_actions_require_the_existing_password_session(self):
        with patch.object(opsapi.adminapi, "settings", return_value={"configured": True}), \
             patch.object(opsapi.adminapi, "configured", return_value=True), \
             patch.object(opsapi.adminapi, "check_session", return_value=False):
            status, body = opsapi.handle({"action": "analytics-summary", "experiment_id": "hot-layout-v1"}, "expired", "", {})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "session")

    def test_summary_rejects_ranges_over_90_days_before_querying(self):
        with self.assertRaises(Reply) as ctx:
            analyticsapi._summary(object(), {"experiment_id": "hot-layout-v1", "from": "2026-01-01", "to": "2026-04-02"})
        self.assertEqual(getattr(ctx.exception, "code", None), "range")


if __name__ == "__main__": unittest.main()
