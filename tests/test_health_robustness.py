"""Cycle M10: one malformed workflow-run entry must not blind the
watchdog.

Regression target: assess() filtered runs with run.get(...) but then
indexed run["id"] / run["created_at"] and parsed timestamps inside
max()/stale(); a single entry with a missing id or a bad timestamp
raised, main() caught it, and the whole assessment became "unknown"
even though every other input was fine.
"""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot import health


class HealthRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 14, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.config = {"timezone": "Asia/Tokyo",
                       "slots": [{"id": "noon", "time_jst": "12:00", "template": "hello"}]}
        self.ops = {"monitor_since_utc": (self.now - timedelta(hours=3)).isoformat(),
                    "expected_did": "did:plc:expected",
                    "native_heartbeat_minutes": 30,
                    "external_heartbeat_minutes": 90,
                    "post_grace_minutes": 30}
        self.run = {"id": 1, "path": ".github/workflows/daily-post.yml",
                    "event": "schedule", "status": "completed",
                    "created_at": (self.now - timedelta(minutes=1)).isoformat(),
                    "conclusion": "success"}
        self.feed = {"feed": [{"post": {
            "uri": "at://did:plc:expected/app.bsky.feed.post/key",
            "author": {"did": "did:plc:expected"},
            "record": {"$type": "app.bsky.feed.post", "text": "hello",
                       "createdAt": self.now.replace(hour=12, minute=3).isoformat(),
                       }}}]}

    def assess(self, runs):
        return health.assess({"workflow_runs": runs}, self.feed,
                             self.config, self.ops, self.now)

    def test_baseline_is_healthy(self):
        self.assertEqual(self.assess([self.run])["status"], "healthy")

    def test_malformed_entries_are_ignored(self):
        malformed = [
            None,
            "not-a-run",
            42,
            {"path": ".github/workflows/daily-post.yml"},  # almost everything missing
            {**self.run, "id": "1"},                        # id not an int
            {**self.run, "id": 2, "created_at": "not-a-date"},
            {**self.run, "id": 3, "created_at": None},
            {**self.run, "id": 4, "run_started_at": "garbage"},
            {**self.run, "id": 5, "created_at": 12345},
        ]
        result = self.assess([*malformed, self.run])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["latest_native_run"]["id"], 1)

    def test_malformed_only_means_stale_not_unknown(self):
        # With no usable run at all, the watchdog should report the
        # scheduler as stale (a real, actionable signal), not crash.
        result = self.assess([{**self.run, "id": "x"}, None])
        self.assertIn("native_scheduler_stale", result["problems"])
        self.assertEqual(result["status"], "unhealthy")

    def test_usable_run_helper(self):
        self.assertTrue(health._is_usable_automatic_run(self.run))
        self.assertFalse(health._is_usable_automatic_run(None))
        self.assertFalse(health._is_usable_automatic_run(
            {**self.run, "status": "queued"}))
        self.assertFalse(health._is_usable_automatic_run(
            {**self.run, "conclusion": "skipped"}))


if __name__ == "__main__":
    unittest.main()
