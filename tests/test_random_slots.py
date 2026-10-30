"""Random-time slots: deterministic per day, spread over the window."""
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

SEGMENTS = [(9 * 60, 13 * 60), (13 * 60, 17 * 60), (17 * 60, 21 * 60)]


def with_random(**overrides):
    random_slot = {
        "id": "follower_recruit",
        "window_start_jst": "09:00",
        "window_end_jst": "21:00",
        "times_per_day": 3,
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


def minutes(slot):
    hour, minute = map(int, slot["time_jst"].split(":"))
    return hour * 60 + minute


class ResolvedRandomSlotTests(unittest.TestCase):
    def test_same_day_same_times(self):
        day = date(2026, 11, 3)
        self.assertEqual(
            post_daily.resolved_random_slots(CONFIG, day),
            post_daily.resolved_random_slots(CONFIG, day),
        )

    def test_three_posts_spread_over_segments(self):
        for offset in range(60):
            day = date(2026, 1, 1) + timedelta(days=offset)
            slots = post_daily.resolved_random_slots(CONFIG, day)
            self.assertEqual(len(slots), 3)
            self.assertEqual(
                [s["id"] for s in slots],
                ["follower_recruit_1", "follower_recruit_2", "follower_recruit_3"],
            )
            for slot, (start, end) in zip(slots, SEGMENTS):
                self.assertTrue(start <= minutes(slot) < end)
                self.assertEqual(minutes(slot) % 15, 0)
            times = [minutes(s) for s in slots]
            self.assertEqual(times, sorted(set(times)))

    def test_times_vary_across_days(self):
        firsts = {
            post_daily.resolved_random_slots(
                CONFIG, date(2026, 1, 1) + timedelta(days=i))[0]["time_jst"]
            for i in range(30)
        }
        self.assertGreater(len(firsts), 1)

    def test_production_template_is_exact(self):
        for slot in post_daily.resolved_random_slots(CONFIG, date(2026, 11, 3)):
            self.assertEqual(slot["template"], "こちらblueskybot、フォロワー募集中！📱")
        entry = CONFIG["random_slots"][0]
        self.assertEqual(entry["id"], "follower_recruit")
        self.assertEqual(entry["times_per_day"], 3)


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

    def test_times_per_day_zero_rejected(self):
        with self.assertRaises(ValueError):
            load(with_random(times_per_day=0))

    def test_window_not_divisible_into_segments_rejected(self):
        # 09:00-20:00 is 660 minutes; 660 / 3 = 220 is not a multiple of 15.
        with self.assertRaises(ValueError):
            load(with_random(window_end_jst="20:00"))

    def test_oversized_template_rejected(self):
        with self.assertRaises(ValueError):
            load(with_random(template="あ" * 400))


class RandomDueSlotsTests(unittest.TestCase):
    def test_each_random_post_becomes_due_at_its_time(self):
        config = load(with_random())
        day = date(2026, 11, 3)
        slots = post_daily.resolved_random_slots(config, day)
        for slot in slots:
            at_time = datetime(day.year, day.month, day.day,
                               *map(int, slot["time_jst"].split(":")), tzinfo=TZ)
            due_ids = [s["id"] for s, _ in post_daily.due_slots(config, at_time)]
            self.assertIn(slot["id"], due_ids)
        first = slots[0]
        before_first = datetime(day.year, day.month, day.day,
                                 *map(int, first["time_jst"].split(":")), tzinfo=TZ) \
            - timedelta(minutes=1)
        due_before = [s["id"] for s, _ in post_daily.due_slots(config, before_first)]
        for slot in slots:
            self.assertNotIn(slot["id"], due_before)


if __name__ == "__main__":
    unittest.main()
