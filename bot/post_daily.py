"""Scheduled post CLI for Bluesky (8 slots a day).

Default is dry-run. Without --slot it renders every slot for today
and posts nothing. With --slot <id> it handles just that slot.

A real post happens only when ALL of these are true:
  --post is passed, --slot names one slot, POST_ENABLED=true is in
  the environment, and the Bluesky handle + app password are set.

Usage:
  python -m bot.post_daily                    # dry-run: all slots
  python -m bot.post_daily --slot promo_morning          # dry-run: one slot
  python -m bot.post_daily --slot promo_morning --post   # post, if enabled
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.state import already_posted_slot, load_state, record_post_slot  # noqa: E402
from bot.template import build_link_facets, render_template, validate_post  # noqa: E402

REQUIRED_ENV = ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduled posts to Bluesky (dry-run by default)")
    parser.add_argument("--post", action="store_true", help="actually post (needs --slot and POST_ENABLED=true)")
    parser.add_argument("--slot", default=None, help="slot id from config (required for --post)")
    parser.add_argument("--config", default=str(ROOT / "config" / "post.json"))
    parser.add_argument("--state", default=str(ROOT / "state" / "last_post.json"))
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    tz = ZoneInfo(config.get("timezone", "Asia/Tokyo"))
    now = datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")
    print(f"date: {now.strftime('%Y-%m-%d %H:%M')} ({config.get('timezone', 'Asia/Tokyo')})")

    if args.slot is None:
        if args.post:
            print("refused: --post requires --slot. Nothing posted.")
            return 1
        for slot in config.get("slots", []):
            text = render_slot(slot, now)
            problems = validate_post(text)
            print(f"--- slot {slot['id']} ({slot['time_jst']} JST) ---")
            print(text)
            if problems:
                print("validation failed: " + "; ".join(problems))
                return 1
        print("dry-run: nothing was posted.")
        return 0

    slot = find_slot(config, args.slot)
    if slot is None:
        known = ", ".join(s["id"] for s in config.get("slots", []))
        print(f"error: unknown slot '{args.slot}'. known slots: {known}")
        return 1

    text = render_slot(slot, now)
    print(f"--- slot {slot['id']} ({slot['time_jst']} JST) ---")
    print(text)
    print("-----------------")
    print("cost: free (Bluesky API has no per-post charge)")

    problems = validate_post(text)
    if problems:
        print("validation failed: " + "; ".join(problems))
        return 1

    state_path = Path(args.state)
    state = load_state(state_path)
    if already_posted_slot(state, date_str, slot["id"]):
        print("skip: this slot was already posted today according to the state file")
        return 0

    if not args.post:
        print("dry-run: nothing was posted. Pass --post with POST_ENABLED=true to post.")
        return 0
    if os.environ.get("POST_ENABLED") != "true":
        print("refused: --post was given but POST_ENABLED is not 'true'. Nothing posted.")
        return 1
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print("refused: missing credentials in environment: " + ", ".join(missing))
        return 1

    from bot.bsky_client import BskyError, create_post

    slot_time = datetime.strptime(slot["time_jst"], "%H:%M").time()
    try:
        result = create_post(
            text,
            os.environ["BLUESKY_HANDLE"],
            os.environ["BLUESKY_APP_PASSWORD"],
            facets=build_link_facets(text),
            slot_date=now.date(),
            slot_time=slot_time,
            tz_name=config.get("timezone", "Asia/Tokyo"),
        )
    except BskyError as exc:
        print(f"error: post failed: {exc}")
        return 1
    if result.get("precheck_failed"):
        print("warning: the server-side duplicate check could not be "
              "completed before posting")
    record_post_slot(state_path, state, date_str, slot["id"], text)
    if result.get("already_posted"):
        print(f"skip: this slot was already posted (server check): "
              f"uri={result.get('uri')}")
        return 0
    print(f"posted: uri={result.get('uri')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
