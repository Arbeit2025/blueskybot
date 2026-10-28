import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.post_daily import find_slot, load_config, render_slot
from bot.state import (
    already_posted_slot,
    load_state,
    record_post_slot,
    slot_key,
)
from bot.template import validate_post

ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(ROOT / "config" / "post.json")

EXPECTED_TIMES = {
    "greeting_morning": "08:00",
    "greeting_noon": "12:00",
    "greeting_evening": "19:00",
    "promo_morning": "10:00",
    "promo_afternoon": "16:00",
    "recruit_morning": "09:00",
    "recruit_evening": "20:00",
    "recruit_night": "01:00",
}


class ConfigTests(unittest.TestCase):
    def test_eight_slots_with_expected_times(self):
        got = {s["id"]: s["time_jst"] for s in CONFIG["slots"]}
        self.assertEqual(got, EXPECTED_TIMES)

    def test_all_slots_render_valid_and_unique(self):
        now = datetime(2026, 10, 29, 8, 0)  # a Thursday
        texts = [render_slot(s, now) for s in CONFIG["slots"]]
        for text in texts:
            self.assertEqual(validate_post(text), [], text)
        self.assertEqual(len(set(texts)), 8, "each slot must differ")

    def test_greetings_match_their_kind(self):
        now = datetime(2026, 10, 29, 8, 0)
        self.assertIn("おはよう", render_slot(find_slot(CONFIG, "greeting_morning"), now))
        self.assertIn("こんにちは", render_slot(find_slot(CONFIG, "greeting_noon"), now))
        self.assertIn("こんばんは", render_slot(find_slot(CONFIG, "greeting_evening"), now))

    def test_promo_and_recruit_carry_minigame_url(self):
        now = datetime(2026, 10, 29, 8, 0)
        for slot_id in ("promo_morning", "promo_afternoon",
                        "recruit_morning", "recruit_evening", "recruit_night"):
            text = render_slot(find_slot(CONFIG, slot_id), now)
            self.assertIn("https://minigame-b1qp.onrender.com", text, slot_id)

    def test_find_slot_unknown(self):
        self.assertIsNone(find_slot(CONFIG, "no_such_slot"))


class SlotStateTests(unittest.TestCase):
    def test_slot_key(self):
        self.assertEqual(slot_key("2026-10-29", "promo_morning"),
                         "2026-10-29:promo_morning")

    def test_per_slot_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = load_state(path)
            self.assertFalse(already_posted_slot(state, "2026-10-29", "promo_morning"))
            state = record_post_slot(path, state, "2026-10-29", "promo_morning", "text")
            self.assertTrue(already_posted_slot(state, "2026-10-29", "promo_morning"))
            # Another slot on the same day is not blocked.
            self.assertFalse(already_posted_slot(state, "2026-10-29", "promo_afternoon"))
            # The same slot on another day is not blocked.
            self.assertFalse(already_posted_slot(state, "2026-10-30", "promo_morning"))
            # And it survives a reload from disk.
            reloaded = load_state(path)
            self.assertTrue(already_posted_slot(reloaded, "2026-10-29", "promo_morning"))


if __name__ == "__main__":
    unittest.main()
