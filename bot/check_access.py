"""Read-only production diagnostic: log in and list recent own posts.

This module has no createRecord call and never prints session tokens.
"""
from __future__ import annotations

import os

from bot.bsky_client import BskyError, _list_recent_records, create_session, pds_url
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
            root = pds_url()
            session = create_session(os.environ["BLUESKY_HANDLE"], os.environ["BLUESKY_APP_PASSWORD"], root)
            records = _list_recent_records(root, session)
            report["outcomes"].append({"status": "access_verified", "detail": f"authenticated; {len(records)} recent record(s) readable; no post sent"})
        except BskyError as exc:
            code = 1
            status = "authentication_error" if exc.status in (401, 403) else "api_error"
            report["outcomes"].append({"status": status, "detail": str(exc)})
    finish_report(report, code, os.environ.get("BOT_REPORT_PATH"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
