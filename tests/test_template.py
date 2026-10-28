import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.bsky_client import build_post_record, format_created_at
from bot.state import already_posted_on, text_hash
from bot.template import (
    build_link_facets,
    contains_url,
    render_template,
    text_length,
    validate_post,
)


class RenderTests(unittest.TestCase):
    def test_date_placeholders(self):
        # 2026-10-29 is a Thursday.
        now = datetime(2026, 10, 29, 9, 0)
        out = render_template("{date} {date_ja}（{weekday_ja}）", now)
        self.assertEqual(out, "2026-10-29 2026年10月29日（木）")

    def test_same_template_differs_by_date(self):
        tpl = "{date} fixed message"
        a = render_template(tpl, datetime(2026, 10, 29))
        b = render_template(tpl, datetime(2026, 10, 30))
        self.assertNotEqual(a, b)


class LengthTests(unittest.TestCase):
    def test_limit_is_300(self):
        self.assertEqual(validate_post("あ" * 300), [])
        self.assertTrue(validate_post("あ" * 301))

    def test_validate_accepts_normal(self):
        self.assertEqual(validate_post("おはようございます"), [])

    def test_validate_rejects_empty(self):
        self.assertTrue(validate_post("   "))

    def test_text_length_counts_codepoints(self):
        self.assertEqual(text_length("あいう"), 3)


class FacetTests(unittest.TestCase):
    def test_no_url_no_facets(self):
        self.assertEqual(build_link_facets("リンクなし"), [])
        self.assertFalse(contains_url("リンクなし"))

    def test_ascii_url_byte_offsets(self):
        text = "see https://example.com/x end"
        facets = build_link_facets(text)
        self.assertEqual(len(facets), 1)
        idx = facets[0]["index"]
        self.assertEqual((idx["byteStart"], idx["byteEnd"]), (4, 25))
        sliced = text.encode("utf-8")[idx["byteStart"]:idx["byteEnd"]].decode("utf-8")
        self.assertEqual(sliced, "https://example.com/x")
        self.assertEqual(facets[0]["features"][0]["uri"], "https://example.com/x")

    def test_japanese_before_url_byte_offsets(self):
        # Byte offsets must be UTF-8 based: 3 Japanese chars = 9 bytes.
        text = "あいう https://example.com"
        facets = build_link_facets(text)
        idx = facets[0]["index"]
        self.assertEqual(idx["byteStart"], 10)  # 9 bytes + 1 space
        sliced = text.encode("utf-8")[idx["byteStart"]:idx["byteEnd"]].decode("utf-8")
        self.assertEqual(sliced, "https://example.com")
        self.assertTrue(contains_url(text))


class RecordTests(unittest.TestCase):
    def test_created_at_format(self):
        now = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_created_at(now), "2026-10-29T00:00:00+00:00".replace("+00:00", "Z"))

    def test_record_shape(self):
        record = build_post_record("hello", now=datetime(2026, 10, 29, tzinfo=timezone.utc))
        self.assertEqual(record["$type"], "app.bsky.feed.post")
        self.assertEqual(record["text"], "hello")
        self.assertEqual(record["langs"], ["ja"])
        self.assertNotIn("facets", record)

    def test_record_includes_facets_when_given(self):
        facets = build_link_facets("https://example.com")
        record = build_post_record("https://example.com", facets=facets)
        self.assertEqual(record["facets"], facets)


class StateTests(unittest.TestCase):
    def test_already_posted(self):
        state = {"last_post_date": "2026-10-29", "last_text_hash": text_hash("x")}
        self.assertTrue(already_posted_on(state, "2026-10-29"))
        self.assertFalse(already_posted_on(state, "2026-10-30"))
        self.assertFalse(already_posted_on({}, "2026-10-29"))


if __name__ == "__main__":
    unittest.main()
