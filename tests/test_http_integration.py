"""Real CLI/client HTTP round trips, confined to a loopback fake PDS."""
import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bot import bsky_client, check_access, post_daily


class HttpIntegrationTests(unittest.TestCase):
    def test_all_nine_slots_and_two_rechecks_with_fresh_state(self):
        records, calls = [], []
        test = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def reject(self):
                body=b'{"error":"RecordNotFound"}'
                self.send_response(400)
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(self.path)
                if self.path.endswith("createSession"):
                    test.assertEqual(payload, {"identifier": "fake.bsky.social", "password": "fake-password"})
                    self.respond({"did": "did:plc:fake", "accessJwt": "fake-token", "handle": "fake.bsky.social"})
                elif self.path.endswith("createRecord"):
                    test.assertEqual(self.headers.get("Authorization"), "Bearer fake-token")
                    test.assertEqual(payload["repo"], "did:plc:fake")
                    test.assertEqual(payload["collection"], "app.bsky.feed.post")
                    key=payload.get("rkey",f"record{len(records)+1}")
                    uri = f"at://did:plc:fake/app.bsky.feed.post/{key}"
                    records.insert(0, {"uri": uri, "value": payload["record"]})
                    self.respond({"uri": uri, "cid": "fake-cid"})
                else:
                    raise AssertionError("unexpected POST path")

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path.endswith("getRecord"):
                    key=parse_qs(parsed.query)["rkey"][0]
                    found=next((r for r in records if r["uri"].endswith("/"+key)),None)
                    if found:self.respond(found)
                    else:self.reject()
                    return
                test.assertTrue(parsed.path.endswith("listRecords"))
                # Newest-first is the API default; reverse=true would
                # return the oldest records first on the real PDS.
                test.assertNotIn("reverse", parse_qs(parsed.query))
                self.respond({"records": records[:100]})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_connect = socket.socket.connect
        port = server.server_port
        def guarded_connect(sock, address):
            if address != ("127.0.0.1", port):
                raise AssertionError(f"external connection refused: {address}")
            return original_connect(sock, address)
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.dict(os.environ, {"POST_ENABLED": "true", "BLUESKY_HANDLE": "fake.bsky.social",
                        "BLUESKY_APP_PASSWORD": "fake-password", "BLUESKY_PDS_URL": f"http://127.0.0.1:{port}",
                        "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}), \
                    mock.patch.object(socket.socket, "connect", guarded_connect), \
                    mock.patch.object(post_daily, "load_expected_did", return_value="did:plc:fake"), \
                    mock.patch.object(check_access, "load_expected_did", return_value="did:plc:fake"), \
                    contextlib.redirect_stdout(io.StringIO()):
                config = post_daily.load_config(post_daily.ROOT / "config/post.json")
                statuses = []
                counter = 0
                for slot in sorted(config["slots"], key=lambda s: s["time_jst"]):
                    hour, minute = map(int, slot["time_jst"].split(":"))
                    scheduled = datetime(2026, 10, 30, hour, minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                    for offset in (7, 17, 67):
                        now = scheduled + timedelta(minutes=offset)
                        with mock.patch.object(post_daily, "datetime", wraps=datetime) as cli_clock, \
                                mock.patch.object(bsky_client, "datetime", wraps=datetime) as client_clock:
                            cli_clock.now.return_value = client_clock.now.return_value = now
                            report = Path(tmp) / f"report{counter}.json"
                            # Different state file every time: server-side duplicate checks must work.
                            code = post_daily.main(["--due", "--post", "--state", str(Path(tmp) / f"state{counter}.json"),
                                                    "--report", str(report)])
                        self.assertEqual(code, 0)
                        statuses.extend(x["status"] for x in json.loads(report.read_text())["outcomes"])
                        counter += 1
                self.assertEqual(counter, 27)
                self.assertEqual(len(records), 9)
                self.assertEqual(statuses.count("posted"), 9)
                self.assertGreaterEqual(statuses.count("already_posted"), 18)
                expected = sorted(slot["template"] for slot in config["slots"])
                self.assertEqual(sorted(r["value"]["text"] for r in records), expected)
                self.assertEqual(check_access.main(), 0)
                self.assertEqual(len(records), 9)  # read-only diagnostic cannot add a post
                self.assertEqual(sum(path.endswith("createRecord") for path in calls), 9)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
