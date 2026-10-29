"""Read-only health assessment for an independent hourly watchdog.

Inputs come from the authenticated GitHub connector (workflow runs and the
latest execution report) and Bluesky's public author feed. No credentials
are needed here and this module never posts or edits GitHub.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.bsky_client import find_posted_uri
from bot.post_daily import ROOT, render_slot


def parse_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("timestamp must contain a timezone")
    return moment.astimezone(timezone.utc)


def assess(runs: dict, feed: dict, config: dict, operations: dict,
           now: datetime, execution: dict | None = None) -> dict:
    if not isinstance(runs.get("workflow_runs"), list):
        raise ValueError("workflow_runs must be a list")
    if not isinstance(feed.get("feed"), list):
        raise ValueError("feed must be a list; API errors are not an empty feed")
    tz = ZoneInfo(config.get("timezone", "Asia/Tokyo"))
    since = parse_time(operations["monitor_since_utc"])
    now = now.astimezone(timezone.utc)
    local = now.astimezone(tz)
    result = {"checked_at": now.isoformat(), "status": "healthy", "problems": [],
              "missing_slots": [], "expected_slots": [], "latest_native_run": None,
              "latest_external_run": None, "execution_report_checked": execution is not None}
    if execution and execution.get("counts", {}).get("disabled"):
        result["status"] = "disabled"
        return result
    if execution and execution.get("exit_code") != 0:
        result["problems"].append("execution_failed")

    automatic = [run for run in runs["workflow_runs"]
                 if run.get("path") == ".github/workflows/daily-post.yml"
                 and run.get("event") in ("schedule", "issues")
                 and run.get("conclusion") not in ("skipped", "cancelled")]
    def latest(event):
        candidates = [run for run in automatic if run["event"] == event]
        return max(candidates, key=lambda r: parse_time(r["created_at"]), default=None)
    native, external = latest("schedule"), latest("issues")
    for name, run in (("native", native), ("external", external)):
        if run:
            result[f"latest_{name}_run"] = {"id": run["id"], "created_at": run["created_at"],
                                                "conclusion": run.get("conclusion")}
    def stale(run, minutes):
        moment = parse_time(run["created_at"]) if run else since
        return now - moment > timedelta(minutes=minutes)
    native_stale = stale(native, operations["native_heartbeat_minutes"])
    external_stale = stale(external, operations["external_heartbeat_minutes"])
    if native_stale:
        result["problems"].append("native_scheduler_stale")
    if native_stale and external_stale:
        result["problems"].append("all_automatic_triggers_stale")
    newest = max(automatic, key=lambda r: parse_time(r["created_at"]), default=None)
    if newest and newest.get("conclusion") in ("failure", "timed_out", "action_required", "startup_failure"):
        result["problems"].append("latest_automatic_run_failed")

    records = []
    for item in feed["feed"]:
        # A repost is not a new original post from this bot.
        if item.get("reason"):
            continue
        post = item.get("post", {})
        value = post.get("record", {})
        if not isinstance(value, dict) or not value.get("createdAt"):
            continue
        try:
            created = parse_time(value["createdAt"])
        except (ValueError, TypeError):
            continue
        if created <= now:
            records.append({"uri": post.get("uri"), "value": value})
    expected = []
    cutoff = max(since, now - timedelta(hours=24))
    for day in (local.date() - timedelta(days=1), local.date()):
        for slot in config["slots"]:
            clock = datetime.strptime(slot["time_jst"], "%H:%M").time()
            scheduled = datetime.combine(day, clock, tzinfo=tz)
            if cutoff <= scheduled <= now - timedelta(minutes=operations["post_grace_minutes"]):
                expected.append((slot, scheduled, clock))
    # Do not label missing posts when an unpaginated feed is too short to prove absence.
    if feed.get("cursor") and expected:
        earliest_needed = min(when for _, when, _ in expected) - timedelta(minutes=30)
        oldest = min((parse_time(r["value"]["createdAt"]) for r in records), default=now)
        if oldest > earliest_needed:
            raise ValueError("feed has more pages; paginate before declaring posts missing")
    for slot, scheduled, clock in expected:
        key = f"{scheduled.date().isoformat()}:{slot['id']}"
        result["expected_slots"].append(key)
        found = find_posted_uri(records, render_slot(slot, scheduled),
                                slot_date=scheduled.date(), slot_time=clock, tz=tz)
        if not found:
            result["missing_slots"].append(key)
    if result["missing_slots"]:
        result["problems"].append("scheduled_posts_missing")
    severe = set(result["problems"]) - {"native_scheduler_stale"}
    if severe:
        result["status"] = "unhealthy"
    elif native_stale:
        result["status"] = "degraded"
    result["alert_code"] = ",".join(sorted(result["problems"])) or "ok"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-json", required=True)
    parser.add_argument("--feed-json", required=True)
    parser.add_argument("--execution-json")
    parser.add_argument("--now", help="timezone-aware timestamp, for reproducible checks")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        def read(path):
            return json.loads(Path(path).read_text(encoding="utf-8"))
        result = assess(read(args.runs_json), read(args.feed_json),
                        read(ROOT / "config/post.json"), read(ROOT / "config/operations.json"),
                        parse_time(args.now) if args.now else datetime.now(timezone.utc),
                        read(args.execution_json) if args.execution_json else None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result = {"status": "unknown", "alert_code": "health_check_failed", "detail": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False)
    print("BOT_HEALTH_JSON=" + encoded)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    return 1 if result["status"] in ("unhealthy", "unknown") else 0


if __name__ == "__main__":
    raise SystemExit(main())
