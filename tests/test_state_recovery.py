import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot import bsky_client, post_daily, state


class StateRecoveryTests(unittest.TestCase):
    def test_missing_is_the_only_empty_state_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(state.load_state(path), {})
            for content in ("{broken", "[]", "null", '{"posted":[]}', '{"posted":{"x":1}}'):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(state.StateError):
                    state.load_state(path)

    def test_invalid_utf8_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_bytes(b"\xff")
            with self.assertRaises(state.StateError):
                state.load_state(path)

    def test_failed_replace_preserves_old_bytes_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            old = b'{"posted":{"old":"hash"}}'
            path.write_bytes(old)
            with mock.patch.object(state.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(state.StateError):
                    state.atomic_write_state(path, {"posted": {"new": "hash"}})
            self.assertEqual(path.read_bytes(), old)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_failed_fsync_preserves_old_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"posted":{"old":"hash"}}')
            with mock.patch.object(state.os, "fsync", side_effect=OSError("flush failure")):
                with self.assertRaises(state.StateError):
                    state.atomic_write_state(path, {"posted": {}})
            self.assertEqual(json.loads(path.read_text()), {"posted": {"old": "hash"}})

    def test_successful_atomic_write_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "state.json"
            state.prepare_state_path(path)
            self.assertFalse(path.exists())
            state.record_post_slot(path, {}, "2026-10-29", "slot", "こんにちは")
            self.assertTrue(state.already_posted_slot(state.load_state(path), "2026-10-29", "slot"))
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_corrupt_state_stops_before_any_api_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{broken")
            outcomes = []
            slot = post_daily.load_config(post_daily.ROOT / "config/post.json")["slots"][0]
            with mock.patch.object(bsky_client, "create_post") as send, contextlib.redirect_stdout(io.StringIO()):
                code = post_daily.run_slot(slot, {}, datetime(2026,10,29,1,tzinfo=ZoneInfo("Asia/Tokyo")), path,
                                           post=True, outcomes=outcomes)
            self.assertEqual(code, 1)
            self.assertEqual(outcomes[0]["status"], "state_read_error")
            send.assert_not_called()
            self.assertEqual(path.read_text(), "{broken")

    def test_unwritable_parent_stops_before_any_api_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "not-a-directory"
            parent.write_text("keep")
            outcomes = []
            slot = post_daily.load_config(post_daily.ROOT / "config/post.json")["slots"][0]
            env = {"POST_ENABLED":"true", "BLUESKY_HANDLE":"fake", "BLUESKY_APP_PASSWORD":"fake"}
            with mock.patch.dict(os.environ,env), mock.patch.object(bsky_client,"create_post") as send, contextlib.redirect_stdout(io.StringIO()):
                code = post_daily.run_slot(slot, {}, datetime(2026,10,29,1,tzinfo=ZoneInfo("Asia/Tokyo")), parent/"state.json",
                                           post=True,outcomes=outcomes)
            self.assertEqual(code,1)
            self.assertEqual(outcomes[0]["status"],"state_read_error")
            send.assert_not_called()
            self.assertEqual(parent.read_text(),"keep")

    def test_failed_storage_probe_stops_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            outcomes = []
            slot = post_daily.load_config(post_daily.ROOT / "config/post.json")["slots"][0]
            env = {"POST_ENABLED":"true", "BLUESKY_HANDLE":"fake", "BLUESKY_APP_PASSWORD":"fake"}
            with mock.patch.dict(os.environ,env), mock.patch.object(state.tempfile,"TemporaryFile",side_effect=OSError("no space")), \
                    mock.patch.object(bsky_client,"create_post") as send, contextlib.redirect_stdout(io.StringIO()):
                code = post_daily.run_slot(slot, {}, datetime(2026,10,29,1,tzinfo=ZoneInfo("Asia/Tokyo")), path,
                                           post=True,outcomes=outcomes)
            self.assertEqual(code,1)
            self.assertEqual(outcomes[0]["status"],"state_preflight_error")
            send.assert_not_called()
            self.assertFalse(path.exists())
