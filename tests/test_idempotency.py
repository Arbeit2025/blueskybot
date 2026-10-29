import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from unittest import mock
from urllib.parse import parse_qs,urlparse
from zoneinfo import ZoneInfo

from bot import bsky_client as c


class Store:
    def __init__(self, barrier=None):
        self.records={}
        self.lock=threading.Lock()
        self.barrier=barrier
        self.creates=0
        self.get_error=None
        self.reject_list=False
        self.lose_response=False
        self.bad_success=False

    def request(self,method,url,payload=None,token=None):
        if "createSession" in url:return {"did":"did:plc:test","accessJwt":"token"}
        if "getRecord" in url:
            if self.get_error:raise self.get_error
            key=parse_qs(urlparse(url).query)["rkey"][0]
            with self.lock:item=copy.deepcopy(self.records.get(key))
            if item:return item
            raise c.BskyError("missing",status=400,error_code="RecordNotFound")
        if "listRecords" in url:
            if self.reject_list:raise AssertionError("direct-key lookup must not scan history")
            with self.lock:items=copy.deepcopy(list(self.records.values()))[:100]
            if self.barrier:self.barrier.wait(timeout=5)
            return {"records":items}
        if "createRecord" in url:
            key=payload.get("rkey","legacy")
            with self.lock:
                self.creates+=1
                if key in self.records:raise c.BskyError("already exists",status=400,error_code="InvalidRequest")
                uri=f"at://did:plc:test/app.bsky.feed.post/{key}"
                self.records[key]={"uri":uri,"cid":"cid","value":copy.deepcopy(payload["record"])}
            if self.lose_response:raise c.BskyError("response lost",transient=True)
            return {} if self.bad_success else {"uri":uri,"cid":"cid"}
        raise AssertionError("unexpected endpoint")


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        patcher=mock.patch.object(c,"format_created_at",return_value="2026-10-29T01:00:30Z")
        patcher.start();self.addCleanup(patcher.stop)

    def post(self,store,**kwargs):
        options={"slot_date":date(2026,10,29),"slot_time":time(10),"slot_id":"promo","sleep":lambda _:None,**kwargs}
        with mock.patch.object(c,"_request_json",store.request):
            return c.create_post("hello","fake","fake",**options)

    def test_stable_key_is_a_valid_tid_and_has_scheduled_timestamp(self):
        zone=ZoneInfo("Asia/Tokyo")
        key=c.slot_record_key(date(2026,10,29),time(10),zone,"promo")
        self.assertRegex(key,r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")
        self.assertEqual(key,c.slot_record_key(date(2026,10,29),time(10),zone,"promo"))
        value=0
        for char in key:value=value*32+"234567abcdefghijklmnopqrstuvwxyz".index(char)
        self.assertEqual(value>>10,1793235600000000)

    def test_nine_configured_slots_and_days_have_distinct_keys(self):
        from bot.post_daily import load_config,ROOT
        config=load_config(ROOT/"config/post.json")
        keys=set()
        for day in (28,29,30):
            for slot in config["slots"]:
                hour,minute=map(int,slot["time_jst"].split(":"))
                keys.add(c.slot_record_key(date(2026,10,day),time(hour,minute),ZoneInfo("Asia/Tokyo"),slot["id"]))
        self.assertEqual(len(keys),27)

    def test_fresh_client_reuses_one_server_record(self):
        store=Store()
        first=self.post(store);second=self.post(store)
        self.assertEqual(first["uri"],second["uri"])
        self.assertTrue(second["already_posted"])
        self.assertEqual(store.creates,1)

    def test_simultaneous_runs_converge_to_one_record(self):
        store=Store(threading.Barrier(2))
        with mock.patch.object(c,"_request_json",store.request),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:c.create_post("hello","fake","fake",slot_date=date(2026,10,29),slot_time=time(10),slot_id="promo",sleep=lambda _:None),range(2)))
        self.assertEqual(store.creates,2)  # Both passed the stale pre-check.
        self.assertEqual(len(store.records),1)
        self.assertEqual(len({r["uri"] for r in results}),1)
        self.assertEqual(sum(bool(r.get("already_posted")) for r in results),1)

    def test_last_attempt_response_loss_is_reconciled(self):
        store=Store();store.lose_response=True
        result=self.post(store,attempts=1)
        self.assertTrue(result["already_posted"])
        self.assertEqual(store.creates,1)
        self.assertEqual(len(store.records),1)

    def test_legacy_final_response_loss_is_also_reconciled(self):
        store=Store();store.lose_response=True
        result=self.post(store,slot_id=None,attempts=1)
        self.assertTrue(result["already_posted"])
        self.assertEqual(store.creates,1)

    def test_malformed_success_is_verified_by_exact_key(self):
        store=Store();store.bad_success=True
        result=self.post(store)
        self.assertTrue(result["already_posted"])
        self.assertEqual(store.creates,1)

    def test_fixed_key_lookup_does_not_depend_on_last_hundred_records(self):
        store=Store();first=self.post(store)
        store.reject_list=True
        self.assertEqual(self.post(store)["uri"],first["uri"])
        self.assertEqual(store.creates,1)

    def test_conflicting_record_is_not_overwritten(self):
        store=Store()
        key=c.slot_record_key(date(2026,10,29),time(10),ZoneInfo("Asia/Tokyo"),"promo")
        store.records[key]={"uri":f"at://did:plc:test/app.bsky.feed.post/{key}","value":{"$type":c.POST_COLLECTION,"text":"different","createdAt":"2026-10-29T01:00:30Z"}}
        before=copy.deepcopy(store.records)
        with self.assertRaisesRegex(c.BskyError,"refusing to overwrite"):self.post(store)
        self.assertEqual(store.records,before);self.assertEqual(store.creates,0)

    def test_unknown_lookup_error_never_becomes_record_absence(self):
        for error in (c.BskyError("forbidden",status=403),c.BskyError("bad",status=400,error_code="InvalidRequest"),c.BskyError("offline",transient=True)):
            store=Store();store.get_error=error
            with self.subTest(error=str(error)),self.assertRaises(c.BskyError):self.post(store)
            self.assertEqual(store.creates,0)

    def test_legacy_random_key_post_is_still_recognized(self):
        store=Store()
        store.records["oldrandom"]={"uri":"at://did:plc:test/app.bsky.feed.post/oldrandom","value":{"$type":c.POST_COLLECTION,"text":"hello","createdAt":"2026-10-29T01:00:30Z"}}
        result=self.post(store)
        self.assertTrue(result["already_posted"]);self.assertEqual(store.creates,0)

    def test_missing_slot_context_is_rejected_before_login(self):
        with mock.patch.object(c,"create_session") as login,self.assertRaises(c.BskyError):
            c.create_post("hello","fake","fake",slot_id="promo")
        login.assert_not_called()

    def test_read_only_diagnostic_reports_direct_lookup_failure(self):
        import contextlib,io,os
        from bot import check_access
        record={"uri":"at://did:plc:test/app.bsky.feed.post/key","value":{"text":"hello"}}
        with mock.patch.dict(os.environ,{"BLUESKY_HANDLE":"fake","BLUESKY_APP_PASSWORD":"fake"},clear=True), \
             mock.patch.object(check_access,"create_session",return_value={"did":"did:plc:test","accessJwt":"fake"}), \
             mock.patch.object(check_access,"_list_recent_records",return_value=[record]), \
             mock.patch.object(check_access,"_get_slot_record",side_effect=c.BskyError("lookup unavailable")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(check_access.main(),1)
        self.assertIn("api_error",output.getvalue())
        self.assertNotIn("access_verified",output.getvalue())
