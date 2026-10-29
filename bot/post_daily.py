"""Scheduled post CLI for Bluesky (9 slots a day).

Default is dry-run. Without a selection it renders every slot for today
and posts nothing. --slot selects one slot; --due selects recent due slots.

A real post happens only when ALL of these are true:
  --post is passed, --slot or --due selects slots, POST_ENABLED=true is in
  the environment, and the Bluesky handle + app password are set.

Usage:
  python -m bot.post_daily                    # dry-run: all slots
  python -m bot.post_daily --slot promo_morning          # dry-run: one slot
  python -m bot.post_daily --slot promo_morning --post   # post, if enabled
  python -m bot.post_daily --due --post                 # catch up recent slots
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.state import StateError, already_posted_slot, load_state, prepare_state_path, record_post_slot  # noqa: E402
from bot.template import build_link_facets, render_template, validate_post  # noqa: E402
from bot.reporting import finish_report, new_report  # noqa: E402

REQUIRED_ENV = ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"]
CATCHUP_WINDOW = timedelta(hours=2)


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_slot(config: dict, slot_id: str) -> dict | None:
    for slot in config.get("slots", []):
        if slot["id"] == slot_id:
            return slot
    return None


def render_slot(slot: dict, now: datetime) -> str:
    return render_template(
        slot["template"], now, extra={"slot_time": slot["time_jst"]}
    )


def due_slots(config: dict, now: datetime) -> list[tuple[dict, datetime]]:
    """Slots due in the last two hours, oldest first (including midnight).

    Expired slots are deliberately not replayed in a burst after a long
    outage. Future slots are never posted early.
    """
    due = []
    for day in (now.date() - timedelta(days=1), now.date()):
        for slot in config.get("slots", []):
            slot_time = datetime.strptime(slot["time_jst"], "%H:%M").time()
            scheduled_at = datetime.combine(day, slot_time, tzinfo=now.tzinfo)
            if timedelta(0) <= now - scheduled_at <= CATCHUP_WINDOW:
                due.append((slot, scheduled_at))
    return sorted(due, key=lambda entry: entry[1])


def run_slot(slot: dict, config: dict, scheduled_at: datetime,
             state_path: Path, *, post: bool, outcomes: list | None = None) -> int:
    date_str = scheduled_at.strftime("%Y-%m-%d")
    def outcome(status: str, code: int = 0, **details) -> int:
        if outcomes is not None:
            outcomes.append({"slot_id": slot["id"], "scheduled_at": scheduled_at.isoformat(),
                             "status": status, **details})
        return code
    text = render_slot(slot, scheduled_at)
    print(f"--- slot {slot['id']} ({slot['time_jst']} JST) ---")
    print(text)
    print("-----------------")
    print("cost: free (Bluesky API has no per-post charge)")

    problems = validate_post(text)
    if problems:
        print("validation failed: " + "; ".join(problems))
        return outcome("validation_error", 1, detail="; ".join(problems))

    try:
        state = load_state(state_path)
    except StateError as exc:
        print(f"error: {exc}")
        return outcome("state_read_error", 1, detail=str(exc))
    if already_posted_slot(state, date_str, slot["id"]):
        print("skip: this slot was already posted for its scheduled date (state file)")
        return outcome("already_posted", detail="local state")

    if not post:
        print("dry-run: nothing was posted. Pass --post with POST_ENABLED=true to post.")
        return outcome("dry_run")
    if os.environ.get("POST_ENABLED") != "true":
        print("refused: --post was given but POST_ENABLED is not 'true'. Nothing posted.")
        return outcome("disabled", 1, detail="POST_ENABLED is not true")
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print("refused: missing credentials in environment: " + ", ".join(missing))
        return outcome("missing_credentials", 1, detail=", ".join(missing))

    try:
        prepare_state_path(state_path)
    except StateError as exc:
        print(f"error: {exc}; nothing posted")
        return outcome("state_preflight_error", 1, detail=str(exc))

    from bot.bsky_client import BskyError, create_post

    slot_time = datetime.strptime(slot["time_jst"], "%H:%M").time()
    try:
        result = create_post(
            text,
            os.environ["BLUESKY_HANDLE"],
            os.environ["BLUESKY_APP_PASSWORD"],
            facets=build_link_facets(text),
            slot_date=scheduled_at.date(),
            slot_time=slot_time,
            tz_name=config.get("timezone", "Asia/Tokyo"),
        )
    except BskyError as exc:
        print(f"error: post failed: {exc}")
        status = "authentication_error" if exc.status in (401, 403) else "api_error"
        return outcome(status, 1, detail=str(exc))
    try:
        record_post_slot(state_path, state, date_str, slot["id"], text)
    except OSError as exc:
        print(f"error: post exists but local state could not be saved: {exc}")
        return outcome("state_write_error", 1, uri=result.get("uri"),
                       detail="post exists; server-side duplicate check is required on retry")
    if result.get("already_posted"):
        print(f"skip: this slot was already posted (server check): "
              f"uri={result.get('uri')}")
        return outcome("already_posted", uri=result.get("uri"), detail="server check")
    print(f"posted: uri={result.get('uri')}")
    return outcome("posted", uri=result.get("uri"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduled posts to Bluesky (dry-run by default)")
    parser.add_argument("--post", action="store_true", help="actually post (needs --slot or --due and POST_ENABLED=true)")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--slot", default=None, help="one slot id from config")
    selection.add_argument("--due", action="store_true", help="select slots due in the last 2 hours")
    parser.add_argument("--config", default=str(ROOT / "config" / "post.json"))
    parser.add_argument("--state", default=str(ROOT / "state" / "last_post.json"))
    parser.add_argument("--report", default=None, help="write JSON outcomes (never credentials)")
    args = parser.parse_args(argv)

    report = new_report("post" if args.post else "dry_run")
    def finish(code: int, status: str | None = None, detail: str = "") -> int:
        if status:
            report["outcomes"].append({"status": status, "detail": detail})
        finish_report(report, code, args.report)
        return code

    try:
        config = load_config(Path(args.config))
        tz = ZoneInfo(config.get("timezone", "Asia/Tokyo"))
    except (ValueError, OSError, KeyError, AttributeError) as exc:
        return finish(1, "configuration_error", str(exc))
    now = datetime.now(tz)
    print(f"date: {now.strftime('%Y-%m-%d %H:%M')} ({config.get('timezone', 'Asia/Tokyo')})")

    if args.due:
        selected = due_slots(config, now)
        print(f"due: {len(selected)} slot(s) in the last 2 hours")
    elif args.slot is not None:
        slot = find_slot(config, args.slot)
        if slot is None:
            known = ", ".join(s["id"] for s in config.get("slots", []))
            print(f"error: unknown slot '{args.slot}'. known slots: {known}")
            return finish(1, "configuration_error", "unknown slot")
        selected = [(slot, now)]
    else:
        if args.post:
            print("refused: --post requires --slot or --due. Nothing posted.")
            return finish(1, "configuration_error", "--post requires --slot or --due")
        for slot in config.get("slots", []):
            text = render_slot(slot, now)
            problems = validate_post(text)
            print(f"--- slot {slot['id']} ({slot['time_jst']} JST) ---")
            print(text)
            if problems:
                print("validation failed: " + "; ".join(problems))
                return finish(1, "validation_error", "; ".join(problems))
        print("dry-run: nothing was posted.")
        return finish(0, "dry_run", "all slots previewed")

    status = 0
    if not selected:
        return finish(0, "no_due", "no slot is due in the last 2 hours")
    for slot, scheduled_at in selected:
        # A failed slot must not stop other due slots from being handled.
        status = max(status, run_slot(slot, config, scheduled_at,
                                     Path(args.state), post=args.post, outcomes=report["outcomes"]))
    return finish(status)


if __name__ == "__main__":
    raise SystemExit(main())
