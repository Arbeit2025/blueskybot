"""Cycle M7: the state-file guard must compare the text hash, not just
the slot key.

Regression target: already_posted_slot only checked key presence, so
if a template was edited after that slot had already posted once, a
re-run silently skipped the corrected text ("already posted") and the
operator never learned the server still held the old wording.
"""
import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot import bsky_client, post_daily, state

TZ = ZoneInfo("Asia/Tokyo")
ENV = {"POST_ENABLED": "true", "BLUESKY_HANDLE": "fake", "BLUESKY_APP_PASSWORD": "fake"}


class AlreadyPostedHashTests(unittest.TestCase):
    def setUp(self):
        self.state = {"posted": {"2026-10-29:promo_morning": state.text_hash("old text")}}

    def test_same_text_matches(self):
        self.assertTrue(state.already_posted_slot(
            self.state, "2026-10-29", "promo_morning", "old text"))

    def test_changed_text_does_not_match(self):
        self.assertFalse(state.already_posted_slot(
            self.state, "2026-10-29", "promo_morning", "corrected text"))

    def test_legacy_key_only_check_unchanged(self):
        self.assertTrue(state.already_posted_slot(
            self.state, "2026-10-29", "promo_morning"))
        self.assertFalse(state.already_posted_slot(
            self.state, "2026-10-29", "promo_afternoon", "old text"))


class RunSlotHashTests(unittest.TestCase):
    def _slot(self):
        config = post_daily.load_config(post_daily.ROOT / "config/post.json")
        return config, post_daily.find_slot(config, "greeting_morning")

    def _run(self, stored_text):
        config, slot = self._slot()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.record_post_slot(path, {}, "2026-10-30", slot["id"], stored_text)
            outcomes = []
            with mock.patch.dict(os.environ, ENV), \
                    mock.patch.object(bsky_client, "create_post",
                                      return_value={"uri": "at://did:plc:x/app.bsky.feed.post/y"}) as send, \
                    contextlib.redirect_stdout(io.StringIO()):
                code = post_daily.run_slot(
                    slot, config, datetime(2026, 10, 30, 8, 0, tzinfo=TZ),
                    path, post=True, outcomes=outcomes)
            return code, outcomes, send

    def test_changed_template_is_not_silently_skipped(self):
        code, outcomes, send = self._run("yesterday's wording")
        send.assert_called_once()  # falls through to the server-side check
        self.assertEqual(code, 0)
        self.assertEqual(outcomes[0]["status"], "posted")

    def test_same_text_still_skips_via_state(self):
        config, slot = self._slot()
        text = post_daily.render_slot(slot, datetime(2026, 10, 30, 8, 0, tzinfo=TZ))
        code, outcomes, send = self._run(text)
        send.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(outcomes[0]["status"], "already_posted")


if __name__ == "__main__":
    unittest.main()
