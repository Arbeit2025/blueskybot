import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot import health, post_daily
from bot.bsky_client import BskyError


class RunSlotOutcomeTests(unittest.TestCase):
    """run_slot outcome handling around posting edge cases.

    - An already-posted skip must survive a local state-write
      failure: the state file is a second-layer defence (the
      server-side check is authoritative), so a redundant write
      failure must not turn a successful skip into a failed run.
      A fresh post whose state cannot be saved stays an error.
    - A slot-key conflict (the template was edited after the slot
      posted, so the key holds a different text) is final for the
      day: the client refuses to overwrite it and no retry can
      change that. It is reported as its own non-error outcome
      instead of failing every catch-up run in the window.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state" / "last_post.json"
        env = mock.patch.dict(os.environ, {
            "POST_ENABLED": "true",
            "BLUESKY_HANDLE": "fake",
            "BLUESKY_APP_PASSWORD": "fake",
        })
        env.start()
        self.addCleanup(env.stop)
        self.slot = {"id": "promo", "time_jst": "10:00", "template": "hello"}
        self.config = {"timezone": "Asia/Tokyo"}
        self.scheduled = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    def run_with(self, create_result=None, create_error=None, break_state=False):
        outcomes = []
        create = mock.patch(
            "bot.bsky_client.create_post",
            return_value=create_result, side_effect=create_error)
        if break_state:
            record = mock.patch.object(post_daily, "record_post_slot",
                                       side_effect=OSError("disk full"))
        else:
            record = mock.patch.object(post_daily, "record_post_slot")
        with create, record:
            code = post_daily.run_slot(self.slot, self.config, self.scheduled,
                                       self.state_path, post=True, outcomes=outcomes)
        return code, outcomes

    def test_already_posted_survives_state_write_failure(self):
        code, outcomes = self.run_with(
            create_result={"uri": "at://did:plc:test/app.bsky.feed.post/x",
                           "already_posted": True},
            break_state=True)
        self.assertEqual(code, 0)
        self.assertEqual(outcomes[0]["status"], "already_posted")

    def test_fresh_post_state_write_failure_is_an_error(self):
        code, outcomes = self.run_with(
            create_result={"uri": "at://did:plc:test/app.bsky.feed.post/x"},
            break_state=True)
        self.assertEqual(code, 1)
        self.assertEqual(outcomes[0]["status"], "state_write_error")

    def test_slot_conflict_is_a_non_error_outcome(self):
        code, outcomes = self.run_with(
            create_error=BskyError("slot key contains a different post; "
                                   "refusing to overwrite",
                                   error_code="SlotConflict"))
        self.assertEqual(code, 0)
        self.assertEqual(outcomes[0]["status"], "slot_conflict")


class HealthExecutionValidationTests(unittest.TestCase):
    """The watchdog must accept execution reports that contain the
    slot_conflict outcome; an unrecognized status would make every
    health check fail on a day a template was edited mid-day."""

    def test_slot_conflict_execution_report_is_valid(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        execution = {
            "exit_code": 0,
            "source": "issues",
            "mode": "post",
            "started_at": "2026-10-29T11:58:00+00:00",
            "finished_at": "2026-10-29T11:59:00+00:00",
            "outcomes": [{"slot_id": "promo", "status": "slot_conflict"}],
            "counts": {"slot_conflict": 1},
        }
        health.validate_execution(execution, now, 90)  # must not raise


if __name__ == "__main__":
    unittest.main()
