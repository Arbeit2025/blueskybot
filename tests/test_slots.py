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

# The original nine slots, fixed by the user on 2026-10-29. The
# 15-minute expansion (later on 2026-10-29) must not move or reword
# any of them.
FIXED_TIMES = {
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
FIXED_TEXTS = {
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

GREETINGS = {"morning": "おはよう！🔆", "day": "こんにちは！☀️", "night": "こんばんは。🌙"}


def _period(hour):
    if 5 <= hour < 10:
        return "morning"
    if 10 <= hour < 19:
        return "day"
    return "night"


def _expected_new_text(kind, hour):
    period = _period(hour)
    if kind == "recruit":
        punct = "。" if period == "night" else "！"
        return f"{GREETINGS[period]}\nサイトに載せるゲーム案募集中{punct}\n{URL}"
    return f"{GREETINGS[period]}\nサイトにてミニゲーム開発中！\n{URL}"


def _new_slots():
    return [s for s in CONFIG["slots"] if s["id"].startswith("extra_")]


class ConfigTests(unittest.TestCase):
    def test_every_quarter_hour_has_exactly_one_slot(self):
        times = [s["time_jst"] for s in CONFIG["slots"]]
        expected = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        self.assertEqual(sorted(times), expected)
        self.assertEqual(len(CONFIG["slots"]), 96)

    def test_fixed_nine_slots_keep_their_times(self):
        got = {s["id"]: s["time_jst"] for s in CONFIG["slots"] if s["id"] in FIXED_TIMES}
        self.assertEqual(got, FIXED_TIMES)

    def test_fixed_texts_match_user_specified_wording(self):
        # Golden test: the original nine texts are exactly what the
        # user specified on 2026-10-29 (no placeholders, no prefix).
        now = datetime(2026, 10, 29, 8, 0)
        got = {s["id"]: render_slot(s, now) for s in CONFIG["slots"] if s["id"] in FIXED_TEXTS}
        self.assertEqual(got, FIXED_TEXTS)

    def test_new_slots_alternate_recruit_and_promo(self):
        # In the full recruit/promo sequence (fixed slots included,
        # greetings skipped), every new slot takes the opposite kind
        # from the previous recruit/promo slot. The only same-kind
        # adjacencies left are the ones a fixed slot itself forces
        # (e.g. 09:45 promo -> 10:00 fixed promo).
        new = sorted(_new_slots(), key=lambda s: s["time_jst"])
        self.assertEqual(len(new), 87)
        self.assertEqual(new[0]["id"], "extra_0000_recruit")
        kinds = ["recruit" if s["id"].endswith("_recruit") else "promo" for s in new]
        self.assertEqual(kinds.count("recruit"), 43)
        self.assertEqual(kinds.count("promo"), 44)
        sequence = []
        for slot in sorted(CONFIG["slots"], key=lambda s: s["time_jst"]):
            if slot["id"].startswith("greeting_"):
                continue
            if slot["id"] in FIXED_TIMES:
                kind = "promo" if slot["id"].startswith("promo_") else "recruit"
            else:
                kind = "recruit" if slot["id"].endswith("_recruit") else "promo"
            sequence.append((slot, kind))
        for (prev_slot, prev_kind), (slot, kind) in zip(sequence, sequence[1:]):
            if kind == prev_kind:
                self.assertIn(slot["id"], FIXED_TIMES,
                              f"{prev_slot['id']} -> {slot['id']}")
            if slot["id"].startswith("extra_"):
                self.assertNotEqual(kind, prev_kind, slot["id"])

    def test_new_slot_ids_encode_their_time_and_kind(self):
        for slot in _new_slots():
            hour, minute = map(int, slot["time_jst"].split(":"))
            kind = "recruit" if slot["id"].endswith("_recruit") else "promo"
            self.assertEqual(slot["id"], f"extra_{hour:02d}{minute:02d}_{kind}")

    def test_new_texts_reuse_recruit_and_promo_lines(self):
        # New texts are built only from the user's existing recruit /
        # promo sentences: the greeting line follows the time of day
        # (as in the fixed slots) and recruit punctuation follows the
        # fixed pattern (！ in the morning/day, 。 at night).
        now = datetime(2026, 10, 29, 8, 0)
        for slot in _new_slots():
            hour = int(slot["time_jst"].split(":")[0])
            kind = "recruit" if slot["id"].endswith("_recruit") else "promo"
            self.assertEqual(render_slot(slot, now), _expected_new_text(kind, hour),
                             slot["id"])
            self.assertIn(URL, slot["template"], slot["id"])

    def test_all_slots_render_valid(self):
        now = datetime(2026, 10, 29, 8, 0)  # a Thursday
        for slot in CONFIG["slots"]:
            text = render_slot(slot, now)
            self.assertEqual(validate_post(text), [], text)

    def test_greetings_match_their_kind(self):
        now = datetime(2026, 10, 29, 8, 0)
        self.assertIn("おはよう", render_slot(find_slot(CONFIG, "greeting_morning"), now))
        self.assertIn("こんにちは", render_slot(find_slot(CONFIG, "greeting_noon"), now))
        self.assertIn("こんばんは", render_slot(find_slot(CONFIG, "greeting_evening"), now))

    def test_promo_and_recruit_carry_minigame_url(self):
        now = datetime(2026, 10, 29, 8, 0)
        for slot in CONFIG["slots"]:
            if slot["id"].startswith("greeting_"):
                continue
            self.assertIn(URL, render_slot(slot, now), slot["id"])

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
