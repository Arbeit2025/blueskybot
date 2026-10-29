"""Trusted event selection and explicit posting/disabled/configuration states.

The external timer edits one owner-authored control issue. Issue text never
becomes a command, a slot name, a URL, or credentials.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from bot import post_daily
from bot.reporting import finish_report, new_report

REPOSITORY = "Arbeit2025/blueskybot"
CONTROL_ISSUE = 2


def trusted_external_event(event: dict) -> bool:
    if not isinstance(event, dict):
        return False
    repository = event.get("repository", {})
    issue = event.get("issue", {})
    sender, changes = event.get("sender", {}), event.get("changes", {})
    if not all(isinstance(value, dict) for value in (repository, issue, sender, changes)):
        return False
    repo_owner, issue_user = repository.get("owner", {}), issue.get("user", {})
    if not isinstance(repo_owner, dict) or not isinstance(issue_user, dict):
        return False
    owner = repo_owner.get("login")
    return bool(
        repository.get("full_name") == REPOSITORY
        and owner == "Arbeit2025"
        and event.get("action") == "edited"
        and issue.get("number") == CONTROL_ISSUE
        and issue.get("state") == "open"
        and issue_user.get("login") == owner
        and sender.get("login") == owner
        and "body" in changes
    )


def select_arguments(environment: dict, event: dict) -> list[str] | None:
    source = environment.get("EVENT_NAME")
    if source == "issues":
        if not trusted_external_event(event):
            return None
        return ["--due", "--post"]
    if source == "schedule":
        return ["--due", "--post"]
    if source != "workflow_dispatch":
        raise ValueError("unsupported event")
    slot = environment.get("INPUT_SLOT", "all") or "all"
    dry_run = environment.get("INPUT_DRY_RUN", "true")
    if dry_run not in ("true", "false"):
        raise ValueError("INPUT_DRY_RUN must be true or false")
    if slot == "all":
        return []  # all is always preview, even if dry_run=false
    if slot == "due":
        arguments = ["--due"]
    else:
        config = post_daily.load_config(post_daily.ROOT / "config" / "post.json")
        if not post_daily.find_slot(config, slot):
            raise ValueError("unknown slot")
        arguments = ["--slot", slot]
    if dry_run == "false":
        arguments.append("--post")
    return arguments


def main() -> int:
    report_path = os.environ.get("BOT_REPORT_PATH")
    report = new_report("event_gate")

    def stop(status: str, detail: str, code: int) -> int:
        print(f"{status}: {detail}")
        report["outcomes"].append({"status": status, "detail": detail})
        finish_report(report, code, report_path)
        return code

    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        event = json.loads(Path(event_path).read_text()) if event_path else {}
        arguments = select_arguments(dict(os.environ), event)
    except (ValueError, OSError) as exc:
        return stop("configuration_error", str(exc), 1)
    if arguments is None:
        return stop("ignored_event", "not an authorized control-issue body edit", 0)
    if "--post" in arguments:
        enabled = os.environ.get("POST_ENABLED", "")
        if enabled == "false":
            return stop("disabled", "POST_ENABLED=false; intentional stop, no posts", 0)
        if enabled != "true":
            return stop("configuration_error", "POST_ENABLED must explicitly be true or false", 1)
        missing = [name for name in post_daily.REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            return stop("missing_credentials", ", ".join(missing), 1)
    if report_path:
        arguments.extend(["--report", report_path])
    return post_daily.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
