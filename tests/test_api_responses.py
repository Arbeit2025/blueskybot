import io
import unittest
import urllib.error
from datetime import date, time
from unittest import mock
from zoneinfo import ZoneInfo

from bot import bsky_client as client

URI="at://did:plc:test/app.bsky.feed.post/key"
RECORD={"uri":URI,"value":{"$type":client.POST_COLLECTION,"text":"hello","createdAt":"2026-10-29T00:00:00Z"}}


class ApiResponseTests(unittest.TestCase):
    def post_with(self, transport, **kwargs):
        with mock.patch.object(client,"_request_json",transport):
            return client.create_post("hello","fake","fake",slot_date=date(2026,10,29),slot_time=time(9),sleep=lambda _:None,**kwargs)

    def transport(self, session=None, records=None, result=None):
        def request(method,url,payload=None,token=None):
            if "createSession" in url:return session if session is not None else {"did":"did:plc:test","accessJwt":"token"}
            if "listRecords" in url:return {"records":records if records is not None else []}
            return result if result is not None else {"uri":URI,"cid":"cid"}
        return request

    def test_bad_session_types_do_not_reach_post(self):
        for session in ({"did":[],"accessJwt":"token"},{"did":"not-a-did","accessJwt":"token"},
                        {"did":"did:plc:test","accessJwt":[]},{"did":"did:plc:test","accessJwt":"bad\r\ntoken"}):
            transport=mock.Mock(side_effect=self.transport(session=session))
            with self.subTest(session=session),self.assertRaises(client.BskyError):self.post_with(transport)
            self.assertEqual(transport.call_count,1)

    def test_malformed_history_stops_before_creating(self):
        bad=["text",None,{}, {"uri":URI,"value":[]},
             {"uri":URI,"value":{"text":"hello","createdAt":"2026-10-29T00:00:00Z"}},
             {**RECORD,"uri":"at://did:plc:other/app.bsky.feed.post/key"},
             {**RECORD,"value":{**RECORD["value"],"createdAt":"not-a-date"}},
             {**RECORD,"value":{**RECORD["value"],"createdAt":"2026-10-29T00:00:00"}}]
        for item in bad:
            transport=mock.Mock(side_effect=self.transport(records=[item]))
            with self.subTest(item=item),self.assertRaises(client.BskyError):self.post_with(transport)
            self.assertEqual(transport.call_count,2)

    def test_wrong_collection_cannot_be_post_success(self):
        with self.assertRaises(client.BskyError):
            self.post_with(self.transport(result={"uri":"at://did:plc:test/app.bsky.feed.like/key","cid":"cid"}))

    def test_wrong_account_cannot_be_post_success(self):
        with self.assertRaises(client.BskyError):
            self.post_with(self.transport(result={"uri":"at://did:plc:other/app.bsky.feed.post/key","cid":"cid"}))

    def test_invalid_cid_shapes_are_not_success(self):
        for cid in (None,[],""," ","abc\ndef","非ASCII"):
            with self.subTest(cid=cid),self.assertRaises(client.BskyError):
                self.post_with(self.transport(result={"uri":URI,"cid":cid}))

    def test_uri_rejects_query_fragment_extra_path_and_reserved_keys(self):
        for suffix in ("key?x=y","key#fragment","key/extra",".","..","","a b"):
            with self.subTest(suffix=suffix):
                self.assertFalse(client.is_post_uri("at://did:plc:test/app.bsky.feed.post/"+suffix))
        self.assertTrue(client.is_post_uri(URI,"did:plc:test","key"))

    def test_success_http_status_with_error_object_is_failure(self):
        response=io.BytesIO(b'{"error":"InvalidRequest","did":"did:plc:test","accessJwt":"token"}')
        with mock.patch.object(client.urllib.request,"urlopen",return_value=response),self.assertRaises(client.BskyError):
            client._get_json("https://example.invalid")

    def test_invalid_utf8_is_wrapped_as_api_error(self):
        with mock.patch.object(client.urllib.request,"urlopen",return_value=io.BytesIO(b'\xff')),self.assertRaises(client.BskyError):
            client._get_json("https://example.invalid")

    def test_oversized_response_is_rejected(self):
        response=io.BytesIO(b"x"*(client.MAX_RESPONSE_BYTES+1))
        with mock.patch.object(client.urllib.request,"urlopen",return_value=response),self.assertRaisesRegex(client.BskyError,"size limit"):
            client._get_json("https://example.invalid")

    def test_error_code_is_retained_without_exposing_server_message(self):
        exc=urllib.error.HTTPError("https://example.invalid",400,"bad",{},io.BytesIO(b'{"error":"RecordNotFound","message":"SECRET_SENTINEL"}'))
        with mock.patch.object(client.urllib.request,"urlopen",side_effect=exc):
            with self.assertRaises(client.BskyError) as caught:client._get_json("https://example.invalid")
        self.assertEqual(caught.exception.error_code,"RecordNotFound")
        self.assertNotIn("SECRET_SENTINEL",str(caught.exception))

    def test_pure_matcher_tolerates_bad_shapes(self):
        self.assertIsNone(client.find_posted_uri([None,"text",{"value":[]}],"hello",slot_date=date(2026,10,29),slot_time=time(9),tz=ZoneInfo("Asia/Tokyo")))

    def test_invalid_attempt_count_is_rejected_before_auth(self):
        for attempts in (0,-1,4,True,"3"):
            with mock.patch.object(client,"create_session") as auth,self.subTest(attempts=attempts),self.assertRaises(client.BskyError):
                client.create_post("hello","fake","fake",attempts=attempts)
            auth.assert_not_called()
