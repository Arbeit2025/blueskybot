"""Catch-up selection and CLI safety, without any external requests."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot import bsky_client, post_daily

TZ = ZoneInfo("Asia/Tokyo")
CONFIG = post_daily.load_config(post_daily.ROOT / "config" / "post.json")


class DueSlotsTests(unittest.TestCase):
    def ids(self, hour, minute=0, second=0):
        now = datetime(2026, 10, 29, hour, minute, second, tzinfo=TZ)
        return [slot["id"] for slot, _ in post_daily.due_slots(CONFIG, now)]

    def test_not_early_and_not_expired(self):
        self.assertEqual(self.ids(7, 59, 59), [])
        self.assertEqual(self.ids(8), ["greeting_morning"])
        self.assertEqual(self.ids(8, 7), ["greeting_morning"])
        self.assertEqual(self.ids(10, 0, 1), ["recruit_morning", "promo_morning"])
        self.assertEqual(self.ids(14, 0, 1), [])

    def test_missed_ticks_are_caught_up_in_order(self):
        self.assertEqual(self.ids(9, 47), ["greeting_morning", "recruit_morning"])
        self.assertEqual(self.ids(10), ["greeting_morning", "recruit_morning", "promo_morning"])

    def test_midnight_keeps_the_original_date(self):
        now = datetime(2026, 10, 30, 0, tzinfo=TZ)
        due = post_daily.due_slots(CONFIG, now)
        self.assertEqual([s["id"] for s, _ in due], ["greeting_night"])
        self.assertEqual(due[0][1].date().isoformat(), "2026-10-29")
        self.assertEqual(post_daily.due_slots(CONFIG, now.replace(minute=1)), [])

    def test_every_slot_is_due_seven_minutes_after_its_time(self):
        for slot in CONFIG["slots"]:
            hour, minute = map(int, slot["time_jst"].split(":"))
            with self.subTest(slot=slot["id"]):
                self.assertIn(slot["id"], self.ids(hour, minute + 7))


class DueCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state.json"
        self.env = mock.patch.dict(os.environ, {
            "POST_ENABLED": "true", "BLUESKY_HANDLE": "test.bsky.social",
            "BLUESKY_APP_PASSWORD": "fake-app-password",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, *args, now=None):
        now = now or datetime(2026, 10, 29, 9, 7, tzinfo=TZ)
        with mock.patch.object(post_daily, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            with contextlib.redirect_stdout(io.StringIO()):
                return post_daily.main([*args, "--state", str(self.state)])

    def test_due_dry_run_never_sends_or_saves(self):
        with mock.patch.object(bsky_client, "create_post") as send:
            self.assertEqual(self.run_cli("--due"), 0)
        send.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_no_due_slot_never_contacts_api(self):
        with mock.patch.object(bsky_client, "create_post") as send:
            self.assertEqual(self.run_cli("--due", "--post", now=datetime(
                2026, 10, 29, 7, 59, tzinfo=TZ)), 0)
        send.assert_not_called()

    def test_due_posts_once_then_skips_local_state(self):
        with mock.patch.object(bsky_client, "create_post", return_value={
            "uri": "at://did:plc:test/app.bsky.feed.post/posted", "cid": "cid1",
        }) as send:
            self.assertEqual(self.run_cli("--due", "--post"), 0)
            self.assertEqual(self.run_cli("--due", "--post"), 0)
        self.assertEqual(send.call_count, 2)  # 08:00 and 09:00, one each
        self.assertEqual(len(json.loads(self.state.read_text())["posted"]), 2)

    def test_failed_slot_does_not_block_another_slot(self):
        with mock.patch.object(bsky_client, "create_post", side_effect=[
            bsky_client.BskyError("temporary outage", transient=True),
            {"uri": "at://did:plc:test/app.bsky.feed.post/next", "cid": "cid2"},
        ]) as send:
            self.assertEqual(self.run_cli("--due", "--post"), 1)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(list(json.loads(self.state.read_text())["posted"]),
                         ["2026-10-29:recruit_morning"])

    def test_kill_switch_blocks_due_posts(self):
        with mock.patch.dict(os.environ, {"POST_ENABLED": "false"}):
            with mock.patch.object(bsky_client, "create_post") as send:
                self.assertEqual(self.run_cli("--due", "--post"), 1)
        send.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_missing_credentials_block_due_posts(self):
        with mock.patch.dict(os.environ, {"BLUESKY_APP_PASSWORD": ""}):
            with mock.patch.object(bsky_client, "create_post") as send:
                self.assertEqual(self.run_cli("--due", "--post"), 1)
        send.assert_not_called()

    def test_midnight_uses_previous_day_for_api_and_state(self):
        with mock.patch.object(bsky_client, "create_post", return_value={
            "uri": "at://did:plc:test/app.bsky.feed.post/night", "cid": "cid1",
        }) as send:
            self.assertEqual(self.run_cli("--due", "--post", now=datetime(
                2026, 10, 30, 0, tzinfo=TZ)), 0)
        self.assertEqual(send.call_args.kwargs["slot_date"].isoformat(), "2026-10-29")
        self.assertIn("2026-10-29:greeting_night",
                      json.loads(self.state.read_text())["posted"])

    def test_post_without_selection_is_refused(self):
        with mock.patch.object(bsky_client, "create_post") as send:
            self.assertEqual(self.run_cli("--post"), 1)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
