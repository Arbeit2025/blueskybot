"""Random-time slots: deterministic per day, due like normal slots."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import post_daily

TZ = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent.parent
CONFIG = post_daily.load_config(ROOT / "config" / "post.json")

BASE_CONFIG = {
    "timezone": "Asia/Tokyo",
    "slots": [{"id": "fixed_one", "time_jst": "12:00", "template": "fixed"}],
}


def with_random(**overrides):
    random_slot = {
        "id": "follower_recruit",
        "window_start_jst": "09:00",
        "window_end_jst": "21:00",
        "template": "こちらblueskybot、フォロワー募集中！📱",
    }
    random_slot.update(overrides)
    return {**BASE_CONFIG, "random_slots": [random_slot]}


def load(config):
    with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False)
        path = Path(f.name)
    try:
        return post_daily.load_config(path)
    finally:
        path.unlink()


class ResolvedRandomSlotTests(unittest.TestCase):
    def test_same_day_same_time(self):
        rs = CONFIG["random_slots"][0]
        day = date(2026, 11, 3)
        self.assertEqual(
            post_daily.resolved_random_slot(rs, day),
            post_daily.resolved_random_slot(rs, day),
        )

    def test_time_is_inside_window_on_the_grid(self):
        rs = CONFIG["random_slots"][0]
        for offset in range(60):
            day = date(2026, 1, 1) + timedelta(days=offset)
            slot = post_daily.resolved_random_slot(rs, day)
            hour, minute = map(int, slot["time_jst"].split(":"))
            self.assertTrue(9 * 60 <= hour * 60 + minute < 21 * 60)
            self.assertEqual(minute % 15, 0)

    def test_time_varies_across_days(self):
        rs = CONFIG["random_slots"][0]
        times = {
            post_daily.resolved_random_slot(rs, date(2026, 1, 1) + timedelta(days=i))["time_jst"]
            for i in range(30)
        }
        self.assertGreater(len(times), 1)

    def test_production_template_is_exact(self):
        rs = CONFIG["random_slots"][0]
        self.assertEqual(rs["id"], "follower_recruit")
        self.assertEqual(rs["template"], "こちらblueskybot、フォロワー募集中！📱")


class RandomConfigValidationTests(unittest.TestCase):
    def test_valid_random_config_loads(self):
        self.assertEqual(load(with_random())["random_slots"][0]["id"],
                         "follower_recruit")

    def test_duplicate_id_with_fixed_slot_rejected(self):
        with self.assertRaises(ValueError):
            load(with_random(id="fixed_one"))

    def test_window_not_multiple_of_15_rejected(self):
        with self.assertRaises(ValueError):
            load(with_random(window_end_jst="21:10"))

    def test_window_end_before_start_rejected(self):
        with self.assertRaises(ValueError):
            load(with_random(window_start_jst="21:00", window_end_jst="09:00"))

    def test_oversized_template_rejected(self):
        with self.assertRaises(ValueError):
            load(with_random(template="あ" * 400))


class RandomDueSlotsTests(unittest.TestCase):
    def test_random_slot_becomes_due_at_its_time(self):
        config = load(with_random())
        day = date(2026, 11, 3)
        slot = post_daily.resolved_random_slots(config, day)[0]
        hour, minute = map(int, slot["time_jst"].split(":"))
        at_time = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
        due_ids = [s["id"] for s, _ in post_daily.due_slots(config, at_time)]
        self.assertIn("follower_recruit", due_ids)
        before = at_time - timedelta(minutes=1)
        due_before = [s["id"] for s, _ in post_daily.due_slots(config, before)]
        self.assertNotIn("follower_recruit", due_before)


if __name__ == "__main__":
    unittest.main()
