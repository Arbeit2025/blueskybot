"""Read-only production diagnostic: log in and list recent own posts.

This module has no createRecord call and never prints session tokens.
"""
from __future__ import annotations

import os
import time

from bot.bsky_client import BskyError, _get_slot_record, _list_recent_records, create_session, pds_url
from bot.post_daily import ROOT, load_expected_did
from bot.reporting import finish_report, new_report


def main() -> int:
    report = new_report("read_only_access_check")
    code = 0
    missing = [name for name in ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD")
               if not os.environ.get(name)]
    if missing:
        code = 1
        report["outcomes"].append({"status": "missing_credentials", "detail": ", ".join(missing)})
    else:
        try:
            expected_did = load_expected_did(ROOT / "config" / "operations.json")
        except ValueError as exc:
            code = 1
            report["outcomes"].append({"status": "configuration_error", "detail": str(exc)})
            finish_report(report, code, os.environ.get("BOT_REPORT_PATH"))
            return code
        try:
            root = pds_url()
            session = create_session(os.environ["BLUESKY_HANDLE"], os.environ["BLUESKY_APP_PASSWORD"], root)
            if expected_did is not None and session["did"] != expected_did:
                code = 1
                report["outcomes"].append({"status": "authentication_error",
                                           "detail": "identity mismatch: session account differs from operations expected_did"})
                finish_report(report, code, os.environ.get("BOT_REPORT_PATH"))
                return code
            records = _list_recent_records(root, session)
            if records:
                record=records[0]
                _get_slot_record(root,session,record["uri"].rsplit("/",1)[1],record["value"]["text"],sleep=time.sleep)
            direct = "direct lookup completed" if records else "direct lookup not applicable (empty history)"
            report["outcomes"].append({"status": "access_verified", "detail": f"authenticated; {len(records)} record(s) readable; {direct}; no post sent"})
        except BskyError as exc:
            code = 1
            status = "authentication_error" if exc.status in (401, 403) else "api_error"
            report["outcomes"].append({"status": status, "detail": str(exc)})
    finish_report(report, code, os.environ.get("BOT_REPORT_PATH"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
