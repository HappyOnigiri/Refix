#!/usr/bin/env python3
"""State comment management for Refix self-review log."""

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
from type_defs import SelfReviewLogEntry

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
RESULT_LOG_SECTION_START_MARKER = "<!-- refix-result-log-start -->"
RESULT_LOG_SECTION_END_MARKER = "<!-- refix-result-log-end -->"
SELF_REVIEW_LOG_SECTION_START_MARKER = "<!-- refix-self-review-log-start -->"
SELF_REVIEW_LOG_SECTION_END_MARKER = "<!-- refix-self-review-log-end -->"
WORKFLOW_STATUS_MARKER_PATTERN = re.compile(r"<!-- refix-status:\s*(\w+)\s*-->")
LAST_REVIEWED_HEAD_MARKER_PATTERN = re.compile(
    r"<!-- refix-last-reviewed-head:\s*([0-9a-f]{4,40})\s*-->"
)
# Use [^<]+ to match the summary text regardless of language (EN or JA).
RESULT_LOG_SECTION_PATTERN = re.compile(
    re.escape(RESULT_LOG_SECTION_START_MARKER)
    + r"\n<details>\n<summary>[^<]+</summary>\n\n(?P<body>.*?)\n</details>\n"
    + re.escape(RESULT_LOG_SECTION_END_MARKER),
    re.DOTALL,
)
SELF_REVIEW_LOG_SECTION_PATTERN = re.compile(
    re.escape(SELF_REVIEW_LOG_SECTION_START_MARKER)
    + r"\n<details>\n<summary>[^<]+</summary>\n\n(?P<body>.*?)\n</details>\n"
    + re.escape(SELF_REVIEW_LOG_SECTION_END_MARKER),
    re.DOTALL,
)

_SELF_REVIEW_ENTRY_HEAD_PATTERN = re.compile(
    r"^####\s+(?P<reviewed_at>[^—]+?)\s+—\s+head\s+`(?P<short_sha>[0-9a-f]{4,40})`\s*$",
    re.MULTILINE,
)
_SELF_REVIEW_ENTRY_HEAD_SHA_PATTERN = re.compile(
    r"<!--\s+refix-entry-head-sha:\s*(?P<head_sha>[0-9a-f]{4,40})\s+-->"
)
_SELF_REVIEW_BREAKDOWN_PATTERN = re.compile(
    r"-\s+(?:Findings|指摘件数):\s*(?P<total>\d+)\s+(?:\(|件 \()"
    r"critical:\s*(?P<critical>\d+),\s*"
    r"major:\s*(?P<major>\d+),\s*"
    r"minor:\s*(?P<minor>\d+),\s*"
    r"nitpick:\s*(?P<nitpick>\d+)\)"
)

DEFAULT_STATE_COMMENT_TIMEZONE = "JST"
STATE_TIMEZONE_ALIASES = {
    "JST": "Asia/Tokyo",
}


@dataclass(frozen=True)
class StateComment:
    """State comment 全体の正規化済みデータ。"""

    github_comment_id: int | None
    body: str
    self_review_log: list[SelfReviewLogEntry] = field(default_factory=list)
    last_reviewed_head: str | None = None
    workflow_status: str = ""
    result_log_body: str = ""


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


def strip_result_log_section(text: str) -> str:
    """Remove the rendered result log block from a state comment body."""
    return RESULT_LOG_SECTION_PATTERN.sub("", text or "")


def extract_result_log_body(text: str) -> str:
    """Extract the markdown body stored in the result log block."""
    match = RESULT_LOG_SECTION_PATTERN.search(text or "")
    if not match:
        return ""
    return match.group("body").strip()


def parse_last_reviewed_head(body: str) -> str | None:
    """Return the head SHA recorded in the last-reviewed-head marker, or None."""
    if not body:
        return None
    match = LAST_REVIEWED_HEAD_MARKER_PATTERN.search(body)
    if not match:
        return None
    return match.group(1)


