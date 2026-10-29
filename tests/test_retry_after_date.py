"""Cycle M8: Retry-After in HTTP-date form must be honored too.

Cycle M2 handled the delta-seconds form only. RFC 9110 allows the
HTTP-date form as well, and intermediaries in front of a PDS may use
it; ignoring it burned all retries inside the rate-limit window.
"""
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from bot.bsky_client import _parse_retry_after, _retry_delay, BskyError

NOW = datetime(2026, 10, 29, 7, 0, 0, tzinfo=timezone.utc)


def _headers(value):
    return {"Retry-After": value}


class RetryAfterDateTests(unittest.TestCase):
    def test_future_date_gives_delta(self):
        value = format_datetime(NOW + timedelta(seconds=12), usegmt=True)
        self.assertAlmostEqual(_parse_retry_after(_headers(value), now=NOW), 12.0, places=0)

    def test_far_future_date_is_capped(self):
        value = format_datetime(NOW + timedelta(hours=2), usegmt=True)
        self.assertEqual(_parse_retry_after(_headers(value), now=NOW), 30.0)

    def test_past_date_means_retry_now(self):
        value = format_datetime(NOW - timedelta(seconds=5), usegmt=True)
        self.assertEqual(_parse_retry_after(_headers(value), now=NOW), 0.0)

    def test_seconds_form_still_works(self):
        self.assertEqual(_parse_retry_after(_headers("7"), now=NOW), 7.0)
        self.assertEqual(_parse_retry_after(_headers("3600"), now=NOW), 30.0)

    def test_garbage_returns_none(self):
        for value in ("soon", "", "12x", "32 Jan 2026"):
            self.assertIsNone(_parse_retry_after(_headers(value), now=NOW), value)
        self.assertIsNone(_parse_retry_after({}, now=NOW))
        self.assertIsNone(_parse_retry_after(None, now=NOW))

    def test_date_hint_flows_into_delay(self):
        error = BskyError("rate limited", status=429,
                          retry_after=_parse_retry_after(
                              _headers(format_datetime(NOW + timedelta(seconds=9), usegmt=True)),
                              now=NOW))
        self.assertEqual(_retry_delay(0, error), 9.0)


if __name__ == "__main__":
    unittest.main()
