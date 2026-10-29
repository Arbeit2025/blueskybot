"""Cycle M5 (Muse): freshness of the post timestamp and of the
local state file.

Two slow-burn bugs:
- create_post built the record ONCE before the retry loop, so its
  createdAt was the first attempt's time. With retries (now even
  longer when a Retry-After hint is honoured) the stored timestamp
  can drift minutes into the past and land outside the slot window
  that the duplicate check and the health monitor match on.
- record_post_slot kept every day's key forever. The guard only ever
  reads today's key, so on a long-lived local install the file grew
  without bound.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import bsky_client
from bot.bsky_client import BskyError, create_post
from bot.state import record_post_slot

GOOD_URI = "at://did:plc:test/app.bsky.feed.post/abc123"


class CreatedAtFreshnessTests(unittest.TestCase):
    def test_record_is_rebuilt_for_each_attempt(self):
        sent_records = []
        outcomes = [BskyError("HTTP 503", status=503, transient=True),
                    {"uri": GOOD_URI, "cid": "cid1"}]

        def transport(method, url, payload=None, token=None):
            if "createSession" in url:
                return {"did": "did:plc:test", "accessJwt": "jwt",
                        "handle": "test.bsky.social"}
            if "createRecord" in url:
                sent_records.append(payload["record"])
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            raise AssertionError(f"unexpected URL: {url}")

        builds = []
        original = bsky_client.build_post_record

        def counting(text, facets=None, now=None):
            record = original(text, facets, now)
            builds.append(1)
            record["createdAt"] = f"marker-{len(builds)}"
            return record

        with mock.patch.object(bsky_client, "_request_json", transport), \
                mock.patch.object(bsky_client, "build_post_record", counting):
            result = create_post("hello", "test.bsky.social", "app-pass",
                                 sleep=lambda s: None)
        self.assertEqual(result["uri"], GOOD_URI)
        self.assertEqual(len(builds), 2)
        self.assertEqual([r["createdAt"] for r in sent_records],
                         ["marker-1", "marker-2"])


class StatePruningTests(unittest.TestCase):
    def test_old_entries_are_pruned_recent_and_unknown_kept(self):
        state = {"posted": {
            "2026-10-21:eight_days_old": "h1",
            "2026-10-22:seven_days_old": "h2",
            "2026-10-28:yesterday": "h3",
            "unparsable-key": "h4",
        }}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            payload = record_post_slot(path, state, "2026-10-29",
                                       "new_slot", "text")
            on_disk = json.loads(path.read_text(encoding="utf-8"))
        expected = {"2026-10-22:seven_days_old",
                    "2026-10-28:yesterday",
                    "unparsable-key",
                    "2026-10-29:new_slot"}
        self.assertEqual(set(payload["posted"]), expected)
        self.assertEqual(on_disk, payload)

    def test_unparsable_current_date_keeps_everything(self):
        state = {"posted": {"2020-01-01:ancient": "h"}}
        with tempfile.TemporaryDirectory() as tmp:
            payload = record_post_slot(Path(tmp) / "s.json", state,
                                       "not-a-date", "slot", "text")
        self.assertIn("2020-01-01:ancient", payload["posted"])


if __name__ == "__main__":
    unittest.main()
