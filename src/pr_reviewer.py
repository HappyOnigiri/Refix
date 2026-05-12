#!/usr/bin/env python3
"""GitHub PR fetcher - fetches PR metadata and CI status."""

import json
import os
import re
import sys
from datetime import datetime
from typing import Any, cast

from subprocess_helpers import SubprocessError, run_command
from type_defs import (
    CheckRunData,
    CheckStatus,
    CommitInfo,
    PRData,
)

_GITHUB_ACTIONS_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")


# Set UTF-8 encoding for output
if sys.stdout.encoding != "utf-8" and hasattr(sys.stdout, "buffer"):
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _filter_check_runs(runs: list[CheckRunData], repo: str) -> list[CheckRunData]:
    """check run をフィルタリングする。
    - workflow_dispatch トリガーの run を除外
    - 同名 check run は最新（id が最大）のみ保持
    """
    run_id_to_runs: dict[str, list[CheckRunData]] = {}
    no_run_id: list[CheckRunData] = []
    for r in runs:
        url = r.get("details_url") or r.get("html_url") or ""
        m = _GITHUB_ACTIONS_RUN_URL_RE.search(url)
        if m:
            run_id_to_runs.setdefault(m.group(1), []).append(r)
        else:
            no_run_id.append(r)

    current_run_id = os.environ.get("GITHUB_RUN_ID", "")
    excluded_run_ids: set[str] = set()
    for run_id in run_id_to_runs:
        # 現在の GitHub Actions run を除外（自身の check run が in_progress で CI ブロックを防ぐ）
        if current_run_id and run_id == current_run_id:
            excluded_run_ids.add(run_id)
            continue
        try:
            result = run_command(
                ["gh", "api", f"repos/{repo}/actions/runs/{run_id}", "--jq", ".event"],
                check=False,
                timeout=10,
            )
            if result.returncode == 0:
                event = (result.stdout or "").strip().strip('"')
                if event == "workflow_dispatch":
                    excluded_run_ids.add(run_id)
        except SubprocessError:
            pass

    filtered: list[CheckRunData] = []
    for run_id, run_list in run_id_to_runs.items():
        if run_id not in excluded_run_ids:
            filtered.extend(run_list)

    by_name: dict[str, CheckRunData] = {}
    for r in filtered:
        name = r.get("name") or ""
        existing = by_name.get(name)
        if existing is None or (r.get("id") or 0) > (existing.get("id") or 0):
            by_name[name] = r

    return list(by_name.values()) + no_run_id


def _flatten_paginated_response(data: Any) -> list[dict[str, Any]]:  # dict-any: ok
    """Flatten gh api --paginate/--slurp responses into a list of objects."""
    if not isinstance(data, list):
        return []

    items: list[dict[str, Any]] = []  # dict-any: ok
    for item in data:
        if isinstance(item, list):
            items.extend(entry for entry in item if isinstance(entry, dict))
        elif isinstance(item, dict):
            items.append(item)
    return items


def _fetch_check_runs_via_rest(repo: str, ref: str) -> list[CheckStatus]:
    """Fetch check runs for a commit via REST API. On error, returns []."""
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/commits/{ref}/check-runs",
        "--paginate",
        "--slurp",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        pages = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        return []
    raw_runs: list[CheckRunData] = []
    for page in pages if isinstance(pages, list) else []:
        if isinstance(page, dict):
            raw_runs.extend(
                cast(
                    list[CheckRunData],
                    [r for r in (page.get("check_runs") or []) if isinstance(r, dict)],
                )
            )
    raw_runs = _filter_check_runs(raw_runs, repo)
    rollup: list[CheckStatus] = []
    for r in raw_runs:
        rollup.append(
            {
                "name": r.get("name") or "",
                "conclusion": (r.get("conclusion") or "").upper(),
                "state": (r.get("status") or "").upper(),
                "detailsUrl": r.get("details_url") or r.get("html_url") or "",
                "targetUrl": r.get("details_url") or r.get("html_url") or "",
            }
        )
    return rollup


def _fetch_classic_statuses_via_rest(repo: str, sha: str) -> list[CheckStatus]:
    """Fetch classic commit statuses (Jenkins, Travis, etc.) via REST API."""
    cmd = ["gh", "api", f"repos/{repo}/commits/{sha}/status"]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    statuses = data.get("statuses") or []
    normalized: list[CheckStatus] = []
    for s in statuses:
        if not isinstance(s, dict):
            continue
        state = (s.get("state") or "").upper()
        normalized.append(
            {
                "name": str(
                    s.get("context") or s.get("description") or "unknown-status"
                ),
                "conclusion": state,
                "state": state,
                "detailsUrl": str(s.get("target_url") or ""),
                "targetUrl": str(s.get("target_url") or ""),
            }
        )
    return normalized


def fetch_pr_details(repo: str, pr_number: int) -> PRData:
    """Fetch PR details including commits, branch names, and CI checks."""
    base_json = "number,title,body,commits,createdAt,updatedAt,labels,headRefName,baseRefName,headRefOid"
    cmd = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        base_json,
    ]
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise SubprocessError(
            f"Failed to fetch PR details for {repo}#{pr_number}: {result.stderr.strip()}",
            returncode=result.returncode,
            stderr=result.stderr or "",
        )
    try:
        pr_data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse gh pr view output for {repo}#{pr_number}"
        ) from exc

    # Use headRefOid as primary source to avoid the 100-commit limit of gh pr view --json commits
    head_oid = str(pr_data.get("headRefOid") or "").strip()
    if not head_oid:
        commits = pr_data.get("commits") or []
        if commits:
            head_oid = (
                str(commits[-1].get("oid") or "")
                if isinstance(commits[-1], dict)
                else ""
            )
    if head_oid:
        check_runs = _fetch_check_runs_via_rest(repo, head_oid)
        classic_statuses = _fetch_classic_statuses_via_rest(repo, head_oid)
        all_checks = check_runs + classic_statuses
        if all_checks:
            pr_data["check_runs"] = all_checks

    return cast(PRData, pr_data)


def get_latest_commit_time(commits: list[CommitInfo]) -> datetime:
    """Get the timestamp of the latest commit."""
    if not commits:
        return datetime.min
    latest = commits[-1]
    return datetime.fromisoformat(
        latest.get("committedDate", "").replace("Z", "+00:00")
    )


def main():
    if len(sys.argv) < 3:
        print("Usage: python pr_reviewer.py <repo> <pr_number>")
        sys.exit(1)

    repo = sys.argv[1]
    pr_number = int(sys.argv[2])
    print(f"Fetching PR #{pr_number} from {repo}...")
    pr_data = fetch_pr_details(repo, pr_number)
    print(json.dumps(pr_data, indent=2, default=str))


if __name__ == "__main__":
    main()
