"""Cycle M1 (Muse): listRecords ordering and pagination.

Live evidence (2026-10-29, public PDS): with reverse=true the real
server returns the OLDEST post first; without it, the NEWEST first.
The duplicate check must therefore use the default order and follow
the cursor, otherwise it goes blind once the account is old enough.
"""
import unittest
import urllib.parse
from unittest import mock

from bot import bsky_client
from bot.bsky_client import BskyError, _list_recent_records

SESSION = {"did": "did:plc:fake", "accessJwt": "token"}


def record(idx):
    return {
        "uri": f"at://did:plc:fake/app.bsky.feed.post/rk{idx:03d}",
        "value": {
            "$type": "app.bsky.feed.post",
            "text": f"text {idx}",
            "createdAt": "2026-10-29T00:00:00Z",
        },
    }


class ListPaginationTests(unittest.TestCase):
    def fetch(self, pages):
        """pages: dict mapping cursor (None for first) -> response."""
        calls = []

        def fake_get(url, token=None):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            calls.append(query)
            self.assertNotIn("reverse", query)
            cursor = query.get("cursor", [None])[0]
            return pages[cursor]

        with mock.patch.object(bsky_client, "_get_json", fake_get):
            result = _list_recent_records("https://pds.example", SESSION,
                                          sleep=lambda s: None)
        return result, calls

    def test_default_order_request_has_no_reverse(self):
        result, calls = self.fetch({None: {"records": [record(2), record(1)]}})
        self.assertEqual([r["value"]["text"] for r in result],
                         ["text 2", "text 1"])
        self.assertEqual(len(calls), 1)

    def test_cursor_is_followed_until_exhausted(self):
        pages = {
            None: {"records": [record(3)], "cursor": "cur-a"},
            "cur-a": {"records": [record(2)], "cursor": "cur-b"},
            "cur-b": {"records": [record(1)]},
        }
        result, calls = self.fetch(pages)
        self.assertEqual([r["value"]["text"] for r in result],
                         ["text 3", "text 2", "text 1"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1]["cursor"], ["cur-a"])
        self.assertEqual(calls[2]["cursor"], ["cur-b"])

    def test_page_cap_stops_endless_cursors(self):
        pages = {None: {"records": [record(1)], "cursor": "loop"}}
        for i in range(10):
            pages["loop"] = {"records": [record(1)], "cursor": "loop"}
        result, calls = self.fetch(pages)
        self.assertEqual(len(calls), bsky_client.MAX_LIST_PAGES)
        self.assertEqual(len(result), bsky_client.MAX_LIST_PAGES)

    def test_malformed_cursor_rejected(self):
        pages = {None: {"records": [record(1)], "cursor": 12345}}
        with self.assertRaises(BskyError):
            self.fetch(pages)

    def test_empty_page_with_cursor_rejected(self):
        pages = {None: {"records": [], "cursor": "cur-a"}}
        with self.assertRaises(BskyError):
            self.fetch(pages)


if __name__ == "__main__":
    unittest.main()
