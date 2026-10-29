"""Cycle M9: the 300 limit is in grapheme clusters, not code points.

len() rejected legitimate emoji-heavy drafts (a family emoji counts
5+ code points but is one cluster). text_length now approximates the
grapheme count; plain text must count exactly as before.
"""
import unittest

from bot.template import text_length, validate_post

FAMILY3 = "👨‍👩‍👧"          # man ZWJ woman ZWJ girl
FAMILY4 = "👨‍👩‍👧‍👦"
RAINBOW_FLAG = "🏳️‍🌈"       # white flag + VS16 + ZWJ + rainbow


class GraphemeLengthTests(unittest.TestCase):
    def test_plain_text_unchanged(self):
        self.assertEqual(text_length(""), 0)
        self.assertEqual(text_length("abc"), 3)
        self.assertEqual(text_length("あいう"), 3)
        self.assertEqual(text_length("おはようございます☀"), 10)

    def test_zwj_sequences_count_once(self):
        self.assertEqual(text_length(FAMILY3), 1)
        self.assertEqual(text_length(FAMILY4), 1)
        self.assertEqual(text_length(RAINBOW_FLAG), 1)
        self.assertEqual(text_length(FAMILY3 + FAMILY3), 2)

    def test_flags_pair_up(self):
        self.assertEqual(text_length("🇯🇵"), 1)
        self.assertEqual(text_length("🇯🇵🇺🇸"), 2)
        self.assertEqual(text_length("🇯🇵" * 3), 3)

    def test_modifiers_and_selectors_do_not_count(self):
        self.assertEqual(text_length("☀️"), 1)        # sun + VS16
        self.assertEqual(text_length("👍🏽"), 1)        # thumbs up + skin tone
        self.assertEqual(text_length("1️⃣"), 1)        # digit + VS16 + keycap
        self.assertEqual(text_length("💤"), 1)

    def test_combining_marks_do_not_count(self):
        self.assertEqual(text_length("が"), 1)  # か + combining dakuten
        self.assertEqual(text_length("é"), 1)        # e + combining acute

    def test_emoji_heavy_post_at_limit_is_accepted(self):
        self.assertEqual(validate_post(FAMILY3 * 300), [])
        self.assertTrue(validate_post(FAMILY3 * 301))

    def test_existing_limit_behavior_for_plain_text(self):
        self.assertEqual(validate_post("あ" * 300), [])
        self.assertTrue(validate_post("あ" * 301))


if __name__ == "__main__":
    unittest.main()
