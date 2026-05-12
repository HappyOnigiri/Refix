"""共有型定義モジュール。複数ファイルで使用する TypedDict を定義する。"""

from dataclasses import dataclass, field
from typing import Any, TypedDict

# AppConfig は将来的に TypedDict 化するための型エイリアス。
# 30 以上のキーを持つ複雑な型のため、段階的移行を見据えてエイリアスとして定義する。
AppConfig = dict[str, Any]  # dict-any: ok


class UserInfo(TypedDict, total=False):
    """GitHub ユーザー情報。"""

    login: str
    name: str
    email: str


class LabelInfo(TypedDict, total=False):
    """GitHub ラベル情報。"""

    id: int
    name: str
    color: str


class CommitInfo(TypedDict, total=False):
    """コミット情報（gh pr view --json commits）。"""

    oid: str
    messageHeadline: str
    committedDate: str


class _RepositoryEntryBase(TypedDict):
    repo: str


class RepositoryEntry(_RepositoryEntryBase, total=False):
    """リポジトリ設定エントリ（.refix-batch.yaml の repositories[] 要素）。"""

    user_name: str | None
    user_email: str | None
    setup: dict | None
    models: dict
    auto_merge: bool
    enabled_pr_labels: list
    process_draft_prs: bool
    include_fork_repositories: bool
    language: str
    state_comment_timezone: str
    review_min_severity: str
    merge_method: str
    base_update_method: str
    max_modified_prs_per_run: int
    max_committed_prs_per_run: int
    max_claude_prs_per_run: int
    ci_empty_as_success: bool
    ci_empty_grace_minutes: int
    exclude_authors: list
    exclude_labels: list
    target_authors: list
    auto_merge_authors: list
    use_pr_labels: bool
    python_version: str | None
    node_version: str | None


class CheckRunData(TypedDict, total=False):
    """REST API の生 check run データ（_filter_check_runs の入出力）。"""

    name: str
    conclusion: str | None
    status: str
    details_url: str
    html_url: str
    id: int


class CheckStatus(TypedDict, total=False):
    """正規化済み CI チェックステータス（PRData.check_runs の要素）。"""

    name: str
    conclusion: str
    state: str
    detailsUrl: str
    targetUrl: str
    context: str
    workflowName: str


class PRData(TypedDict, total=False):
    """PR データ（fetch_open_prs / fetch_pr_details の戻り値）。

    REST API と GraphQL の両方のレスポンス形式を統合した型。
    """

    number: int
    title: str
    author: UserInfo
    createdAt: str
    updatedAt: str
    labels: list[LabelInfo]
    isDraft: bool
    state: str  # "OPEN", "MERGED", "CLOSED"
    check_runs: list[CheckStatus]
    body: str
    headRefName: str
    baseRefName: str
    headRefOid: str
    commits: list[CommitInfo]
    mergedAt: str


@dataclass(frozen=True)
class SelfReviewFinding:
    """Self-review が生成した個別指摘。"""

    finding_id: str
    severity: str  # "critical" | "major" | "minor" | "nitpick"
    path: str
    line: int | None
    title: str
    body: str  # 問題の説明
    fix_approach: str  # 修正方針（影響範囲は fix 側が能動的に決定する）


@dataclass(frozen=True)
class SelfReviewResult:
    """Self-review セッションのパース済み結果。"""

    head_sha: str
    reviewed_at: str  # ISO8601 with state_comment_timezone
    summary: str
    findings: list[SelfReviewFinding]
    raw_xml: str


@dataclass(frozen=True)
class LoggedCommit:
    """ログに記録するコミットのメタ情報。"""

    sha: str
    message: str  # コミットメッセージの subject 行


@dataclass(frozen=True)
class SelfReviewLogEntry:
    """State Comment に保存する Refix セッション 1 件。"""

    head_sha: str
    reviewed_at: str
    summary: str = ""
    findings: list[SelfReviewFinding] = field(default_factory=list)
    commits: list[LoggedCommit] = field(default_factory=list)
    fix_failed: bool = False
