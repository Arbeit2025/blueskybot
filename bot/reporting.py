"""Machine-readable outcomes and a concise GitHub Actions summary."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_report(mode: str) -> dict:
    return {
        "schema_version": 1,
        "started_at": utc_now(),
        "source": os.environ.get("EVENT_NAME", "local"),
        "mode": mode,
        "outcomes": [],
    }


def finish_report(report: dict, exit_code: int, path: str | None = None) -> None:
    report["finished_at"] = utc_now()
    report["exit_code"] = exit_code
    report["counts"] = dict(Counter(x["status"] for x in report["outcomes"]))
    encoded = json.dumps(report, ensure_ascii=False)
    # Contains statuses and public post URIs, never credentials or environment dumps.
    print("BOT_REPORT_JSON=" + encoded)
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(encoded + "\n", encoding="utf-8")
        temporary.replace(destination)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = ["## Bluesky Bot 実行結果", "",
                 f"起動元: `{report['source']}` / モード: `{report['mode']}` / 終了コード: `{exit_code}`", "",
                 "| 枠 | 結果 | 投稿URI・詳細 |", "| --- | --- | --- |"]
        for item in report["outcomes"]:
            detail = item.get("uri") or item.get("detail", "")
            detail = str(detail).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {item.get('slot_id', 'run')} | {item['status']} | {detail} |")
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
