import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot import bsky_client, check_access, health, post_daily, runner

ROOT = post_daily.ROOT
CONFIG = json.loads((ROOT / "config/post.json").read_text())
OPERATIONS = json.loads((ROOT / "config/operations.json").read_text())
TZ = ZoneInfo("Asia/Tokyo")


def control_event():
    return {"action": "edited", "repository": {"full_name": runner.REPOSITORY,
            "owner": {"login": "Arbeit2025"}}, "sender": {"login": "Arbeit2025"},
            "issue": {"number": 2, "state": "open", "user": {"login": "Arbeit2025"},
                      "body": "untrusted body; never used as a command"},
            "changes": {"body": {"from": "old body"}}}


class EventGateTests(unittest.TestCase):
    def test_owner_control_body_edit_can_trigger(self):
        self.assertTrue(runner.trusted_external_event(control_event()))

    def test_other_issue_cannot_trigger(self):
        event = control_event()
        event["issue"]["number"] = 999
        self.assertFalse(runner.trusted_external_event(event))

    def test_other_actor_cannot_trigger(self):
        event = control_event()
        event["sender"]["login"] = "someone-else"
        self.assertFalse(runner.trusted_external_event(event))

    def test_wrong_repository_cannot_trigger(self):
        event = control_event()
        event["repository"]["full_name"] = "other/blueskybot"
        self.assertFalse(runner.trusted_external_event(event))

    def test_closed_issue_cannot_trigger(self):
        event = control_event()
        event["issue"]["state"] = "closed"
        self.assertFalse(runner.trusted_external_event(event))

    def test_title_only_edit_cannot_trigger(self):
        event = control_event()
        event["changes"] = {"title": {"from": "old"}}
        self.assertFalse(runner.trusted_external_event(event))

    def test_opened_issue_cannot_trigger(self):
        event = control_event()
        event["action"] = "opened"
        self.assertFalse(runner.trusted_external_event(event))

    def test_issue_text_does_not_select_commands_or_slots(self):
        event = control_event()
        event["issue"]["body"] = "--slot greeting_night --config /etc/passwd $(bad)"
        self.assertEqual(runner.select_arguments({"EVENT_NAME": "issues"}, event),
                         ["--due", "--post"])

    def test_manual_defaults_remain_preview(self):
        self.assertEqual(runner.select_arguments({"EVENT_NAME": "workflow_dispatch"}, {}), [])
        self.assertEqual(runner.select_arguments({"EVENT_NAME": "workflow_dispatch",
                         "INPUT_SLOT": "due", "INPUT_DRY_RUN": "true"}, {}), ["--due"])

    def test_manual_all_never_posts(self):
        self.assertEqual(runner.select_arguments({"EVENT_NAME": "workflow_dispatch",
                         "INPUT_SLOT": "all", "INPUT_DRY_RUN": "false"}, {}), [])

    def test_invalid_manual_input_is_rejected(self):
        for settings in ({"INPUT_SLOT": "$(bad)"}, {"INPUT_DRY_RUN": "maybe"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                runner.select_arguments({"EVENT_NAME": "workflow_dispatch", **settings}, {})


class RunnerResultTests(unittest.TestCase):
    def run_gate(self, env, event=None):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "result.json"
            event_path = Path(tmp) / "event.json"
            event_path.write_text(json.dumps(event or {}))
            environment = {"EVENT_NAME": "schedule", "BOT_REPORT_PATH": str(report_path),
                           "GITHUB_EVENT_PATH": str(event_path), **env}
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(post_daily, "main") as cli, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = runner.main()
            return code, json.loads(report_path.read_text()) if report_path.exists() else None, cli, output.getvalue()

    def test_missing_switch_is_failure_not_silent_dry_run(self):
        code, report, cli, _ = self.run_gate({})
        self.assertEqual(code, 1)
        self.assertEqual(report["counts"], {"configuration_error": 1})
        cli.assert_not_called()

    def test_explicit_stop_has_distinct_status(self):
        code, report, cli, _ = self.run_gate({"POST_ENABLED": "false"})
        self.assertEqual(code, 0)
        self.assertEqual(report["counts"], {"disabled": 1})
        cli.assert_not_called()

    def test_missing_credentials_fail_before_running_cli(self):
        code, report, cli, _ = self.run_gate({"POST_ENABLED": "true"})
        self.assertEqual(code, 1)
        self.assertEqual(report["counts"], {"missing_credentials": 1})
        cli.assert_not_called()

    def test_unauthorized_external_event_never_reaches_credentials(self):
        event = control_event()
        event["sender"]["login"] = "stranger"
        code, report, cli, _ = self.run_gate({"EVENT_NAME": "issues"}, event)
        self.assertEqual(code, 0)
        self.assertEqual(report["counts"], {"ignored_event": 1})
        cli.assert_not_called()


class StructuredOutcomeTests(unittest.TestCase):
    def test_read_only_access_check_never_creates_a_post_or_prints_tokens(self):
        with mock.patch.dict(os.environ, {"BLUESKY_HANDLE": "fake", "BLUESKY_APP_PASSWORD": "password-sentinel"}, clear=True), \
                mock.patch.object(check_access, "create_session", return_value={"did": "did:plc:fake", "accessJwt": "token-sentinel"}), \
                mock.patch.object(check_access, "load_expected_did", return_value="did:plc:fake"), \
                mock.patch.object(check_access, "_list_recent_records", return_value=[]), \
                mock.patch.object(bsky_client, "create_post") as send, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(check_access.main(), 0)
        send.assert_not_called()
        self.assertIn("access_verified", output.getvalue())
        self.assertNotIn("token-sentinel", output.getvalue())
        self.assertNotIn("password-sentinel", output.getvalue())

    def test_posts_and_repeated_execution_have_distinct_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, report = Path(tmp) / "state.json", Path(tmp) / "report.json"
            env = {"POST_ENABLED": "true", "BLUESKY_HANDLE": "fake.bsky.social",
                   "BLUESKY_APP_PASSWORD": "SECRET_SENTINEL"}
            args = ["--slot", "greeting_morning", "--post", "--state", str(state), "--report", str(report)]
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as output, \
                    mock.patch.object(bsky_client, "create_post", return_value={
                        "uri": "at://did:plc:fake/app.bsky.feed.post/abc", "cid": "cid1"}) as send:
                self.assertEqual(post_daily.main(args), 0)
                first = json.loads(report.read_text())
                self.assertEqual(post_daily.main(args), 0)
                second = json.loads(report.read_text())
            self.assertEqual(send.call_count, 1)
            self.assertEqual(first["counts"], {"posted": 1})
            self.assertEqual(second["counts"], {"already_posted": 1})
            self.assertNotIn("SECRET_SENTINEL", report.read_text() + output.getvalue())

    def test_authentication_error_is_not_generic_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcomes = []
            with mock.patch.dict(os.environ, {"POST_ENABLED": "true", "BLUESKY_HANDLE": "fake",
                                             "BLUESKY_APP_PASSWORD": "fake"}), \
                    mock.patch.object(bsky_client, "create_post", side_effect=bsky_client.BskyError("unauthorized", status=401)), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = post_daily.run_slot(CONFIG["slots"][0], CONFIG, datetime(2026, 10, 30, 1, tzinfo=TZ),
                                           Path(tmp) / "state.json", post=True, outcomes=outcomes)
            self.assertEqual(code, 1)
            self.assertEqual(outcomes[0]["status"], "authentication_error")


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 8, 40, tzinfo=TZ)
        self.ops = {**OPERATIONS, "monitor_since_utc": "2026-10-29T22:59:00Z"}
        self.ops["expected_did"]="did:plc:fake"

    def run_record(self, event="schedule", age=5, conclusion="success", path=".github/workflows/daily-post.yml"):
        return {"id": 100, "event": event, "path": path, "conclusion": conclusion, "status":"completed",
                "created_at": (self.now - timedelta(minutes=age)).isoformat()}

    def feed(self, slot_id="greeting_morning", when=None):
        slot = post_daily.find_slot(CONFIG, slot_id)
        when = when or self.now.replace(hour=8, minute=3)
        return {"feed": [{"post": {"uri": "at://did:plc:fake/app.bsky.feed.post/abc",
                         "author":{"did":"did:plc:fake"},
                         "record": {"$type":"app.bsky.feed.post","text": post_daily.render_slot(slot, when), "createdAt": when.isoformat()}}}]}

    def execution(self,status,code):
        return {"source":"issues","mode":"event_gate" if status=="disabled" else "post",
                "exit_code":code,"counts":{status:1},"outcomes":[{"status":status}],
                "started_at":(self.now-timedelta(minutes=1)).isoformat(),"finished_at":self.now.isoformat()}

    def assess(self, runs=None, feed=None, execution=None):
        return health.assess({"workflow_runs": runs or []}, feed if feed is not None else self.feed(),
                             CONFIG, self.ops, self.now, execution)

    def test_normal_native_run_and_real_post_are_healthy(self):
        self.assertEqual(self.assess([self.run_record()])["status"], "healthy")

    def test_ci_success_does_not_count_as_bot_heartbeat(self):
        result = self.assess([self.run_record(path=".github/workflows/ci.yml")])
        self.assertIn("native_scheduler_stale", result["problems"])

    def test_external_fallback_can_work_while_native_scheduler_is_stale(self):
        result = self.assess([self.run_record(event="issues")])
        self.assertEqual(result["status"], "degraded")
        self.assertNotIn("all_automatic_triggers_stale", result["problems"])

    def test_successful_workflow_without_public_post_is_unhealthy(self):
        result = self.assess([self.run_record()], {"feed": []})
        self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(result["missing_slots"], ["2026-10-30:greeting_morning"])

    def test_posts_before_monitor_activation_are_not_reported_missing(self):
        self.ops["monitor_since_utc"] = self.now.isoformat()
        result = self.assess([self.run_record()], {"feed": []})
        self.assertEqual(result["missing_slots"], [])

    def test_disabled_is_not_successfully_posted(self):
        result = self.assess(execution=self.execution("disabled",0))
        self.assertEqual(result["status"], "disabled")

    def test_execution_failure_is_reported_even_before_post_deadline(self):
        result = self.assess([self.run_record()], execution=self.execution("authentication_error",1))
        self.assertIn("execution_failed", result["problems"])
        self.assertEqual(result["status"], "unhealthy")

    def test_api_error_does_not_become_empty_successful_feed(self):
        with self.assertRaises(ValueError):
            self.assess([self.run_record()], {"error": "RateLimitExceeded"})

    def test_incomplete_feed_requires_pagination(self):
        feed = self.feed()
        feed["cursor"] = "more"
        with self.assertRaises(ValueError):
            self.assess([self.run_record()], feed)

    def test_repost_does_not_fulfill_expected_original_post(self):
        feed = self.feed()
        feed["feed"][0]["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
        self.assertEqual(self.assess([self.run_record()], feed)["status"], "unhealthy")

    def test_no_trigger_for_over_ninety_minutes_is_unhealthy(self):
        self.ops["monitor_since_utc"] = (self.now - timedelta(hours=3)).isoformat()
        result = self.assess([self.run_record(event="issues", age=100)])
        self.assertIn("all_automatic_triggers_stale", result["problems"])

    def test_failed_latest_automatic_run_is_unhealthy(self):
        result = self.assess([self.run_record(conclusion="failure")])
        self.assertIn("latest_automatic_run_failed", result["problems"])

    def test_monitor_checks_previous_day_after_midnight(self):
        self.now = datetime(2026, 10, 30, 0, 10, tzinfo=TZ)
        self.ops["monitor_since_utc"] = "2026-10-29T12:50:00Z"
        when = datetime(2026, 10, 29, 22, 3, tzinfo=TZ)
        result = self.assess([self.run_record()], self.feed("greeting_night", when))
        self.assertEqual(result["expected_slots"], ["2026-10-29:greeting_night"])
        self.assertEqual(result["missing_slots"], [])


if __name__ == "__main__":
    unittest.main()
