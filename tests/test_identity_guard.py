"""Cycle M4 (Muse): the posting path verifies where credentials go
and who they log in as.

Before this fix the health check verified the account (expected_did
in config/operations.json), but the posting path itself never did:
a wrong account's app password in Secrets would have posted as that
account every day. Likewise BLUESKY_PDS_URL was trusted blindly, so
a typo'd or malicious value could send the app password over plain
http to an arbitrary host.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import bsky_client
from bot.bsky_client import BskyError, create_post, pds_url, validate_pds_url
from bot.post_daily import load_expected_did


class PdsUrlTests(unittest.TestCase):
    def test_https_accepted_and_slash_stripped(self):
        self.assertEqual(validate_pds_url("https://bsky.social/"),
                         "https://bsky.social")
        self.assertEqual(validate_pds_url("https://pds.example.com"),
                         "https://pds.example.com")

    def test_plain_http_remote_rejected(self):
        with self.assertRaises(BskyError):
            validate_pds_url("http://pds.example.com")

    def test_loopback_http_allowed_for_test_servers(self):
        self.assertEqual(validate_pds_url("http://127.0.0.1:8765"),
                         "http://127.0.0.1:8765")
        self.assertEqual(validate_pds_url("http://localhost:8765"),
                         "http://localhost:8765")

    def test_embedded_credentials_query_and_schemeless_rejected(self):
        for bad in ("https://user:pass@bsky.social",
                    "https://bsky.social/?x=1",
                    "bsky.social",
                    "ftp://bsky.social",
                    ""):
            with self.assertRaises(BskyError, msg=bad):
                validate_pds_url(bad)

    def test_env_override_is_validated(self):
        with mock.patch.dict(os.environ,
                             {"BLUESKY_PDS_URL": "http://evil.example"}):
            with self.assertRaises(BskyError):
                pds_url()
        with mock.patch.dict(os.environ,
                             {"BLUESKY_PDS_URL": "https://pds.example.com/"}):
            self.assertEqual(pds_url(), "https://pds.example.com")


class FakeTransport:
    def __init__(self, session_did):
        self.session_did = session_did
        self.calls = {"session": 0, "create": 0}

    def __call__(self, method, url, payload=None, token=None):
        if "createSession" in url:
            self.calls["session"] += 1
            return {"did": self.session_did, "accessJwt": "jwt",
                    "handle": "fake.bsky.social"}
        if "createRecord" in url:
            self.calls["create"] += 1
            return {"uri": f"at://{self.session_did}/app.bsky.feed.post/abc",
                    "cid": "cid1"}
        raise AssertionError(f"unexpected URL: {url}")


class IdentityGuardTests(unittest.TestCase):
    def run_post(self, session_did, expected_did):
        fake = FakeTransport(session_did)
        with mock.patch.object(bsky_client, "_request_json", fake):
            result = create_post("hello", "fake.bsky.social", "app-pass",
                                 expected_did=expected_did,
                                 sleep=lambda s: None)
        return result, fake

    def test_mismatched_account_is_refused_before_posting(self):
        fake = FakeTransport("did:plc:other")
        with mock.patch.object(bsky_client, "_request_json", fake):
            with self.assertRaises(BskyError) as ctx:
                create_post("hello", "fake.bsky.social", "app-pass",
                            expected_did="did:plc:test",
                            sleep=lambda s: None)
        self.assertEqual(ctx.exception.error_code, "IdentityMismatch")
        self.assertEqual(fake.calls["create"], 0)

    def test_matching_account_posts(self):
        result, fake = self.run_post("did:plc:test", "did:plc:test")
        self.assertIn("uri", result)
        self.assertEqual(fake.calls["create"], 1)

    def test_no_expectation_keeps_old_behaviour(self):
        result, _ = self.run_post("did:plc:anything", None)
        self.assertIn("uri", result)

    def test_malformed_expected_did_rejected(self):
        with self.assertRaises(BskyError):
            self.run_post("did:plc:test", "not-a-did")


class LoadExpectedDidTests(unittest.TestCase):
    def write_ops(self, tmp, content):
        path = Path(tmp) / "operations.json"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        return path

    def test_missing_file_means_no_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_expected_did(Path(tmp) / "operations.json"))

    def test_valid_file_returns_did(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ops(tmp, json.dumps(
                {"expected_did": "did:plc:tgbrskgfguuhmg6jcl4wfzsk"}))
            self.assertEqual(load_expected_did(path),
                             "did:plc:tgbrskgfguuhmg6jcl4wfzsk")

    def test_malformed_file_is_a_configuration_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ops(tmp, "{not json")
            with self.assertRaises(ValueError):
                load_expected_did(path)
            path = self.write_ops(tmp, json.dumps({"expected_did": "nope"}))
            with self.assertRaises(ValueError):
                load_expected_did(path)


if __name__ == "__main__":
    unittest.main()
