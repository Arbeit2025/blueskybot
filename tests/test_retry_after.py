"""Cycle M2 (Muse): Retry-After hints are honoured by the retry logic.

Before this fix every transient failure was retried after a fixed
1s/2s backoff, even when the server explicitly said (HTTP 429) to
wait longer, so a rate-limited run burned all 3 attempts inside a
few seconds and lost the slot until the catch-up window.
"""
import email.message
import io
import unittest
import urllib.error
from unittest import mock

from bot import bsky_client
from bot.bsky_client import BskyError, _parse_retry_after, _retry_delay, _with_retry


def http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://pds.example/xrpc/test", code, "error", headers,
        io.BytesIO(b'{"error":"TooManyRequests","message":"slow down"}'),
    )


class RetryAfterParsingTests(unittest.TestCase):
    def request_error(self, code, retry_after=None):
        with mock.patch.object(bsky_client.urllib.request, "urlopen",
                               side_effect=http_error(code, retry_after)):
            with self.assertRaises(BskyError) as ctx:
                bsky_client._request_json("GET", "https://pds.example/xrpc/test")
        return ctx.exception

    def test_429_carries_retry_after(self):
        exc = self.request_error(429, "7")
        self.assertEqual(exc.status, 429)
        self.assertTrue(exc.transient)
        self.assertEqual(exc.retry_after, 7.0)
        self.assertEqual(exc.error_code, "TooManyRequests")

    def test_retry_after_is_capped(self):
        exc = self.request_error(429, "3600")
        self.assertEqual(exc.retry_after, bsky_client.RETRY_AFTER_CAP_SECONDS)

    def test_missing_or_malformed_hint_is_none(self):
        self.assertIsNone(self.request_error(503).retry_after)
        self.assertIsNone(self.request_error(503, "soon").retry_after)
        self.assertIsNone(_parse_retry_after(None))
        headers = email.message.Message()
        headers["Retry-After"] = "-5"
        self.assertIsNone(_parse_retry_after(headers))


class RetryDelayTests(unittest.TestCase):
    def test_plain_backoff_without_hint(self):
        self.assertEqual(_retry_delay(0, None), 1.0)
        self.assertEqual(_retry_delay(1, None), 2.0)

    def test_hint_raises_the_wait(self):
        err = BskyError("x", transient=True, retry_after=5.0)
        self.assertEqual(_retry_delay(0, err), 5.0)
        self.assertEqual(_retry_delay(3, err), 8.0)  # backoff wins when larger

    def test_with_retry_sleeps_at_least_the_hint(self):
        sleeps = []
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) == 1:
                raise BskyError("HTTP 429", status=429, transient=True,
                                retry_after=7.0)
            return "ok"

        result = _with_retry(flaky, sleep=sleeps.append)
        self.assertEqual(result, "ok")
        self.assertEqual(sleeps, [7.0])

    def test_with_retry_still_gives_up_after_max_attempts(self):
        def always():
            raise BskyError("HTTP 429", status=429, transient=True,
                            retry_after=3.0)

        with self.assertRaises(BskyError):
            _with_retry(always, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
