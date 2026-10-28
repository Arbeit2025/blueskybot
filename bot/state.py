"""Same-day duplicate guard backed by a small JSON state file.

State format: {"last_post_date": "2026-10-29", "last_text_hash": "..."}
Note: on GitHub Actions the filesystem is ephemeral, so this guard is a
local/second-layer defence. The primary defence is the once-a-day
schedule; the date embedded in the template also makes each day's text
unique, so an accidental re-run does not look like an exact duplicate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def already_posted_on(state: dict, date_str: str) -> bool:
    return state.get("last_post_date") == date_str


def record_post(path: Path, date_str: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_post_date": date_str, "last_text_hash": text_hash(text)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
