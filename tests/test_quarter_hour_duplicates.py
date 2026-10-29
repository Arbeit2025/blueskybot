"""15-minute slots: identical texts repeat 30 minutes apart, so the
legacy time-window duplicate check must not claim a neighbouring
slot's post, and the health monitor must not fulfil a slot with its
neighbour's post either.

Regression target: find_posted_uri matched on text + a window of
-30 min to +120 min. The promo text at 10:00 and the same promo text
at 10:30 (an extra slot) sit inside each other's windows, so the
second slot was reported already posted without ever being sent (and
health counted the first post as proof for both slots). Slot record
keys solve this: a record whose key decodes to another slot's
scheduled time is not this slot's post, and this slot's own key is an
exact match even when the post landed outside the window.
"""
import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import bsky_client, health, post_daily  # noqa: E402
from bot.bsky_client import find_posted_uri, slot_record_key  # noqa: E402

TZ = ZoneInfo("Asia/Tokyo")
DAY = date(2026, 10, 30)
CONFIG = post_daily.load_config(post_daily.ROOT / "config" / "post.json")
PROMO_DAY = "こんにちは！☀️\nサイトにてミニゲーム開発中！\nhttps://minigame-b1qp.onrender.com/"


def record_for(slot_id, slot_time, text, created_at):
    rkey = slot_record_key(DAY, slot_time, TZ, slot_id)
    return {"uri": f"at://did:plc:test/app.bsky.feed.post/{rkey}",
            "value": {"$type": "app.bsky.feed.post", "text": text,
                      "createdAt": created_at}}


class NeighbourSlotTests(unittest.TestCase):
    def test_other_slots_post_inside_window_does_not_count(self):
        # The 10:00 promo post (created 10:03) is inside the window of
        # the 10:30 extra promo slot (10:00 to 12:30) and the text is
        # identical, yet it belongs to a different slot key.
        records = [record_for("promo_morning", time(10, 0), PROMO_DAY,
                              "2026-10-30T01:03:00.000Z")]  # 10:03 JST
        own = slot_record_key(DAY, time(10, 30), TZ, "extra_1030_promo")
        self.assertIsNone(find_posted_uri(
            records, PROMO_DAY, slot_date=DAY, slot_time=time(10, 30),
            tz=TZ, own_rkey=own))

    def test_own_key_counts_even_outside_the_window(self):
        # A post recovered long after its slot (created 13:00 for a
        # 10:30 slot, past the +120 min window end) is still this
        # slot's post when the key matches.
        records = [record_for("extra_1030_promo", time(10, 30), PROMO_DAY,
                              "2026-10-30T04:00:00.000Z")]  # 13:00 JST
        own = slot_record_key(DAY, time(10, 30), TZ, "extra_1030_promo")
        self.assertIsNotNone(find_posted_uri(
            records, PROMO_DAY, slot_date=DAY, slot_time=time(10, 30),
            tz=TZ, own_rkey=own))

    def test_own_key_with_different_text_does_not_count(self):
        records = [record_for("extra_1030_promo", time(10, 30), "別の文面",
                              "2026-10-30T01:33:00.000Z")]
        own = slot_record_key(DAY, time(10, 30), TZ, "extra_1030_promo")
        self.assertIsNone(find_posted_uri(
            records, PROMO_DAY, slot_date=DAY, slot_time=time(10, 30),
            tz=TZ, own_rkey=own))

    def test_legacy_record_without_slot_key_still_uses_window(self):
        legacy = {"uri": "at://did:plc:test/app.bsky.feed.post/oldrandom",
                  "value": {"$type": "app.bsky.feed.post", "text": PROMO_DAY,
                            "createdAt": "2026-10-30T01:05:00.000Z"}}
        own = slot_record_key(DAY, time(10, 0), TZ, "promo_morning")
        self.assertIsNotNone(find_posted_uri(
            [legacy], PROMO_DAY, slot_date=DAY, slot_time=time(10, 0),
            tz=TZ, own_rkey=own))

    def test_without_own_rkey_the_window_rule_is_unchanged(self):
        records = [record_for("promo_morning", time(10, 0), PROMO_DAY,
                              "2026-10-30T01:03:00.000Z")]
        self.assertIsNotNone(find_posted_uri(
            records, PROMO_DAY, slot_date=DAY, slot_time=time(10, 30), tz=TZ))


class HealthNeighbourTests(unittest.TestCase):
    def test_health_does_not_fulfil_slot_with_neighbours_post(self):
        ops = {"repository": "Arbeit2025/blueskybot",
               "handle": "arbeit2025.bsky.social",
               "expected_did": "did:plc:test",
               "monitor_since_utc": "2026-10-30T00:30:00Z",  # 09:30 JST
               "native_heartbeat_minutes": 30,
               "external_heartbeat_minutes": 90,
               "post_grace_minutes": 30}
        now = datetime(2026, 10, 30, 11, 20, tzinfo=TZ)
        runs = {"workflow_runs": [{
            "id": 1, "path": ".github/workflows/daily-post.yml",
            "event": "schedule", "status": "completed",
            "conclusion": "success", "created_at": now.isoformat(),
            "run_started_at": now.isoformat()}]}
        # Only the 10:00 promo post exists. The 10:30 slot shares its
        # text and is inside the legacy window, so without key-based
        # matching health would wrongly count it as posted too.
        neighbour = record_for("promo_morning", time(10, 0), PROMO_DAY,
                               "2026-10-30T01:03:00.000Z")
        feed_item = {"post": {"uri": neighbour["uri"],
                              "author": {"did": "did:plc:test"},
                              "record": neighbour["value"]}}
        result = health.assess(runs, {"feed": [feed_item]}, CONFIG, ops, now)
        self.assertIn("2026-10-30:promo_morning", result["expected_slots"])
        self.assertNotIn("2026-10-30:promo_morning", result["missing_slots"])
        # extra_1030_promo shares promo_morning's text and sits 30
        # minutes later, inside the legacy window: it must still be
        # reported missing, because the 10:00 post is not its post.
        self.assertIn("2026-10-30:extra_1030_promo", result["missing_slots"])


if __name__ == "__main__":
    unittest.main()
