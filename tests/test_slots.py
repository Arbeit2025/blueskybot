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
    "greeting_night": "22:00",
    "promo_morning": "10:00",
    "promo_afternoon": "16:00",
    "recruit_morning": "09:00",
    "recruit_evening": "20:00",
    "recruit_night": "01:00",
}

URL = "https://minigame-b1qp.onrender.com/"
EXPECTED_TEXTS = {
    "greeting_morning": "おはよう！🔆\n今日も1日頑張ろう！",
    "recruit_morning": "おはよう！🔆\nサイトに載せるゲーム案募集中！\n" + URL,
    "promo_morning": "こんにちは！☀️\nサイトにてミニゲーム開発中！\n" + URL,
    "greeting_noon": "こんにちは！☀️\n今日も折り返し！頑張って！",
    "promo_afternoon": "こんにちは！☀️\nサイトにてミニゲーム開発中！\n" + URL,
    "greeting_evening": "こんばんは。🌙\n1日おつかれさま。\nゆっくり休んでね。",
    "recruit_evening": "こんばんは。🌙\nサイトに載せるゲーム案募集中。\n" + URL,
    "greeting_night": "おやすみ、また明日。💤",
    "recruit_night": "こんばんは。🌙\nサイトに載せるゲーム案募集中。\n" + URL,
}


class ConfigTests(unittest.TestCase):
    def test_nine_slots_with_expected_times(self):
        got = {s["id"]: s["time_jst"] for s in CONFIG["slots"]}
        self.assertEqual(got, EXPECTED_TIMES)

    def test_all_slots_render_valid(self):
        now = datetime(2026, 10, 29, 8, 0)  # a Thursday
        for slot in CONFIG["slots"]:
            text = render_slot(slot, now)
            self.assertEqual(validate_post(text), [], text)

    def test_texts_match_user_specified_wording(self):
        # Golden test: the wording is exactly what the user specified
        # on 2026-10-29 (no date placeholders, no auto-post prefix).
        now = datetime(2026, 10, 29, 8, 0)
        got = {s["id"]: render_slot(s, now) for s in CONFIG["slots"]}
        self.assertEqual(got, EXPECTED_TEXTS)

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
            self.assertIn(URL, text, slot_id)

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
