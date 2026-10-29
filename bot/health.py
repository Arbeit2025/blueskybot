"""Read-only health assessment for an independent hourly watchdog.

Inputs come from the authenticated GitHub connector (workflow runs and the
latest execution report) and Bluesky's public author feed. No credentials
are needed here and this module never posts or edits GitHub.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.bsky_client import POST_COLLECTION, find_posted_uri, is_post_uri
from bot.post_daily import ROOT, render_slot


def parse_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("timestamp must contain a timezone")
    return moment.astimezone(timezone.utc)


def validate_execution(execution: dict, now: datetime, maximum_age: int) -> None:
    if not isinstance(execution, dict) or type(execution.get("exit_code")) is not int \
            or execution["exit_code"] not in (0, 1):
        raise ValueError("execution report has no valid exit code")
    outcomes, counts = execution.get("outcomes"), execution.get("counts")
    if not isinstance(outcomes, list) or not outcomes or not isinstance(counts, dict) \
            or any(not isinstance(x, dict) or not isinstance(x.get("status"), str) for x in outcomes) \
            or any(type(value) is not int or value <= 0 for value in counts.values()) \
            or dict(Counter(x["status"] for x in outcomes)) != counts:
        raise ValueError("execution report outcomes and counts are inconsistent")
    known = {"posted","already_posted","no_due","disabled","configuration_error","missing_credentials",
             "authentication_error","api_error","state_write_error","state_read_error","state_preflight_error","validation_error","internal_error"}
    if not set(counts).issubset(known):
        raise ValueError("execution report is not a recognized posting outcome")
    if execution.get("source") not in ("schedule", "issues") or execution.get("mode") not in ("post", "event_gate"):
        raise ValueError("execution report is not an automatic posting result")
    started, finished = parse_time(execution["started_at"]), parse_time(execution["finished_at"])
    if started > finished or finished > now + timedelta(minutes=5) \
            or now - finished > timedelta(minutes=maximum_age):
        raise ValueError("execution report is stale or has invalid timestamps")
    if "ignored_event" in counts:
        raise ValueError("ignored events do not prove posting health")
    if "disabled" in counts and (counts != {"disabled": 1} or execution["exit_code"] != 0):
        raise ValueError("disabled outcome is mixed with an error or another result")
    if execution["exit_code"] == 0 and any(status.endswith("_error") or status == "missing_credentials" for status in counts):
        raise ValueError("successful exit code contradicts error outcomes")


def assess(runs: dict, feed: dict, config: dict, operations: dict,
           now: datetime, execution: dict | None = None) -> dict:
    if not isinstance(runs.get("workflow_runs"), list):
        raise ValueError("workflow_runs must be a list")
    if not isinstance(feed.get("feed"), list):
        raise ValueError("feed must be a list; API errors are not an empty feed")
    if now.tzinfo is None:
        raise ValueError("health timestamp must contain a timezone")
    tz = ZoneInfo(config.get("timezone", "Asia/Tokyo"))
    since = parse_time(operations["monitor_since_utc"])
    now = now.astimezone(timezone.utc)
    if since > now:
        raise ValueError("monitor start is in the future")
    expected_did = operations.get("expected_did")
    if not isinstance(expected_did, str) or not is_post_uri(f"at://{expected_did}/{POST_COLLECTION}/probe"):
        raise ValueError("expected_did must identify the monitored account")
    local = now.astimezone(tz)
    result = {"checked_at": now.isoformat(), "status": "healthy", "problems": [],
              "missing_slots": [], "expected_slots": [], "latest_native_run": None,
              "latest_external_run": None, "execution_report_checked": execution is not None}
    if execution is not None:
        validate_execution(execution, now, operations["external_heartbeat_minutes"])
        if execution["counts"].get("disabled"):
            result.update(status="disabled", alert_code="disabled")
            return result
        if execution["exit_code"] != 0:
            result["problems"].append("execution_failed")

    automatic = [run for run in runs["workflow_runs"]
                 if run.get("path") == ".github/workflows/daily-post.yml"
                 and run.get("event") in ("schedule", "issues")
                 and run.get("status") in ("completed", "in_progress")
                 and run.get("conclusion") not in ("skipped", "cancelled")]
    def started_at(run):
        return parse_time(run.get("run_started_at") or run["created_at"])
    def latest(event):
        candidates = [run for run in automatic if run["event"] == event]
        return max(candidates, key=lambda r: parse_time(r["created_at"]), default=None)
    native, external = latest("schedule"), latest("issues")
    for name, run in (("native", native), ("external", external)):
        if run:
            result[f"latest_{name}_run"] = {"id": run["id"], "created_at": run["created_at"],
                                                "conclusion": run.get("conclusion")}
    def stale(run, minutes):
        moment = started_at(run) if run else since
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
    if any(run.get("status") == "in_progress" and now - started_at(run) > timedelta(minutes=10) for run in automatic):
        result["problems"].append("automatic_run_stuck")

    records = []
    for item in feed["feed"]:
        # A repost is not a new original post from this bot.
        if not isinstance(item, dict):
            raise ValueError("author feed contains a malformed item")
        reason = item.get("reason")
        if reason is not None and not isinstance(reason, dict):
            raise ValueError("author feed contains a malformed reason")
        if reason and reason.get("$type") == "app.bsky.feed.defs#reasonRepost":
            continue
        post = item.get("post", {})
        if not isinstance(post, dict) or not isinstance(post.get("author"), dict) \
                or post["author"].get("did") != expected_did \
                or not is_post_uri(post.get("uri"), expected_did):
            raise ValueError("author feed contains a post from an unexpected account or invalid URI")
        value = post.get("record", {})
        if not isinstance(value, dict) or value.get("$type") != POST_COLLECTION \
                or not isinstance(value.get("createdAt"), str) or not isinstance(value.get("text"), str):
            raise ValueError("author feed contains a malformed post record")
        try:
            created = parse_time(value["createdAt"])
        except (ValueError, TypeError) as exc:
            raise ValueError("author feed contains an invalid timestamp") from exc
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
