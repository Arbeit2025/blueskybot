"""Tests for the hardened posting path in bot.bsky_client.

The transport (_request_json) is replaced with a scripted fake, so no
network is touched. The fake routes by URL substring:
createSession / listRecords / createRecord.
"""
import sys
import unittest
import urllib.parse
from datetime import date, time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import bsky_client  # noqa: E402
from bot.bsky_client import BskyError, create_post, find_posted_uri  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

TEXT = "こんにちは！☀️\nサイトにてミニゲーム開発中！\nhttps://example.com/"
GOOD_URI = "at://did:plc:test/app.bsky.feed.post/abc123"
SLOT_DATE = date(2026, 10, 29)
TZ = ZoneInfo("Asia/Tokyo")


def make_record(text, created_at, uri="at://did:plc:test/app.bsky.feed.post/old1"):
    return {"uri": uri, "cid": "cidold",
            "value": {"$type": "app.bsky.feed.post", "text": text,
                      "createdAt": created_at}}


class FakeTransport:
    """Scripted replacement for bsky_client._request_json."""

    def __init__(self):
        self.records = []            # what listRecords returns
        self.create_outcomes = []    # queue of dicts or exceptions
        self.session_outcomes = []   # queue of dicts or exceptions
        self.calls = {"session": 0, "list": 0, "create": 0}

    def __call__(self, method, url, payload=None, token=None):
        if "createSession" in url:
            self.calls["session"] += 1
            if self.session_outcomes:
                outcome = self.session_outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return {"did": "did:plc:test", "accessJwt": "jwt",
                    "handle": "test.bsky.social"}
        if "listRecords" in url:
            self.calls["list"] += 1
            return {"records": list(self.records)}
        if "createRecord" in url:
            self.calls["create"] += 1
            outcome = self.create_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        raise AssertionError(f"unexpected URL: {url}")


def no_sleep(_seconds):
    pass


class CreatePostTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTransport()
        patcher = mock.patch.object(bsky_client, "_request_json", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, slot_time=time(10, 0)):
        return create_post(TEXT, "test.bsky.social", "app-pass",
                           slot_date=SLOT_DATE, slot_time=slot_time,
                           tz_name="Asia/Tokyo", sleep=no_sleep)

    def test_normal_post(self):
        self.fake.create_outcomes = [{"uri": GOOD_URI, "cid": "cid1"}]
        result = self.post()
        self.assertEqual(result["uri"], GOOD_URI)
        self.assertNotIn("already_posted", result)
        self.assertEqual(self.fake.calls["create"], 1)

    def test_duplicate_in_window_is_skipped(self):
        # Same text today at 10:05 JST (= 01:05 UTC), slot is 10:00.
        self.fake.records = [make_record(TEXT, "2026-10-29T01:05:00.000Z")]
        result = self.post()
        self.assertTrue(result["already_posted"])
        self.assertEqual(self.fake.calls["create"], 0)

    def test_same_text_other_slot_still_posts(self):
        # Identical promo text posted at 10:05 JST must not block the
        # 16:00 slot on the same day.
        self.fake.records = [make_record(TEXT, "2026-10-29T01:05:00.000Z")]
        self.fake.create_outcomes = [{"uri": GOOD_URI, "cid": "cid1"}]
        result = self.post(slot_time=time(16, 0))
        self.assertEqual(result["uri"], GOOD_URI)
        self.assertEqual(self.fake.calls["create"], 1)

    def test_same_text_yesterday_still_posts(self):
        self.fake.records = [make_record(TEXT, "2026-10-28T01:05:00.000Z")]
        self.fake.create_outcomes = [{"uri": GOOD_URI, "cid": "cid1"}]
        result = self.post()
        self.assertEqual(result["uri"], GOOD_URI)
        self.assertEqual(self.fake.calls["create"], 1)

    def test_malformed_success_raises(self):
        self.fake.create_outcomes = [{"unexpected": "shape"}]
        with self.assertRaises(BskyError):
            self.post()
        self.assertEqual(self.fake.calls["create"], 1)

    def test_malformed_success_but_post_landed(self):
        # The response is garbage, but the record exists on the server
        # (created 09:09 JST... within the 10:00 slot window start).
        self.fake.records = [make_record(TEXT, "2026-10-29T00:35:00.000Z",
                                         uri="at://did:plc:test/app.bsky.feed.post/landed")]
        self.fake.create_outcomes = [{"unexpected": "shape"}]
        result = self.post()
        self.assertTrue(result["already_posted"])
        self.assertEqual(result["uri"],
                         "at://did:plc:test/app.bsky.feed.post/landed")

    def test_transient_error_then_success(self):
        self.fake.create_outcomes = [
            BskyError("HTTP 503", status=503, transient=True),
            {"uri": GOOD_URI, "cid": "cid1"},
        ]
        result = self.post()
        self.assertEqual(result["uri"], GOOD_URI)
        self.assertEqual(self.fake.calls["create"], 2)

    def test_transient_error_but_post_landed(self):
        # First createRecord attempt times out after the server stored
        # the post; the re-check before the retry must find it and not
        # post again.
        def create_then_land(method, url, payload=None, token=None):
            if "createRecord" in url:
                self.fake.calls["create"] += 1
                self.fake.records = [make_record(
                    TEXT, "2026-10-29T01:00:30.000Z",
                    uri="at://did:plc:test/app.bsky.feed.post/landed")]
                raise BskyError("network error", transient=True)
            return FakeTransport.__call__(self.fake, method, url,
                                          payload, token)
        with mock.patch.object(bsky_client, "_request_json", create_then_land):
            result = self.post()
        self.assertTrue(result["already_posted"])
        self.assertEqual(self.fake.calls["create"], 1)

    def test_client_error_not_retried(self):
        self.fake.create_outcomes = [
            BskyError("HTTP 400", status=400, transient=False),
        ]
        with self.assertRaises(BskyError):
            self.post()
        self.assertEqual(self.fake.calls["create"], 1)

    def test_session_transient_retry(self):
        self.fake.session_outcomes = [
            BskyError("HTTP 503", status=503, transient=True),
        ]
        self.fake.create_outcomes = [{"uri": GOOD_URI, "cid": "cid1"}]
        result = self.post()
        self.assertEqual(result["uri"], GOOD_URI)
        self.assertEqual(self.fake.calls["session"], 2)

    def test_recent_records_are_requested_newest_first(self):
        seen = []
        def capture(method, url, payload=None, token=None):
            if "listRecords" in url:
                seen.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
            return self.fake(method, url, payload, token)
        self.fake.create_outcomes = [{"uri": GOOD_URI, "cid": "cid1"}]
        with mock.patch.object(bsky_client, "_request_json", capture):
            self.post()
        self.assertEqual(seen[0]["reverse"], ["true"])
        self.assertEqual(seen[0]["limit"], ["100"])

    def test_failed_duplicate_check_never_posts_blindly(self):
        def unavailable(method, url, payload=None, token=None):
            if "listRecords" in url:
                raise BskyError("HTTP 503", status=503, transient=True)
            return self.fake(method, url, payload, token)
        with mock.patch.object(bsky_client, "_request_json", unavailable):
            with self.assertRaises(BskyError):
                self.post()
        self.assertEqual(self.fake.calls["create"], 0)

    def test_malformed_duplicate_response_never_posts(self):
        def malformed(method, url, payload=None, token=None):
            if "listRecords" in url:
                return {"records": None}
            return self.fake(method, url, payload, token)
        with mock.patch.object(bsky_client, "_request_json", malformed):
            with self.assertRaises(BskyError):
                self.post()
        self.assertEqual(self.fake.calls["create"], 0)

    def test_failed_recheck_never_repeats_an_uncertain_post(self):
        def unavailable_after_create(method, url, payload=None, token=None):
            if "listRecords" in url and self.fake.calls["create"]:
                raise BskyError("duplicate lookup unavailable")
            return self.fake(method, url, payload, token)
        self.fake.create_outcomes = [BskyError("response lost", transient=True)]
        with mock.patch.object(bsky_client, "_request_json", unavailable_after_create):
            with self.assertRaises(BskyError):
                self.post()
        self.assertEqual(self.fake.calls["create"], 1)


class FindPostedUriTests(unittest.TestCase):
    def find(self, records, slot_time):
        return find_posted_uri(records, TEXT, slot_date=SLOT_DATE,
                               slot_time=slot_time, tz=TZ)

    def test_window_edges(self):
        # 09:30 JST (= 00:30 UTC) is the window start for a 10:00 slot.
        rec = [make_record(TEXT, "2026-10-29T00:30:00.000Z")]
        self.assertIsNotNone(self.find(rec, time(10, 0)))
        # 09:29 JST is just outside.
        rec = [make_record(TEXT, "2026-10-29T00:29:00.000Z")]
        self.assertIsNone(self.find(rec, time(10, 0)))
        # 12:00 JST (= 03:00 UTC) is the window end.
        rec = [make_record(TEXT, "2026-10-29T03:00:00.000Z")]
        self.assertIsNotNone(self.find(rec, time(10, 0)))

    def test_other_text_ignored(self):
        rec = [make_record("別の文面", "2026-10-29T01:05:00.000Z")]
        self.assertIsNone(self.find(rec, time(10, 0)))

    def test_late_post_after_midnight_matches_previous_days_slot(self):
        rec = [make_record(TEXT, "2026-10-29T15:00:00Z")]
        self.assertIsNotNone(self.find(rec, time(22, 0)))


if __name__ == "__main__":
    unittest.main()
