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
        # With a slot every 15 minutes, the 2-hour catch-up window is
        # never empty; the boundaries are what matter: a slot is not
        # due before its time, and a slot exactly 2 hours old is due.
        self.assertEqual(self.ids(7, 59, 59), [
            "extra_0600_recruit", "extra_0615_promo", "extra_0630_recruit",
            "extra_0645_promo", "extra_0700_recruit", "extra_0715_promo",
            "extra_0730_recruit", "extra_0745_promo"])
        self.assertEqual(self.ids(8), [
            "extra_0600_recruit", "extra_0615_promo", "extra_0630_recruit",
            "extra_0645_promo", "extra_0700_recruit", "extra_0715_promo",
            "extra_0730_recruit", "extra_0745_promo", "greeting_morning"])
        self.assertEqual(self.ids(8, 7), [
            "extra_0615_promo", "extra_0630_recruit", "extra_0645_promo",
            "extra_0700_recruit", "extra_0715_promo", "extra_0730_recruit",
            "extra_0745_promo", "greeting_morning"])
        self.assertEqual(self.ids(10, 0, 1), [
            "extra_0815_recruit", "extra_0830_promo", "extra_0845_recruit",
            "recruit_morning", "extra_0915_promo", "extra_0930_recruit",
            "extra_0945_promo", "promo_morning"])
        self.assertEqual(self.ids(14, 0, 1), [
            "extra_1215_promo", "extra_1230_recruit", "extra_1245_promo",
            "extra_1300_recruit", "extra_1315_promo", "extra_1330_recruit",
            "extra_1345_promo", "extra_1400_recruit"])

    def test_missed_ticks_are_caught_up_in_order(self):
        self.assertEqual(self.ids(9, 47), [
            "greeting_morning", "extra_0815_recruit", "extra_0830_promo",
            "extra_0845_recruit", "recruit_morning", "extra_0915_promo",
            "extra_0930_recruit", "extra_0945_promo"])
        self.assertEqual(self.ids(10), [
            "greeting_morning", "extra_0815_recruit", "extra_0830_promo",
            "extra_0845_recruit", "recruit_morning", "extra_0915_promo",
            "extra_0930_recruit", "extra_0945_promo", "promo_morning"])

    def test_midnight_keeps_the_original_date(self):
        now = datetime(2026, 10, 30, 0, tzinfo=TZ)
        due = post_daily.due_slots(CONFIG, now)
        self.assertEqual(len(due), 9)  # 22:00-23:45 of Oct 29 + 00:00 of Oct 30
        by_id = {s["id"]: when for s, when in due}
        self.assertEqual(by_id["greeting_night"].date().isoformat(), "2026-10-29")
        self.assertEqual(by_id["extra_0000_recruit"].date().isoformat(), "2026-10-30")
        # One minute later the 22:00 slot has aged out of the window,
        # while the rest of the previous evening is still due.
        later = [s["id"] for s, _ in post_daily.due_slots(CONFIG, now.replace(minute=1))]
        self.assertNotIn("greeting_night", later)
        self.assertEqual(len(later), 8)

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

    def run_cli(self, *args, now=None, config=None):
        now = now or datetime(2026, 10, 29, 9, 7, tzinfo=TZ)
        argv = [*args, "--state", str(self.state)]
        if config is not None:
            argv += ["--config", str(config)]
        with mock.patch.object(post_daily, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            with contextlib.redirect_stdout(io.StringIO()):
                return post_daily.main(argv)

    def test_due_dry_run_never_sends_or_saves(self):
        with mock.patch.object(bsky_client, "create_post") as send:
            self.assertEqual(self.run_cli("--due"), 0)
        send.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_no_due_slot_never_contacts_api(self):
        # With a slot every 15 minutes the real config always has a
        # due slot, so use a sparse one-slot config for this case.
        sparse = Path(self.state.parent) / "sparse.json"
        sparse.write_text(json.dumps({
            "timezone": "Asia/Tokyo",
            "slots": [{"id": "only_morning", "time_jst": "08:00",
                       "template": "おはよう！🔆\n今日も1日頑張ろう！"}],
        }), encoding="utf-8")
        with mock.patch.object(bsky_client, "create_post") as send:
            self.assertEqual(self.run_cli("--due", "--post", config=sparse, now=datetime(
                2026, 10, 29, 7, 59, tzinfo=TZ)), 0)
        send.assert_not_called()

    def test_due_posts_once_then_skips_local_state(self):
        with mock.patch.object(bsky_client, "create_post", return_value={
            "uri": "at://did:plc:test/app.bsky.feed.post/posted", "cid": "cid1",
        }) as send:
            self.assertEqual(self.run_cli("--due", "--post"), 0)
            self.assertEqual(self.run_cli("--due", "--post"), 0)
        # At 09:07 the due window (07:07-09:07) holds eight slots:
        # 07:15, 07:30, 07:45, 08:00, 08:15, 08:30, 08:45, 09:00.
        self.assertEqual(send.call_count, 8)
        self.assertEqual(len(json.loads(self.state.read_text())["posted"]), 8)

    def test_failed_slot_does_not_block_another_slot(self):
        outcomes = [bsky_client.BskyError("temporary outage", transient=True)] + [
            {"uri": f"at://did:plc:test/app.bsky.feed.post/next{i}", "cid": "cid2"}
            for i in range(7)]
        with mock.patch.object(bsky_client, "create_post", side_effect=outcomes) as send:
            self.assertEqual(self.run_cli("--due", "--post"), 1)
        self.assertEqual(send.call_count, 8)
        posted = json.loads(self.state.read_text())["posted"]
        self.assertEqual(len(posted), 7)
        self.assertNotIn("2026-10-29:extra_0715_promo", posted)

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
        # Nine slots are due at midnight: the eight evening slots of
        # Oct 29 (22:00-23:45) plus the 00:00 slot of Oct 30, oldest
        # first, each keeping its own scheduled date.
        self.assertEqual(send.call_count, 9)
        self.assertEqual(send.call_args_list[0].kwargs["slot_date"].isoformat(), "2026-10-29")
        self.assertEqual(send.call_args_list[-1].kwargs["slot_date"].isoformat(), "2026-10-30")
        posted = json.loads(self.state.read_text())["posted"]
        self.assertIn("2026-10-29:greeting_night", posted)
        self.assertIn("2026-10-30:extra_0000_recruit", posted)

    def test_post_without_selection_is_refused(self):
        with mock.patch.object(bsky_client, "create_post") as send:
            self.assertEqual(self.run_cli("--post"), 1)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
