#!/usr/bin/env python3
"""State comment management for Refix log."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from errors import ConfigError
from i18n import t
from subprocess_helpers import run_command
from type_defs import LoggedCommit, SelfReviewFinding, SelfReviewLogEntry

# --- ローカルファイルモード設定 ---
_use_local_state: bool = False
_local_state_dir: str = "state"


def configure_local_state(
    *, use_local_state: bool, local_state_dir: str = "state"
) -> None:
    """ローカルファイルモードの設定。main() で一度呼び出す。"""
    global _use_local_state, _local_state_dir
    if use_local_state and os.environ.get("GITHUB_ACTIONS") == "true":
        raise ConfigError(
            "use_local_state is not supported in GitHub Actions. "
            "Local state files do not persist across CI runs."
        )
    _use_local_state = use_local_state
    _local_state_dir = local_state_dir


STATE_COMMENT_MARKER = "<!-- refix-state-comment -->"
STATE_COMMENT_TITLE = "### 🤖 Refix Status"
STATE_COMMENT_MAX_LENGTH = 60000
REFIX_LOG_SECTION_START_MARKER = "<!-- refix-log-start -->"
REFIX_LOG_SECTION_END_MARKER = "<!-- refix-log-end -->"
WORKFLOW_STATUS_MARKER_PATTERN = re.compile(r"<!-- refix-status:\s*(\w+)\s*-->")
LAST_REVIEWED_HEAD_MARKER_PATTERN = re.compile(
    r"<!-- refix-last-reviewed-head:\s*([0-9a-f]{4,40})\s*-->"
)
REFIX_LOG_SECTION_PATTERN = re.compile(
    re.escape(REFIX_LOG_SECTION_START_MARKER)
    + r"\n(?P<body>.*?)\n"
    + re.escape(REFIX_LOG_SECTION_END_MARKER),
    re.DOTALL,
)

_ENTRY_HEADING_PATTERN = re.compile(
    r"^###\s+(?P<reviewed_at>.+?)\s+—\s+`(?P<short_sha>[0-9a-f]{4,40})`\s*$",
    re.MULTILINE,
)
_ENTRY_HEAD_SHA_MARKER_PATTERN = re.compile(
    r"<!--\s+refix-entry-head-sha:\s*(?P<head_sha>[0-9a-f]{4,40})\s+-->"
)
_ENTRY_FIX_FAILED_TOKEN = "<!-- refix-entry-fix-failed -->"

DEFAULT_STATE_COMMENT_TIMEZONE = "JST"
STATE_TIMEZONE_ALIASES = {
    "JST": "Asia/Tokyo",
}


@dataclass(frozen=True)
class StateComment:
    """State comment 全体の正規化済みデータ。"""

    github_comment_id: int | None
    body: str
    refix_log: list[SelfReviewLogEntry] = field(default_factory=list)
    last_reviewed_head: str | None = None
    workflow_status: str = ""


def normalize_state_timezone_name(timezone_name: str) -> str:
    """Normalize a configured timezone name for state comment timestamps."""
    normalized = (timezone_name or DEFAULT_STATE_COMMENT_TIMEZONE).strip()
    if not normalized:
        normalized = DEFAULT_STATE_COMMENT_TIMEZONE
    return STATE_TIMEZONE_ALIASES.get(normalized.upper(), normalized)


def ensure_valid_state_timezone(timezone_name: str) -> str:
    """Validate and return a timezone name accepted by zoneinfo."""
    normalized = normalize_state_timezone_name(timezone_name)
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Invalid state comment timezone: {timezone_name}. "
            "Use a valid IANA timezone (e.g. Asia/Tokyo) or JST."
        ) from exc
    return normalized


def current_timestamp(timezone_name: str = DEFAULT_STATE_COMMENT_TIMEZONE) -> str:
    """Return the current timestamp in the state-comment format."""
    normalized = ensure_valid_state_timezone(timezone_name)
    return datetime.now(ZoneInfo(normalized)).strftime("%Y-%m-%d %H:%M:%S %Z")


def parse_last_reviewed_head(body: str) -> str | None:
    """Return the head SHA recorded in the last-reviewed-head marker, or None."""
    if not body:
        return None
    match = LAST_REVIEWED_HEAD_MARKER_PATTERN.search(body)
    if not match:
        return None
    return match.group(1)


def _severity_breakdown(findings: list[SelfReviewFinding]) -> dict[str, int]:
    breakdown = {"critical": 0, "major": 0, "minor": 0, "nitpick": 0}
    for finding in findings:
        if finding.severity in breakdown:
            breakdown[finding.severity] += 1
    return breakdown


def _render_one_finding(finding: SelfReviewFinding) -> list[str]:
    location = finding.path
    if finding.line is not None:
        location = f"{finding.path}:{finding.line}"
    lines = [f"- **[{finding.severity}]** `{location}` — {finding.title}"]
    body = finding.body.strip()
    if body:
        for body_line in body.splitlines():
            lines.append(f"  {body_line}")
    suggested = finding.suggested_fix.strip()
    if suggested:
        suggested_lines = suggested.splitlines()
        lines.append(f"  {t('state_comment.suggested_fix_label')} {suggested_lines[0]}")
        for extra in suggested_lines[1:]:
            lines.append(f"  {extra}")
    return lines


def _render_one_entry(entry: SelfReviewLogEntry) -> str:
    short_sha = entry.head_sha[:7] if entry.head_sha else "unknown"
    lines = [
        f"### {entry.reviewed_at} — `{short_sha}`",
        f"<!-- refix-entry-head-sha: {entry.head_sha} -->",
        "",
    ]
    if not entry.findings:
        lines.append(t("state_comment.no_findings"))
        return "\n".join(lines)

    breakdown = _severity_breakdown(entry.findings)
    lines.append(
        t(
            "state_comment.findings_label",
            total=len(entry.findings),
            critical=breakdown["critical"],
            major=breakdown["major"],
            minor=breakdown["minor"],
            nitpick=breakdown["nitpick"],
        )
    )
    summary = entry.summary.strip()
    if summary:
        lines.append("")
        for summary_line in summary.splitlines():
            lines.append(f"> {summary_line}")
    lines.append("")
    for finding in entry.findings:
        lines.extend(_render_one_finding(finding))
    if entry.commits:
        lines.extend(["", t("state_comment.applied_commits_label")])
        for commit in entry.commits:
            message = commit.message.strip() or "(no commit message)"
            lines.append(f"- `{commit.sha[:7]}` {message}")
    if entry.fix_failed:
        lines.extend(
            ["", _ENTRY_FIX_FAILED_TOKEN, t("state_comment.fix_failed_notice")]
        )
    return "\n".join(lines)


def render_refix_log_section(entries: list[SelfReviewLogEntry]) -> str:
    """Render the unified Refix Log section. Returns empty string if no entries."""
    if not entries:
        return ""
    rendered_entries = "\n\n".join(_render_one_entry(e) for e in entries)
    return "\n".join(
        [
            "<details open>",
            f"<summary>{t('state_comment.refix_log_summary')}</summary>",
            "",
            REFIX_LOG_SECTION_START_MARKER,
            "",
            rendered_entries,
            "",
            REFIX_LOG_SECTION_END_MARKER,
            "",
            "</details>",
        ]
    )


def _parse_one_finding_block(block: str) -> SelfReviewFinding | None:
    header_match = re.match(
        r"^-\s+\*\*\[(?P<severity>[^\]]+)\]\*\*\s+`(?P<location>[^`]+)`\s+—\s+(?P<title>.+)$",
        block.splitlines()[0],
    )
    if not header_match:
        return None
    severity = header_match.group("severity").strip()
    location = header_match.group("location").strip()
    title = header_match.group("title").strip()
    path = location
    line: int | None = None
    if ":" in location:
        path_part, _, line_part = location.rpartition(":")
        if line_part.isdigit():
            path = path_part
            line = int(line_part)
    rest_lines = block.splitlines()[1:]
    body_lines: list[str] = []
    suggested_lines: list[str] = []
    in_suggested = False
    suggested_marker = t("state_comment.suggested_fix_label")
    for raw_line in rest_lines:
        stripped = raw_line[2:] if raw_line.startswith("  ") else raw_line
        if not in_suggested and stripped.startswith(suggested_marker):
            tail = stripped[len(suggested_marker) :].lstrip()
            in_suggested = True
            if tail:
                suggested_lines.append(tail)
            continue
        if in_suggested:
            suggested_lines.append(stripped)
        else:
            body_lines.append(stripped)
    return SelfReviewFinding(
        finding_id="",
        severity=severity,
        path=path,
        line=line,
        title=title,
        body="\n".join(body_lines).strip(),
        suggested_fix="\n".join(suggested_lines).strip(),
    )


def _parse_one_entry(raw_entry: str) -> SelfReviewLogEntry | None:
    head_match = _ENTRY_HEADING_PATTERN.search(raw_entry)
    if not head_match:
        return None
    reviewed_at = head_match.group("reviewed_at").strip()
    full_sha_match = _ENTRY_HEAD_SHA_MARKER_PATTERN.search(raw_entry)
    head_sha = (
        full_sha_match.group("head_sha")
        if full_sha_match
        else head_match.group("short_sha")
    )

    findings: list[SelfReviewFinding] = []
    commits: list[LoggedCommit] = []
    summary = ""
    fix_failed = _ENTRY_FIX_FAILED_TOKEN in raw_entry

    if t("state_comment.no_findings") in raw_entry:
        return SelfReviewLogEntry(
            head_sha=head_sha,
            reviewed_at=reviewed_at,
            summary="",
            findings=[],
            commits=[],
            fix_failed=False,
        )

    summary_lines = [
        line[2:] for line in raw_entry.splitlines() if line.startswith("> ")
    ]
    if summary_lines:
        summary = "\n".join(summary_lines).strip()

    finding_blocks = re.findall(
        r"(^-\s+\*\*\[[^\]]+\]\*\*\s+`[^`]+`\s+—\s+.+(?:\n  .+)*)",
        raw_entry,
        flags=re.MULTILINE,
    )
    for block in finding_blocks:
        parsed = _parse_one_finding_block(block)
        if parsed is not None:
            findings.append(parsed)

    commits_label = t("state_comment.applied_commits_label")
    commits_section_match = re.search(
        re.escape(commits_label) + r"\n(?P<rows>(?:- `[0-9a-f]{4,40}` .*(?:\n|$))+)",
        raw_entry,
    )
    if commits_section_match:
        for row in commits_section_match.group("rows").splitlines():
            row_match = re.match(r"-\s+`(?P<sha>[0-9a-f]{4,40})`\s+(?P<msg>.*)$", row)
            if row_match:
                commits.append(
                    LoggedCommit(
                        sha=row_match.group("sha"),
                        message=row_match.group("msg").strip(),
                    )
                )

    return SelfReviewLogEntry(
        head_sha=head_sha,
        reviewed_at=reviewed_at,
        summary=summary,
        findings=findings,
        commits=commits,
        fix_failed=fix_failed,
    )


def parse_refix_log(body: str) -> list[SelfReviewLogEntry]:
    """Parse the unified Refix Log section back into structured entries (ascending)."""
    if not body:
        return []
    match = REFIX_LOG_SECTION_PATTERN.search(body)
    if not match:
        return []
    inner = match.group("body")
    raw_chunks = re.split(r"(?=^###\s)", inner, flags=re.MULTILINE)
    entries: list[SelfReviewLogEntry] = []
    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        parsed = _parse_one_entry(chunk)
        if parsed is not None:
            entries.append(parsed)
    return entries


def _build_state_comment_body(
    refix_log: list[SelfReviewLogEntry],
    workflow_status: str = "",
    last_reviewed_head: str | None = None,
) -> str:
    """Build the visible body portion of the state comment."""
    body_lines = [STATE_COMMENT_MARKER]
    if workflow_status:
        body_lines.append(f"<!-- refix-status: {workflow_status} -->")
    if last_reviewed_head:
        body_lines.append(f"<!-- refix-last-reviewed-head: {last_reviewed_head} -->")
    body_lines.extend(
        [
            STATE_COMMENT_TITLE,
            t("state_comment.description"),
        ]
    )
    rendered_log_section = render_refix_log_section(refix_log)
    if rendered_log_section:
        body_lines.extend(["", rendered_log_section])
    return "\n".join(body_lines)


def render_state_comment(
    refix_log: list[SelfReviewLogEntry],
    workflow_status: str = "",
    last_reviewed_head: str | None = None,
) -> str:
    """Render the full state comment, dropping oldest entries if necessary."""
    trimmed_entries = list(refix_log)
    while True:
        body = _build_state_comment_body(
            trimmed_entries,
            workflow_status,
            last_reviewed_head,
        )
        if len(body) <= STATE_COMMENT_MAX_LENGTH:
            return body
        if len(trimmed_entries) > 1:
            # 昇順前提なので先頭（最古）から削除
            trimmed_entries.pop(0)
            continue
        return body


def _local_state_path(repo: str, pr_number: int) -> Path:
    """ローカルステートファイルのパスを返す。repo は 'org/repo_name' 形式。"""
    parts = repo.split("/", 1)
    org = parts[0] if len(parts) == 2 else repo
    repo_name = parts[1] if len(parts) == 2 else repo
    return Path(_local_state_dir) / org / repo_name / f"{pr_number}.md"


def _state_from_body(body: str, github_comment_id: int | None = None) -> StateComment:
    """生 body 文字列から StateComment dataclass を構築する。"""
    refix_log = parse_refix_log(body)
    last_reviewed_head = parse_last_reviewed_head(body)
    status_match = WORKFLOW_STATUS_MARKER_PATTERN.search(body or "")
    workflow_status = status_match.group(1) if status_match else ""
    return StateComment(
        github_comment_id=github_comment_id,
        body=body,
        refix_log=refix_log,
        last_reviewed_head=last_reviewed_head,
        workflow_status=workflow_status,
    )


def _load_state_from_file(repo: str, pr_number: int) -> StateComment:
    """ローカルファイルからステートを読み込む。ファイルが存在しなければ空を返す。"""
    path = _local_state_path(repo, pr_number)
    if not path.exists():
        return StateComment(github_comment_id=None, body="")
    body = path.read_text(encoding="utf-8")
    return _state_from_body(body, github_comment_id=None)


def _save_state_to_file(repo: str, pr_number: int, body: str) -> None:
    """ローカルファイルにステートを書き込む。"""
    path = _local_state_path(repo, pr_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _get_authenticated_github_user() -> str | None:
    """Return the login of the currently authenticated GitHub user, or None on failure."""
    result = run_command(
        ["gh", "api", "user", "--jq", ".login"],
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        username = result.stdout.strip()
        if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}(\[bot\])?$", username):
            return username
    return None


def load_state_comment(repo: str, pr_number: int) -> StateComment:
    """Load the current state comment for a PR."""
    if _use_local_state:
        return _load_state_from_file(repo, pr_number)
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate",
        "--slurp",
    ]
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch PR comments for {repo}#{pr_number}: {(result.stderr or '').strip()}"
        )

    try:
        raw_data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse PR comments for {repo}#{pr_number}"
        ) from exc

    pages = raw_data if isinstance(raw_data, list) else []
    comments: list[dict] = []
    for item in pages:
        if isinstance(item, list):
            comments.extend(comment for comment in item if isinstance(comment, dict))
        elif isinstance(item, dict):
            comments.append(item)

    github_username = _get_authenticated_github_user()
    if github_username is None:
        raise RuntimeError(
            f"Failed to determine authenticated GitHub user; cannot safely load state comment for {repo}#{pr_number}"
        )
    matching_comments = [
        comment
        for comment in comments
        if STATE_COMMENT_MARKER in str(comment.get("body") or "")
        and comment.get("user", {}).get("login") == github_username
    ]
    if not matching_comments:
        return StateComment(github_comment_id=None, body="")

    # 複数コメントがある場合、最新以外を削除（レースコンディション対応）
    if len(matching_comments) > 1:
        for comment in matching_comments[:-1]:
            comment_id = comment.get("id")
            if comment_id:
                run_command(
                    [
                        "gh",
                        "api",
                        f"repos/{repo}/issues/comments/{comment_id}",
                        "-X",
                        "DELETE",
                    ],
                    check=False,
                )

    latest_comment = matching_comments[-1]
    latest_body = str(latest_comment.get("body") or "")
    return _state_from_body(latest_body, github_comment_id=latest_comment.get("id"))


def upsert_state_comment(
    repo: str,
    pr_number: int,
    *,
    refix_log: list[SelfReviewLogEntry] | None = None,
    workflow_status: str | None = None,
    last_reviewed_head: str | None = None,
    _preloaded_state: StateComment | None = None,
) -> None:
    """Create or update the state comment for a PR.

    None を渡したフィールドは既存値を維持。明示的に空にしたい場合は空リスト / 空文字列を渡す。
    """
    state = (
        _preloaded_state
        if _preloaded_state is not None
        else load_state_comment(repo, pr_number)
    )
    next_refix_log = list(state.refix_log) if refix_log is None else list(refix_log)
    next_workflow_status = (
        state.workflow_status if workflow_status is None else workflow_status
    )
    next_last_reviewed_head = (
        state.last_reviewed_head if last_reviewed_head is None else last_reviewed_head
    )

    if not next_refix_log and not next_workflow_status and not next_last_reviewed_head:
        return

    body = render_state_comment(
        next_refix_log,
        workflow_status=next_workflow_status,
        last_reviewed_head=next_last_reviewed_head,
    )
    if _use_local_state:
        _save_state_to_file(repo, pr_number, body)
        return
    if state.github_comment_id is None:
        # 作成前に再確認: 別の呼び出しで既に作成済みかもしれない (stale state 対策)
        fresh = load_state_comment(repo, pr_number)
        if fresh.github_comment_id is not None:
            # 既存コメントが見つかった → PATCH に切り替え
            body = render_state_comment(
                next_refix_log or fresh.refix_log,
                workflow_status=next_workflow_status or fresh.workflow_status,
                last_reviewed_head=next_last_reviewed_head or fresh.last_reviewed_head,
            )
            cmd = [
                "gh",
                "api",
                f"repos/{repo}/issues/comments/{fresh.github_comment_id}",
                "-X",
                "PATCH",
                "-f",
                f"body={body}",
            ]
        else:
            cmd = [
                "gh",
                "pr",
                "comment",
                str(pr_number),
                "--repo",
                repo,
                "--body",
                body,
            ]
    else:
        cmd = [
            "gh",
            "api",
            f"repos/{repo}/issues/comments/{state.github_comment_id}",
            "-X",
            "PATCH",
            "-f",
            f"body={body}",
        ]

    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to upsert state comment for {repo}#{pr_number}: {(result.stderr or '').strip()}"
        )


def append_refix_log_entry(
    repo: str,
    pr_number: int,
    entry: SelfReviewLogEntry,
    *,
    update_last_reviewed_head: bool = True,
    _preloaded_state: StateComment | None = None,
) -> None:
    """既存の Refix Log の末尾に新しいエントリを追加する（昇順）。"""
    state = (
        _preloaded_state
        if _preloaded_state is not None
        else load_state_comment(repo, pr_number)
    )
    new_log = [*state.refix_log, entry]
    last_reviewed_head = (
        entry.head_sha if update_last_reviewed_head else state.last_reviewed_head
    )
    upsert_state_comment(
        repo,
        pr_number,
        refix_log=new_log,
        last_reviewed_head=last_reviewed_head,
        _preloaded_state=state,
    )


def update_workflow_status(
    repo: str,
    pr_number: int,
    status: str,
    *,
    _preloaded_state: StateComment | None = None,
) -> None:
    """ステータスのみをコメントに書き込む軽量関数。

    `_preloaded_state` は呼び出し元のキャッシュ用。stale な可能性を考慮して
    実際の書き込み前に最新版を必ず再 fetch し、書き戻し時にレース由来の
    エントリ消失を防ぐ。
    """
    cached = _preloaded_state
    if cached is not None and cached.workflow_status == status:
        return
    fresh = load_state_comment(repo, pr_number)
    if fresh.workflow_status == status:
        return
    upsert_state_comment(
        repo,
        pr_number,
        workflow_status=status,
        _preloaded_state=fresh,
    )