def _parse_one_self_review_entry(
    raw_entry: str,
) -> SelfReviewLogEntry | None:
    """Parse a single rendered self-review entry block back into a dataclass."""
    head_match = _SELF_REVIEW_ENTRY_HEAD_PATTERN.search(raw_entry)
    if not head_match:
        return None
    reviewed_at = head_match.group("reviewed_at").strip()

    full_sha_match = _SELF_REVIEW_ENTRY_HEAD_SHA_PATTERN.search(raw_entry)
    head_sha = (
        full_sha_match.group("head_sha")
        if full_sha_match
        else head_match.group("short_sha")
    )

    breakdown = {"critical": 0, "major": 0, "minor": 0, "nitpick": 0}
    breakdown_match = _SELF_REVIEW_BREAKDOWN_PATTERN.search(raw_entry)
    finding_count = 0
    if breakdown_match:
        finding_count = int(breakdown_match.group("total"))
        breakdown = {
            "critical": int(breakdown_match.group("critical")),
            "major": int(breakdown_match.group("major")),
            "minor": int(breakdown_match.group("minor")),
            "nitpick": int(breakdown_match.group("nitpick")),
        }

    commit_shas: list[str] = []
    for commit_match in re.finditer(
        r"^\s*-\s+`([0-9a-f]{4,40})`", raw_entry, flags=re.MULTILINE
    ):
        commit_shas.append(commit_match.group(1))

    raw_xml: str | None = None
    xml_block = re.search(
        r"```xml\n(?P<xml>.*?)\n```",
        raw_entry,
        flags=re.DOTALL,
    )
    if xml_block:
        raw_xml = xml_block.group("xml")

    return SelfReviewLogEntry(
        head_sha=head_sha,
        reviewed_at=reviewed_at,
        finding_count=finding_count,
        severity_breakdown=breakdown,
        commit_shas=commit_shas,
        raw_xml=raw_xml,
    )


def parse_self_review_log(body: str) -> list[SelfReviewLogEntry]:
    """Parse the self-review log section back into structured entries."""
    if not body:
        return []
    match = SELF_REVIEW_LOG_SECTION_PATTERN.search(body)
    if not match:
        return []
    inner = match.group("body")
    # Split on entry headings; preserve the heading line on each chunk.
    raw_chunks = re.split(r"(?=^####\s)", inner, flags=re.MULTILINE)
    entries: list[SelfReviewLogEntry] = []
    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        parsed = _parse_one_self_review_entry(chunk)
        if parsed is not None:
            entries.append(parsed)
    return entries


def _render_one_self_review_entry(entry: SelfReviewLogEntry) -> str:
    """Render a single self-review log entry."""
    short_sha = entry.head_sha[:7] if entry.head_sha else "unknown"
    lines = [
        f"#### {entry.reviewed_at} — head `{short_sha}`",
        f"<!-- refix-entry-head-sha: {entry.head_sha} -->",
    ]
    if entry.raw_xml is None or entry.finding_count == 0:
        lines.append(f"- {t('state_comment.no_findings')}")
    else:
        breakdown = entry.severity_breakdown
        lines.append(
            "- "
            + t(
                "state_comment.findings_breakdown",
                total=entry.finding_count,
                critical=breakdown.get("critical", 0),
                major=breakdown.get("major", 0),
                minor=breakdown.get("minor", 0),
                nitpick=breakdown.get("nitpick", 0),
            )
        )
        if entry.commit_shas:
            for sha in entry.commit_shas:
                lines.append(f"  - `{sha}`")
        if entry.raw_xml:
            lines.extend(
                [
                    "",
                    "<details>",
                    f"<summary>{t('state_comment.review_details_summary')}</summary>",
                    "",
                    "```xml",
                    entry.raw_xml,
                    "```",
                    "",
                    "</details>",
                ]
            )
    return "\n".join(lines)


def render_self_review_log_section(entries: list[SelfReviewLogEntry]) -> str:
    """Render the Self-Review Log section. Returns empty string if no entries."""
    if not entries:
        return ""
    rendered_entries = "\n\n".join(_render_one_self_review_entry(e) for e in entries)
    return "\n".join(
        [
            SELF_REVIEW_LOG_SECTION_START_MARKER,
            "<details>",
            f"<summary>{t('state_comment.self_review_log_summary')}</summary>",
            "",
            rendered_entries,
            "",
            "</details>",
            SELF_REVIEW_LOG_SECTION_END_MARKER,
        ]
    )


