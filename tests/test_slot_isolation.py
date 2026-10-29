"""Cycle M6: an unexpected exception in one slot must not take down the
remaining due slots or the execution report.

Regression target: post_daily.main called run_slot directly in the loop;
run_slot only converts *expected* failures (BskyError, StateError) into
outcomes. Anything else (a bug, a ValueError from facet building, ...)
propagated out of main, skipping later slots and losing finish_report.
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot import health, post_daily

TZ = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 10, 30, 10, 5, tzinfo=TZ)  # eight slots due (08:15-10:00)


def _fake_run_slot_factory(fail_slot_id):
    def fake(slot, config, scheduled_at, state_path, *, post, outcomes=None,
             expected_did=None):
        if slot["id"] == fail_slot_id:
            raise RuntimeError("simulated unexpected bug")
        if outcomes is not None:
            outcomes.append({
                "slot_id": slot["id"],
                "scheduled_at": scheduled_at.isoformat(),
                "status": "posted",
                "uri": "at://did:plc:test/app.bsky.feed.post/x",
            })
        return 0
    return fake


class SlotIsolationTests(unittest.TestCase):
    def _run_due(self, fail_slot_id):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            state_path = Path(tmp) / "state.json"
            fixed = mock.Mock(wraps=datetime)
            fixed.now.return_value = NOW
            with mock.patch.object(post_daily, "datetime", fixed), \
                    mock.patch.object(post_daily, "run_slot",
                                      _fake_run_slot_factory(fail_slot_id)), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = post_daily.main([
                    "--due", "--post",
                    "--state", str(state_path),
                    "--report", str(report_path),
                ])
            return code, json.loads(report_path.read_text(encoding="utf-8"))

    def test_first_slot_crash_does_not_skip_second(self):
        code, report = self._run_due("recruit_morning")
        self.assertEqual(code, 1)
        statuses = [o["status"] for o in report["outcomes"]]
        self.assertIn("internal_error", statuses)
        self.assertIn("posted", statuses)
        self.assertEqual(report["counts"].get("internal_error"), 1)
        self.assertEqual(report["counts"].get("posted"), 7)  # the other due slots
        self.assertEqual(report["exit_code"], 1)

    def test_second_slot_crash_still_reports_first(self):
        # promo_morning (10:00) is the last of the eight due slots.
        code, report = self._run_due("promo_morning")
        self.assertEqual(code, 1)
        statuses = [o["status"] for o in report["outcomes"]]
        self.assertEqual(statuses[0], "posted")
        self.assertEqual(statuses[-1], "internal_error")
        self.assertEqual(len(statuses), 8)

    def test_no_crash_keeps_exit_zero(self):
        code, report = self._run_due("no_such_slot")
        self.assertEqual(code, 0)
        self.assertNotIn("internal_error", report["counts"])


class HealthInternalErrorTests(unittest.TestCase):
    def _execution(self, exit_code):
        finished = datetime.now(timezone.utc)
        return {
            "exit_code": exit_code,
            "outcomes": [{"status": "internal_error", "detail": "boom"}],
            "counts": {"internal_error": 1},
            "source": "schedule",
            "mode": "post",
            "started_at": (finished - timedelta(seconds=30)).isoformat(),
            "finished_at": finished.isoformat(),
        }

    def test_watchdog_recognizes_internal_error(self):
        now = datetime.now(timezone.utc)
        health.validate_execution(self._execution(1), now, 75)  # no raise

    def test_internal_error_with_success_exit_is_contradiction(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            health.validate_execution(self._execution(0), now, 75)


if __name__ == "__main__":
    unittest.main()
