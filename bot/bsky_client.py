"""Minimal Bluesky (AT Protocol) client, stdlib only.

Authentication uses an *app password* (Settings -> Privacy and
Security -> App Passwords on Bluesky), never the account's main
password. An app password can be revoked on its own if it leaks.

Flow (XRPC over HTTPS):
  1. POST /xrpc/com.atproto.server.createSession  {identifier, password}
     -> {accessJwt, did, handle}
  2. POST /xrpc/com.atproto.repo.createRecord     (Bearer accessJwt)
     {repo: did, collection: "app.bsky.feed.post", record: {...}}
     -> {uri, cid}

The PDS base URL defaults to https://bsky.social and can be overridden
with BLUESKY_PDS_URL for accounts hosted on another PDS.

Network is touched only inside create_session() / create_post().
build_post_record() is a pure function so it can be unit-tested.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

DEFAULT_PDS_URL = "https://bsky.social"


def pds_url() -> str:
    return os.environ.get("BLUESKY_PDS_URL", DEFAULT_PDS_URL).rstrip("/")


def format_created_at(now: datetime | None = None) -> str:
    """AT Protocol timestamps: UTC ISO-8601 with a trailing 'Z'."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_post_record(text: str, facets: list[dict] | None = None,
                      now: datetime | None = None) -> dict:
    """Build the app.bsky.feed.post record for `text` (pure)."""
    record: dict = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "langs": ["ja"],
        "createdAt": format_created_at(now),
    }
    if facets:
        record["facets"] = facets
    return record


def _post_json(url: str, payload: dict, token: str | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def create_session(handle: str, app_password: str, base: str | None = None) -> dict:
    """Log in with handle + app password. Returns the session dict."""
    root = (base or pds_url()).rstrip("/")
    return _post_json(
        f"{root}/xrpc/com.atproto.server.createSession",
        {"identifier": handle, "password": app_password},
    )


def create_post(text: str, handle: str, app_password: str,
                facets: list[dict] | None = None,
                base: str | None = None) -> dict:
    """Post `text` to Bluesky. Raises on HTTP error. Returns {uri, cid}."""
    root = (base or pds_url()).rstrip("/")
    session = create_session(handle, app_password, root)
    record = build_post_record(text, facets)
    return _post_json(
        f"{root}/xrpc/com.atproto.repo.createRecord",
        {
            "repo": session["did"],
            "collection": "app.bsky.feed.post",
            "record": record,
        },
        token=session["accessJwt"],
    )