def _build_state_comment_body(
    self_review_log: list[SelfReviewLogEntry],
    result_log_body: str,
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
    rendered_log_section = render_self_review_log_section(self_review_log)
    if rendered_log_section:
        body_lines.extend(["", rendered_log_section])

    normalized_log_body = (result_log_body or "").strip()
    if normalized_log_body:
        body_lines.extend(
            [
                "",
                RESULT_LOG_SECTION_START_MARKER,
                "<details>",
                f"<summary>{t('state_comment.result_log_summary')}</summary>",
                "",
                normalized_log_body,
                "",
                "</details>",
                RESULT_LOG_SECTION_END_MARKER,
            ]
        )
    return "\n".join(body_lines)


def _truncate_result_log_body_to_fit(
    self_review_log: list[SelfReviewLogEntry],
    result_log_body: str,
    max_length: int,
    workflow_status: str = "",
    last_reviewed_head: str | None = None,
) -> str:
    """Truncate the result log block so the state comment can still fit."""
    normalized_log_body = (result_log_body or "").strip()
    if not normalized_log_body:
        return ""

    truncation_notice = t("state_comment.truncation_notice")
    log_scaffold_length = (
        len(
            _build_state_comment_body(
                self_review_log, "x", workflow_status, last_reviewed_head
            )
        )
        - 1
    )
    available_log_length = max_length - log_scaffold_length
    if available_log_length <= 0:
        return ""
    if len(normalized_log_body) <= available_log_length:
        return normalized_log_body
    if available_log_length <= len(truncation_notice):
        return ""

    phases = re.split(r"\n\n(?=#### )", normalized_log_body)
    while phases:
        candidate = "\n\n".join(phases) + truncation_notice
        if len(candidate) <= available_log_length:
            return candidate
        phases.pop()
    return ""


def render_state_comment(
    self_review_log: list[SelfReviewLogEntry],
    result_log_body: str = "",
    workflow_status: str = "",
    last_reviewed_head: str | None = None,
) -> str:
    """Render the full state comment, trimming oldest entries if necessary."""
    trimmed_entries = list(self_review_log)
    truncated_log_body = (result_log_body or "").strip()
    while True:
        body = _build_state_comment_body(
            trimmed_entries,
            truncated_log_body,
            workflow_status,
            last_reviewed_head,
        )
        if len(body) <= STATE_COMMENT_MAX_LENGTH:
            return body
        if len(trimmed_entries) > 1:
            # 末尾は新しい順前提なので、末尾（最古）から削除
            trimmed_entries.pop()
            continue
        if truncated_log_body:
            shortened_log_body = _truncate_result_log_body_to_fit(
                trimmed_entries,
                truncated_log_body,
                STATE_COMMENT_MAX_LENGTH,
                workflow_status,
                last_reviewed_head,
            )
            if shortened_log_body != truncated_log_body:
                truncated_log_body = shortened_log_body
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
    self_review_log = parse_self_review_log(body)
    last_reviewed_head = parse_last_reviewed_head(body)
    status_match = WORKFLOW_STATUS_MARKER_PATTERN.search(body or "")
    workflow_status = status_match.group(1) if status_match else ""
    return StateComment(
        github_comment_id=github_comment_id,
        body=body,
        self_review_log=self_review_log,
        last_reviewed_head=last_reviewed_head,
        workflow_status=workflow_status,
        result_log_body=extract_result_log_body(body),
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
    self_review_log: list[SelfReviewLogEntry] | None = None,
    result_log_body: str | None = None,
    workflow_status: str | None = None,
    last_reviewed_head: str | None = None,
    _preloaded_state: StateComment | None = None,
) -> None:
    """Create or update the state comment for a PR.

    None を渡したフィールドは既存値を維持。明示的に空にしたい場合は空文字列 / 空リストを渡す。
    """
    state = (
        _preloaded_state
        if _preloaded_state is not None
        else load_state_comment(repo, pr_number)
    )
    next_self_review_log = (
        list(state.self_review_log)
        if self_review_log is None
        else list(self_review_log)
    )
    next_result_log_body = (
        state.result_log_body if result_log_body is None else result_log_body.strip()
    )
    next_workflow_status = (
        state.workflow_status if workflow_status is None else workflow_status
    )
    next_last_reviewed_head = (
        state.last_reviewed_head if last_reviewed_head is None else last_reviewed_head
    )

    if (
        not next_self_review_log
        and not next_result_log_body
        and not next_workflow_status
        and not next_last_reviewed_head
    ):
        return

    body = render_state_comment(
        next_self_review_log,
        result_log_body=next_result_log_body,
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
                next_self_review_log or fresh.self_review_log,
                result_log_body=next_result_log_body or fresh.result_log_body,
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


def append_self_review_entry(
    repo: str,
    pr_number: int,
    entry: SelfReviewLogEntry,
    *,
    update_last_reviewed_head: bool = True,
    _preloaded_state: StateComment | None = None,
) -> None:
    """既存の Self-Review Log の先頭に新しいエントリを追加する。"""
    state = (
        _preloaded_state
        if _preloaded_state is not None
        else load_state_comment(repo, pr_number)
    )
    new_log = [entry, *state.self_review_log]
    last_reviewed_head = (
        entry.head_sha if update_last_reviewed_head else state.last_reviewed_head
    )
    upsert_state_comment(
        repo,
        pr_number,
        self_review_log=new_log,
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
    """ステータスのみをコメントに書き込む軽量関数。"""
    state = (
        _preloaded_state
        if _preloaded_state is not None
        else load_state_comment(repo, pr_number)
    )
    if state.workflow_status == status:
        return
    upsert_state_comment(
        repo,
        pr_number,
        workflow_status=status,
        _preloaded_state=state,
    )
