import copy
import unittest
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

from bot import health


class HealthIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,30,14,tzinfo=ZoneInfo("Asia/Tokyo"))
        self.config={"timezone":"Asia/Tokyo","slots":[{"id":"noon","time_jst":"12:00","template":"hello"}]}
        self.ops={"monitor_since_utc":(self.now-timedelta(hours=3)).isoformat(),"expected_did":"did:plc:expected",
                  "native_heartbeat_minutes":30,"external_heartbeat_minutes":90,"post_grace_minutes":30}
        self.run={"id":1,"path":".github/workflows/daily-post.yml","event":"schedule","status":"completed",
                  "created_at":(self.now-timedelta(minutes=1)).isoformat(),"conclusion":"success"}
        self.feed={"feed":[{"post":{"uri":"at://did:plc:expected/app.bsky.feed.post/key","author":{"did":"did:plc:expected"},
                   "record":{"$type":"app.bsky.feed.post","text":"hello","createdAt":self.now.replace(hour=12,minute=3).isoformat()}}}]}

    def assess(self,execution=None):
        return health.assess({"workflow_runs":[self.run]},self.feed,self.config,self.ops,self.now,execution)

    def report(self,status="no_due",code=0):
        return {"source":"issues","mode":"event_gate" if status=="disabled" else "post","exit_code":code,
                "counts":{status:1},"outcomes":[{"status":status}],
                "started_at":(self.now-timedelta(minutes=1)).isoformat(),"finished_at":self.now.isoformat()}

    def test_queued_is_not_a_started_heartbeat(self):
        self.run.update(status="queued",conclusion=None)
        result=self.assess()
        self.assertEqual(result["status"],"unhealthy")
        self.assertIn("all_automatic_triggers_stale",result["problems"])

    def test_waiting_pending_and_skipped_do_not_prove_activity(self):
        for status in ("waiting","pending","requested"):
            self.run["status"]=status
            with self.subTest(status=status):self.assertIn("native_scheduler_stale",self.assess()["problems"])

    def test_actual_start_time_is_used_after_long_queue_wait(self):
        self.run["created_at"]=(self.now-timedelta(minutes=50)).isoformat()
        self.run["run_started_at"]=(self.now-timedelta(minutes=1)).isoformat()
        self.assertEqual(self.assess()["status"],"healthy")

    def test_long_running_job_is_reported_stuck(self):
        self.run.update(status="in_progress",conclusion=None,run_started_at=(self.now-timedelta(minutes=11)).isoformat())
        self.assertIn("automatic_run_stuck",self.assess()["problems"])

    def test_short_running_job_is_not_reported_stuck(self):
        self.run.update(status="in_progress",conclusion=None,run_started_at=(self.now-timedelta(minutes=4)).isoformat())
        self.assertEqual(self.assess()["status"],"healthy")

    def test_foreign_author_cannot_fulfill_a_slot(self):
        self.feed["feed"][0]["post"]["author"]["did"]="did:plc:other"
        with self.assertRaisesRegex(ValueError,"unexpected account"):self.assess()

    def test_uri_owner_and_collection_must_match(self):
        for uri in ("at://did:plc:other/app.bsky.feed.post/key","at://did:plc:expected/app.bsky.feed.like/key"):
            self.feed["feed"][0]["post"]["uri"]=uri
            with self.subTest(uri=uri),self.assertRaises(ValueError):self.assess()

    def test_missing_author_is_not_silently_skipped(self):
        del self.feed["feed"][0]["post"]["author"]
        with self.assertRaises(ValueError):self.assess()

    def test_malformed_feed_items_are_unknown_not_healthy(self):
        for item in (None,"text",{"post":{}},{"reason":"bad","post":{}}):
            self.feed={"feed":[item]}
            with self.subTest(item=item),self.assertRaises(ValueError):self.assess()

    def test_invalid_post_timestamp_is_unknown(self):
        self.feed["feed"][0]["post"]["record"]["createdAt"]="not-a-date"
        with self.assertRaisesRegex(ValueError,"timestamp"):self.assess()

    def test_foreign_repost_is_excluded_while_own_post_still_counts(self):
        repost=copy.deepcopy(self.feed["feed"][0]);repost["post"]["author"]["did"]="did:plc:other"
        repost["reason"]={"$type":"app.bsky.feed.defs#reasonRepost"}
        self.feed["feed"].append(repost)
        self.assertEqual(self.assess()["status"],"healthy")

    def test_pinned_own_post_is_not_mistaken_for_a_repost(self):
        self.feed["feed"][0]["reason"]={"$type":"app.bsky.feed.defs#reasonPin"}
        self.assertEqual(self.assess()["missing_slots"],[])

    def test_report_count_mismatch_is_unknown(self):
        report=self.report();report["counts"]={"posted":1}
        with self.assertRaisesRegex(ValueError,"inconsistent"):self.assess(report)

    def test_disabled_mixed_with_error_does_not_mask_failure(self):
        report=self.report("disabled",1);report["outcomes"].append({"status":"api_error"});report["counts"]["api_error"]=1
        with self.assertRaisesRegex(ValueError,"disabled"):self.assess(report)

    def test_valid_disabled_has_stable_notification_code(self):
        result=self.assess(self.report("disabled"))
        self.assertEqual((result["status"],result["alert_code"]),("disabled","disabled"))

    def test_stale_and_nonposting_reports_are_rejected(self):
        for changes in ({"finished_at":(self.now-timedelta(hours=2)).isoformat(),"started_at":(self.now-timedelta(hours=2,minutes=1)).isoformat()},
                        {"source":"workflow_dispatch","mode":"read_only_access_check"}):
            report={**self.report(),**changes}
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.assess(report)

    def test_future_monitor_start_is_not_a_permanent_grace_period(self):
        self.ops["monitor_since_utc"]=(self.now+timedelta(days=365)).isoformat()
        with self.assertRaisesRegex(ValueError,"future"):self.assess()

    def test_incomplete_and_empty_reports_are_rejected(self):
        for report in ({},{"counts":{"disabled":1},"exit_code":0},self.report(code="0")):
            with self.subTest(report=report),self.assertRaises(ValueError):self.assess(report)

    def test_unknown_or_contradictory_outcomes_are_rejected(self):
        for report in (self.report("unknown_status"),self.report("api_error",0),self.report("ignored_event")):
            with self.subTest(report=report),self.assertRaises(ValueError):self.assess(report)

    def test_valid_posting_report_is_checked(self):
        result=self.assess(self.report())
        self.assertTrue(result["execution_report_checked"])
        self.assertEqual(result["status"],"healthy")
