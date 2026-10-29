import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from bot import bsky_client, post_daily, runner
from bot.template import render_template

VALID = {"timezone":"Asia/Tokyo", "slots":[{"id":"morning", "time_jst":"08:00", "template":"hello {date}"}]}


class ConfigurationTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"config.json"
            path.write_text(json.dumps(value))
            return post_daily.load_config(path)

    def test_valid_config_roundtrips_unchanged(self):
        self.assertEqual(self.load(VALID),VALID)

    def test_invalid_top_level_and_slots_are_rejected(self):
        for data in (None,[],1,"text",{}, {"slots":[]}, {"slots":{}}, {"slots":[None]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)

    def test_invalid_times_are_rejected(self):
        for clock in (None,123,"99:99","24:00","08:60","8:00","08:0","08:00:00"):
            data=copy.deepcopy(VALID);data["slots"][0]["time_jst"]=clock
            with self.subTest(clock=clock), self.assertRaises(ValueError):self.load(data)

    def test_duplicate_ids_are_rejected_before_selection(self):
        data=copy.deepcopy(VALID);data["slots"].append({"id":"morning","time_jst":"09:00","template":"other"})
        with self.assertRaisesRegex(ValueError,"duplicate slot id"):self.load(data)

    def test_duplicate_times_are_rejected(self):
        data=copy.deepcopy(VALID);data["slots"].append({"id":"other","time_jst":"08:00","template":"other"})
        with self.assertRaisesRegex(ValueError,"duplicate slot time"):self.load(data)

    def test_invalid_ids_are_rejected(self):
        for slot_id in (None,123,"","a"*65,"$(bad)","line\nbreak","日本語"):
            data=copy.deepcopy(VALID);data["slots"][0]["id"]=slot_id
            with self.subTest(slot_id=slot_id),self.assertRaises(ValueError):self.load(data)

    def test_timezone_errors_are_clear(self):
        for zone in (None,[],"","Not/A_Zone"):
            data=copy.deepcopy(VALID);data["timezone"]=zone
            with self.subTest(zone=zone),self.assertRaises(ValueError):self.load(data)

    def test_bad_template_fields_and_large_format_are_rejected(self):
        for template in (None,123,"","   ","{typo}","{date.__class__}","{date[0]}","{date:1000000000}","{date!r}","{date"):
            data=copy.deepcopy(VALID);data["slots"][0]["template"]=template
            with self.subTest(template=template),self.assertRaises(ValueError):self.load(data)

    def test_escaped_literal_braces_and_known_fields_render(self):
        text=render_template("{{literal}} {date} {weekday_ja} {slot_time}",datetime(2026,10,29),{"slot_time":"08:00"})
        self.assertEqual(text,"{literal} 2026-10-29 木 08:00")

    def test_length_is_checked_after_expansion(self):
        data=copy.deepcopy(VALID);data["slots"][0]["template"]="x"*295+"{date}"
        with self.assertRaises(ValueError):self.load(data)

    def test_invalid_config_is_reported_without_api_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,report=Path(tmp)/"config.json",Path(tmp)/"report.json"
            path.write_text('{"slots":[]}')
            with mock.patch.object(bsky_client,"create_post") as send,contextlib.redirect_stdout(io.StringIO()):
                code=post_daily.main(["--config",str(path),"--due","--post","--report",str(report)])
            self.assertEqual(code,1)
            self.assertEqual(json.loads(report.read_text())["counts"],{"configuration_error":1})
            send.assert_not_called()

    def test_malformed_external_payloads_are_rejected_without_crashing(self):
        for payload in (None,[],"text",{"repository":None},{"repository":[]},
                        {"issue":None},{"sender":None},{"changes":None},
                        {"repository":{"owner":None}},{"issue":{"user":None}}):
            with self.subTest(payload=payload):self.assertFalse(runner.trusted_external_event(payload))
