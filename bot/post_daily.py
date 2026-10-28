"""Daily fixed-post CLI for Bluesky.

Default is dry-run: it renders today's post and prints it, posting
nothing. A real post happens only when ALL of these are true:
  --post is passed, POST_ENABLED=true is in the environment, and the
  Bluesky handle + app password are set. This is deliberate: enabling
  posting is a conscious step, never the default.

Usage:
  python -m bot.post_daily                 # dry-run (default)
  python -m bot.post_daily --post          # post, if enabled + creds set
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

from bot.state import already_posted_on, load_state, record_post  # noqa: E402
from bot.template import build_link_facets, render_template, validate_post  # noqa: E402

REQUIRED_ENV = ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"]


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily fixed post to Bluesky (dry-run by default)")
    parser.add_argument("--post", action="store_true", help="actually post (needs POST_ENABLED=true)")
    parser.add_argument("--config", default=str(ROOT / "config" / "post.json"))
    parser.add_argument("--state", default=str(ROOT / "state" / "last_post.json"))
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    tz = ZoneInfo(config.get("timezone", "Asia/Tokyo"))
    now = datetime.now(tz)
    text = render_template(config["template"], now)

    problems = validate_post(text)
    print(f"date: {now.strftime('%Y-%m-%d %H:%M')} ({config.get('timezone', 'Asia/Tokyo')})")
    print("--- post text ---")
    print(text)
    print("-----------------")
    print("cost: free (Bluesky API has no per-post charge)")
    if problems:
        print("validation failed: " + "; ".join(problems))
        return 1

    state_path = Path(args.state)
    state = load_state(state_path)
    if already_posted_on(state, now.strftime("%Y-%m-%d")):
        print("skip: already posted today according to the state file")
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

    from bot.bsky_client import create_post

    result = create_post(
        text,
        os.environ["BLUESKY_HANDLE"],
        os.environ["BLUESKY_APP_PASSWORD"],
        facets=build_link_facets(text),
    )
    record_post(state_path, now.strftime("%Y-%m-%d"), text)
    print(f"posted: uri={result.get('uri')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
