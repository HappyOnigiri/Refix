#!/usr/bin/env python3
"""Refix - Claude セルフレビュー → 自動修正ツール。

このモジュールはオーケストレーション層として、以下のサブモジュールを呼び出して PR の処理フローを制御する:

- config: 設定ファイルの読み込みと検証
- pr_label: PR ラベルの管理
- prompt_builder: Claude へのプロンプト生成 / セルフレビュー XML パース
- claude_runner: Claude CLI の実行
- git_ops: Git リポジトリの操作
- state_manager: PR 上の State Comment 管理
"""

import argparse
import dataclasses
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

from __version__ import __version__
from claude_limit import ClaudeCommandFailedError
from claude_runner import run_claude_prompt
from config import (
    DEFAULT_CONFIG,
    expand_repositories,
    get_enabled_pr_label_keys,
    get_incremental_review,
    get_process_draft_prs,
    get_use_pr_labels,
    load_config,
    load_single_config,
    merge_repo_config,
)
from constants import SEPARATOR_LEN
from error_collector import ErrorCollector
from errors import ConfigError
from git_ops import (
    abort_rebase,
    continue_rebase,
    get_branch_compare_status,
    has_merge_conflicts,
    is_rebase_in_progress,
    merge_base_branch,
    needs_base_merge,
    prepare_repository,
    rebase_base_branch,
)
from github_pr_fetcher import fetch_open_prs, fetch_single_pr
from i18n import set_language
from ci_log import log_endgroup, log_error, log_group
from pr_label import (
    REFIX_DONE_LABEL,
    REFIX_RUNNING_LABEL,
    backfill_merged_labels,
    edit_pr_label,
    resolve_workflow_status,
    set_pr_running_label,
    update_done_label_if_completed,
)
from pr_reviewer import fetch_pr_details
from prompt_builder import (
    build_conflict_resolution_prompt,
    build_fix_prompt,
    build_self_review_prompt,
    filter_findings_by_severity,
    parse_self_review_xml,
)
from state_manager import (
    StateComment,
    append_refix_log_entry,
    configure_local_state,
    current_timestamp,
    load_state_comment,
    update_workflow_status,
)
from subprocess_helpers import SubprocessError
from subprocess_helpers import run_git as _run_git
from type_defs import (
    AppConfig,
    LabelInfo,
    LoggedCommit,
    PRData,
    RepositoryEntry,
    SelfReviewLogEntry,
    SelfReviewResult,
)


_SELF_REVIEW_FILENAME = "_self_review.xml"
_PROMPT_FILENAME = "_review_prompt.md"


def _ensure_refix_artifacts_excluded(works_dir: Any) -> None:
    """Refix が works_dir に書き込む一時ファイルを .git/info/exclude に追加。

    これらが untracked のまま残ると、fix セッション後の dirty-check に引っかかり
    state 更新と push がスキップされてしまうため、git status から見えなくする。
    """
    exclude_file = Path(works_dir) / ".git" / "info" / "exclude"
    entries = [_SELF_REVIEW_FILENAME, _PROMPT_FILENAME]
    existing_lines: list[str] = []
    if exclude_file.exists():
        existing_lines = exclude_file.read_text(encoding="utf-8").splitlines()
    new_lines = [e for e in entries if e not in existing_lines]
    if not new_lines:
        return
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    existing_text = (
        exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
    )
    needs_leading_newline = bool(existing_text) and not existing_text.endswith("\n")
    with open(exclude_file, "a", encoding="utf-8") as f:
        if needs_leading_newline:
            f.write("\n")
        for entry in new_lines:
            f.write(f"{entry}\n")


@dataclass
class PRContext:
    """PR 処理に必要な設定・情報をまとめるデータクラス。"""

    repo: str
    pr_number: int
    title: str
    is_draft: bool
    branch_name: str
    base_branch: str
    works_dir: Any  # Path
    labels: list[LabelInfo]
    dry_run: bool
    silent: bool
    review_model: str
    fix_model: str
    review_min_severity: str
    auto_merge_enabled: bool
    enabled_pr_label_keys: set[str]
    process_draft_prs: bool
    state_comment_timezone: str
    language: str
    max_modified_prs_per_run: int
    max_committed_prs_per_run: int
    max_claude_prs_per_run: int
    modified_prs: set
    committed_prs: set
    claude_prs: set
    ci_empty_as_success: bool | None
    ci_empty_grace_minutes: int
    ci_pending_wait_seconds: int
    merge_method: str
    base_update_method: str
    needs_force_push: bool = False
    use_pr_labels: bool = True
    incremental_review: bool = True


def _pr_ref(repo: str, pr_number: int) -> str:
    """ログ向けの PR 識別子を返す。"""
    return f"{repo} PR #{pr_number}"


def _mark_pr_data_as_running(pr_data: PRData) -> None:
    """pr_data のラベルスナップショットを running 状態に更新する。"""
    labels = [
        lbl
        for lbl in (pr_data.get("labels") or [])
        if not (isinstance(lbl, dict) and lbl.get("name") == REFIX_DONE_LABEL)
    ]
    if not any(
        isinstance(lbl, dict) and lbl.get("name") == REFIX_RUNNING_LABEL
        for lbl in labels
    ):
        labels.append({"name": REFIX_RUNNING_LABEL})
    pr_data["labels"] = labels


def _push_if_needed(
    ctx: PRContext,
    works_dir: Any,
    branch_name: str,
    *,
    check: bool = True,
) -> "subprocess.CompletedProcess[str] | None":
    """未push コミットがあれば fetch + rebase + push を 1 回だけ行う。"""
    unpushed = _run_git(
        "log",
        "--oneline",
        f"origin/{branch_name}..HEAD",
        cwd=works_dir,
        check=False,
        timeout=10,
    )
    if unpushed.returncode != 0:
        raise RuntimeError(
            f"Failed to check unpushed commits for {branch_name}. "
            f"details: {unpushed.stderr.strip()}"
        )
    if not unpushed.stdout.strip():
        return None

    if ctx.base_update_method != "merge" and not ctx.needs_force_push:
        _run_git("fetch", "origin", branch_name, cwd=works_dir, timeout=120)
        rebase_result = _run_git(
            "rebase",
            f"origin/{branch_name}",
            cwd=works_dir,
            check=False,
            timeout=120,
        )
        if rebase_result.returncode != 0:
            _run_git("rebase", "--abort", cwd=works_dir, check=False, timeout=30)
            raise RuntimeError(
                f"Pre-push rebase failed due to conflicts with origin/{branch_name}. "
                f"details: {rebase_result.stderr.strip()}"
            )

    args = ["push"]
    if ctx.needs_force_push:
        args.append("--force-with-lease")
    args.extend(["origin", branch_name])
    return _run_git(*args, cwd=works_dir, check=check, timeout=120)


def _run_self_review_phase(
    ctx: PRContext,
    pr_data: PRData,
    works_dir: Any,
    state_comment: StateComment,
    extra_env: dict[str, str] | None = None,
) -> SelfReviewResult | None:
    """セルフレビューフェーズを実行する。

    dry_run の場合は None を返す。
    Claude がコミットを作成した（=指示違反）場合は RuntimeError。
    """
    repo = ctx.repo
    pr_number = ctx.pr_number
    head_sha = pr_data.get("headRefOid") or ""

    if ctx.dry_run:
        print("\n[DRY RUN] Would execute Claude self-review phase.")
        print(f"  cwd: {works_dir}")
        print(f"  model: {ctx.review_model}")
        return None

    output_path = str(Path(works_dir) / _SELF_REVIEW_FILENAME)
    try:
        Path(output_path).unlink()
    except FileNotFoundError:
        pass
    _ensure_refix_artifacts_excluded(works_dir)

    previously_applied_fixes: list[LoggedCommit] = [
        commit for entry in (state_comment.refix_log or []) for commit in entry.commits
    ]

    diff_range = f"origin/{ctx.base_branch}...HEAD"
    review_files: list[str] = []
    use_incremental = bool(ctx.incremental_review and state_comment.last_reviewed_head)

    if use_incremental:
        candidate: str = state_comment.last_reviewed_head  # type: ignore[assignment]
        ancestor_check = _run_git(
            "merge-base",
            "--is-ancestor",
            candidate,
            "HEAD",
            cwd=works_dir,
            check=False,
            timeout=10,
        )
        if ancestor_check.returncode != 0:
            print(
                f"[self-review] {_pr_ref(repo, pr_number)}: last_reviewed_head "
                f"{candidate[:7]} not ancestor of HEAD; falling back to full review"
            )
            use_incremental = False

    if use_incremental:
        candidate = state_comment.last_reviewed_head  # type: ignore[assignment]
        diff_range = f"{candidate}..HEAD"
        incr_files_result = _run_git(
            "diff",
            "--name-only",
            f"{candidate}..HEAD",
            cwd=works_dir,
            check=False,
            timeout=30,
        )
        pr_files_result = _run_git(
            "diff",
            "--name-only",
            f"origin/{ctx.base_branch}...HEAD",
            cwd=works_dir,
            check=False,
            timeout=30,
        )
        if incr_files_result.returncode != 0 or pr_files_result.returncode != 0:
            print(
                f"[self-review] {_pr_ref(repo, pr_number)}: failed to compute incremental "
                "file scope; falling back to full review"
            )
            use_incremental = False
            diff_range = f"origin/{ctx.base_branch}...HEAD"

    if use_incremental:
        incr_set = {
            ln.strip() for ln in incr_files_result.stdout.splitlines() if ln.strip()
        }  # type: ignore[union-attr]
        pr_set = [
            ln.strip() for ln in pr_files_result.stdout.splitlines() if ln.strip()
        ]  # type: ignore[union-attr]
        review_files = [p for p in pr_set if p in incr_set]
        if not review_files:
            print(
                f"[self-review] {_pr_ref(repo, pr_number)}: empty incremental scope; "
                "skipping Claude and recording no-findings entry"
            )
            head_sha_now = _run_git(
                "rev-parse", "HEAD", cwd=works_dir, check=False, timeout=10
            )
            new_head = (
                head_sha_now.stdout.strip()
                if head_sha_now.returncode == 0
                else str(head_sha)
            )
            return SelfReviewResult(
                head_sha=new_head,
                reviewed_at=current_timestamp(ctx.state_comment_timezone),
                summary="No incremental changes in PR scope.",
                findings=[],
                raw_xml="",
            )
    else:
        pr_files_result = _run_git(
            "diff",
            "--name-only",
            f"origin/{ctx.base_branch}...HEAD",
            cwd=works_dir,
            check=False,
            timeout=30,
        )
        if pr_files_result.returncode == 0:
            review_files = [
                ln.strip() for ln in pr_files_result.stdout.splitlines() if ln.strip()
            ]
        if not review_files:
            print(
                f"[self-review] {_pr_ref(repo, pr_number)}: PR has no changed files; "
                "skipping Claude and recording no-findings entry"
            )
            return SelfReviewResult(
                head_sha=str(head_sha),
                reviewed_at=current_timestamp(ctx.state_comment_timezone),
                summary="No changed files in PR.",
                findings=[],
                raw_xml="",
            )

    prompt = build_self_review_prompt(
        pr_number=pr_number,
        pr_title=ctx.title,
        pr_body=str(pr_data.get("body") or ""),
        base_branch=ctx.base_branch,
        head_sha=str(head_sha),
        diff_range=diff_range,
        review_files=review_files,
        output_path=output_path,
        language=ctx.language,
        previously_applied_fixes=previously_applied_fixes,
    )

    print(
        f"[self-review] {_pr_ref(repo, pr_number)}: running Claude self-review "
        f"(model={ctx.review_model})"
    )
    try:
        (commits, _stdout) = run_claude_prompt(
            works_dir=works_dir,
            prompt=prompt,
            model=ctx.review_model,
            silent=ctx.silent,
            phase_label="self-review",
            extra_env=extra_env,
        )
    except Exception as e:
        print(
            f"[self-review:error] {_pr_ref(repo, pr_number)}: Claude self-review failed",
            file=sys.stderr,
        )
        print(f"  details: {e}", file=sys.stderr)
        raise

    if commits:
        raise RuntimeError(
            f"Self-review phase produced unexpected commits for "
            f"{_pr_ref(repo, pr_number)}; review session must not commit."
        )

    try:
        xml_text = Path(output_path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Self-review file not found at {output_path} for "
            f"{_pr_ref(repo, pr_number)}; review session did not produce output."
        ) from exc

    parsed = parse_self_review_xml(xml_text)
    filtered = filter_findings_by_severity(parsed, ctx.review_min_severity)
    if len(filtered.findings) != len(parsed.findings):
        dropped = len(parsed.findings) - len(filtered.findings)
        print(
            f"[self-review] {_pr_ref(repo, pr_number)}: dropped {dropped} finding(s) "
            f"below review_min_severity={ctx.review_min_severity!r}"
        )
    return dataclasses.replace(
        filtered,
        head_sha=str(head_sha) or filtered.head_sha,
        reviewed_at=current_timestamp(ctx.state_comment_timezone),
    )


def _run_fix_phase(
    ctx: PRContext,
    pr_data: PRData,
    works_dir: Any,
    self_review: SelfReviewResult,
    state_comment: StateComment,
    commits_by_phase: list[str],
    error_collector: ErrorCollector | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[bool, bool, bool, bool]:
    """セルフレビュー XML を入力に Claude で修正フェーズを実行する。

    Returns:
        (fix_started, fix_added_commits, state_saved, fix_failed)
    """
    repo = ctx.repo
    pr_number = ctx.pr_number
    branch_name = ctx.branch_name

    self_review_path = str(Path(works_dir) / _SELF_REVIEW_FILENAME)
    prompt = build_fix_prompt(
        pr_number=pr_number,
        pr_title=ctx.title,
        base_branch=ctx.base_branch,
        self_review_path=self_review_path,
        self_review_xml=self_review.raw_xml,
        language=ctx.language,
    )

    if ctx.dry_run:
        print("\n[DRY RUN] Would execute Claude fix phase.")
        print(f"  cwd: {works_dir}")
        print(f"  model: {ctx.fix_model}")
        print(f"  findings: {len(self_review.findings)}")
        return False, False, False, False

    fix_started = False
    fix_added_commits = False
    state_saved = False
    fix_failed = False
    _remove_running_on_exit = False
    try:
        set_pr_running_label(
            repo,
            pr_number,
            pr_data=pr_data,
            enabled_pr_label_keys=ctx.enabled_pr_label_keys,
            use_pr_labels=ctx.use_pr_labels,
            state_comment=state_comment,
        )
        _remove_running_on_exit = True
        fix_started = True
        (fix_commits, _stdout) = run_claude_prompt(
            works_dir=works_dir,
            prompt=prompt,
            model=ctx.fix_model,
            silent=ctx.silent,
            phase_label="fix",
            extra_env=extra_env,
        )
        if fix_commits:
            fix_added_commits = True
            commits_by_phase.append(fix_commits)
            ctx.committed_prs.add((repo, pr_number))
        ctx.claude_prs.add((repo, pr_number))

        should_update_state = True
        dirty_check = _run_git(
            "status",
            "--porcelain",
            cwd=works_dir,
            check=False,
        )
        if dirty_check.returncode != 0:
            print(
                f"Warning: git status failed (rc={dirty_check.returncode}); skipping state update to allow retry.",
                file=sys.stderr,
            )
            if dirty_check.stderr.strip():
                print(f"  stderr: {dirty_check.stderr.strip()}", file=sys.stderr)
            if error_collector:
                error_collector.add_pr_error(
                    repo,
                    pr_number,
                    f"git status failed (rc={dirty_check.returncode}); skipping state update to allow retry.",
                )
            should_update_state = False
        elif dirty_check.stdout.strip():
            should_update_state = False
            print(
                "Cleaning worktree (uncommitted work files; per assumption: correct work is committed). "
                "State update skipped to allow retry."
            )
            print(f"  dirty files:\n{dirty_check.stdout.strip()}")
            try:
                diff_result = _run_git("diff", cwd=works_dir, check=False, timeout=10)
                if diff_result.returncode == 0 and diff_result.stdout.strip():
                    print(f"  diff:\n{diff_result.stdout.strip()}")
            except Exception:
                pass
            git_path = shutil.which("git")
            if git_path is None:
                print(
                    "Warning: git not found in PATH; skipping cleanup.",
                    file=sys.stderr,
                )
                if error_collector:
                    error_collector.add_pr_error(
                        repo, pr_number, "git not found in PATH; skipping cleanup."
                    )
            else:
                try:
                    _run_git("reset", "--hard", "HEAD", cwd=works_dir, timeout=30)
                    _run_git("clean", "-fd", cwd=works_dir, timeout=30)
                except SubprocessError as e:
                    print(
                        f"Warning: git clean failed: {e}",
                        file=sys.stderr,
                    )
                    if error_collector:
                        error_collector.add_pr_error(
                            repo, pr_number, f"git clean failed: {e}"
                        )
        if should_update_state and commits_by_phase:
            push_result = _push_if_needed(ctx, works_dir, branch_name, check=False)
            if push_result is not None and push_result.returncode != 0:
                print(
                    f"Warning: git push failed (rc={push_result.returncode}); skipping state update to allow retry.",
                    file=sys.stderr,
                )
                if push_result.stderr.strip():
                    print(f"  stderr: {push_result.stderr.strip()}", file=sys.stderr)
                if error_collector:
                    error_collector.add_pr_error(
                        repo,
                        pr_number,
                        f"git push failed (rc={push_result.returncode}); skipping state update to allow retry.",
                    )
                should_update_state = False
            elif push_result is not None:
                unpushed_check = _run_git(
                    "log",
                    f"origin/{branch_name}..HEAD",
                    "--oneline",
                    cwd=works_dir,
                    check=False,
                    timeout=10,
                )
                if unpushed_check.returncode != 0:
                    print(
                        f"Warning: git log failed (rc={unpushed_check.returncode}); skipping state update to allow retry.",
                        file=sys.stderr,
                    )
                    if unpushed_check.stderr.strip():
                        print(
                            f"  stderr: {unpushed_check.stderr.strip()}",
                            file=sys.stderr,
                        )
                    if error_collector:
                        error_collector.add_pr_error(
                            repo,
                            pr_number,
                            f"git log failed (rc={unpushed_check.returncode}); skipping state update to allow retry.",
                        )
                    should_update_state = False
                elif unpushed_check.stdout.strip():
                    print(
                        "Warning: local commits not pushed to remote; skipping state update to allow retry.",
                        file=sys.stderr,
                    )
                    print(
                        f"  unpushed commits:\n{unpushed_check.stdout.strip()}",
                        file=sys.stderr,
                    )
                    if error_collector:
                        error_collector.add_pr_error(
                            repo,
                            pr_number,
                            "local commits not pushed to remote; skipping state update to allow retry.",
                        )
                    should_update_state = False
        if should_update_state:
            commits = _parse_fix_commits(fix_commits)
            entry = SelfReviewLogEntry(
                head_sha=self_review.head_sha,
                reviewed_at=self_review.reviewed_at,
                summary=self_review.summary,
                findings=list(self_review.findings),
                commits=commits,
                fix_failed=False,
            )
            post_fix_head: str | None = None
            rev_parse = _run_git(
                "rev-parse", "HEAD", cwd=works_dir, check=False, timeout=10
            )
            if rev_parse.returncode == 0 and rev_parse.stdout.strip():
                post_fix_head = rev_parse.stdout.strip()
            try:
                _latest = load_state_comment(repo, pr_number)
                _preloaded_latest = _latest
            except Exception as e:
                print(
                    f"Warning: failed to reload state comment for {_pr_ref(repo, pr_number)}: {e}",
                    file=sys.stderr,
                )
                if error_collector:
                    error_collector.add_pr_error(
                        repo, pr_number, f"failed to reload state comment: {e}"
                    )
                _preloaded_latest = None

            try:
                append_refix_log_entry(
                    repo,
                    pr_number,
                    entry,
                    update_last_reviewed_head=True,
                    last_reviewed_head_override=post_fix_head,
                    _preloaded_state=_preloaded_latest,
                )
                state_saved = True
            except Exception as e:
                print(
                    f"Warning: failed to update state comment for {_pr_ref(repo, pr_number)}: {e}",
                    file=sys.stderr,
                )
                if error_collector:
                    error_collector.add_pr_error(
                        repo, pr_number, f"failed to update state comment: {e}"
                    )
        _remove_running_on_exit = False
    except ClaudeCommandFailedError:
        _remove_running_on_exit = False
        # 失敗時: log エントリを記録するが last_reviewed_head は更新しない
        _record_failed_fix_log_entry(ctx, self_review, state_comment, error_collector)
        raise
    except subprocess.CalledProcessError as e:
        fix_failed = True
        print(f"Error executing Claude: {e}", file=sys.stderr)
        if e.output:
            print(f"  stdout: {e.output.strip()}", file=sys.stderr)
        if e.stderr:
            print(f"  stderr: {e.stderr.strip()}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(
                repo, pr_number, f"Claude execution failed: {e}"
            )
        _record_failed_fix_log_entry(ctx, self_review, state_comment, error_collector)
    finally:
        if _remove_running_on_exit and ctx.use_pr_labels:
            edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_RUNNING_LABEL,
                enabled_pr_label_keys=ctx.enabled_pr_label_keys,
                error_collector=error_collector,
            )

    return fix_started, fix_added_commits, state_saved, fix_failed


def _parse_fix_commits(fix_commits: str) -> list[LoggedCommit]:
    """`git log --oneline` 形式の出力から LoggedCommit のリストを構築する。"""
    if not fix_commits:
        return []
    commits: list[LoggedCommit] = []
    for line in fix_commits.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if not parts or len(parts[0]) < 7:
            continue
        sha = parts[0]
        message = parts[1] if len(parts) >= 2 else ""
        commits.append(LoggedCommit(sha=sha, message=message))
    return commits


def _record_failed_fix_log_entry(
    ctx: PRContext,
    self_review: SelfReviewResult,
    state_comment: StateComment,
    error_collector: ErrorCollector | None = None,
) -> None:
    """fix 失敗時に Refix ログエントリのみ記録し、last_reviewed_head は更新しない。"""
    try:
        entry = SelfReviewLogEntry(
            head_sha=self_review.head_sha,
            reviewed_at=self_review.reviewed_at,
            summary=self_review.summary,
            findings=list(self_review.findings),
            commits=[],
            fix_failed=True,
        )
        append_refix_log_entry(
            ctx.repo,
            ctx.pr_number,
            entry,
            update_last_reviewed_head=False,
            _preloaded_state=state_comment,
        )
    except Exception as e:
        print(
            f"Warning: failed to record failed-fix log entry for "
            f"{_pr_ref(ctx.repo, ctx.pr_number)}: {e}",
            file=sys.stderr,
        )
        if error_collector:
            error_collector.add_pr_error(
                ctx.repo,
                ctx.pr_number,
                f"failed to record failed-fix log entry: {e}",
            )


def _record_no_findings_entry(
    ctx: PRContext,
    self_review: SelfReviewResult,
    state_comment: StateComment,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """指摘ゼロのレビュー結果を log に記録し last_reviewed_head を更新する。"""
    try:
        entry = SelfReviewLogEntry(
            head_sha=self_review.head_sha,
            reviewed_at=self_review.reviewed_at,
            summary=self_review.summary,
            findings=[],
            commits=[],
            fix_failed=False,
        )
        append_refix_log_entry(
            ctx.repo,
            ctx.pr_number,
            entry,
            update_last_reviewed_head=True,
            _preloaded_state=state_comment,
        )
        return True
    except Exception as e:
        print(
            f"Warning: failed to record no-findings entry for "
            f"{_pr_ref(ctx.repo, ctx.pr_number)}: {e}",
            file=sys.stderr,
        )
        if error_collector:
            error_collector.add_pr_error(
                ctx.repo,
                ctx.pr_number,
                f"failed to record no-findings entry: {e}",
            )
        return False


def _run_merge_phase(
    ctx: PRContext,
    works_dir: Any,
    has_self_review_target: bool,
    state_comment: Any,
    compare_status: str,
    behind_by: int,
    commits_by_phase: list[str],
    extra_env: dict[str, str] | None = None,
) -> None:
    """ベースブランチの取り込みとコンフリクト解消を行う。"""
    base_branch = ctx.base_branch
    base_update_method = ctx.base_update_method
    claude_limit_reached = (
        ctx.max_claude_prs_per_run > 0
        and len(ctx.claude_prs) >= ctx.max_claude_prs_per_run
    )

    if ctx.dry_run:
        if base_update_method == "rebase":
            print(
                f"[DRY RUN] Would rebase onto base branch: git rebase origin/{base_branch} "
                f"(status={compare_status}, behind_by={behind_by})"
            )
        else:
            print(
                f"[DRY RUN] Would merge base branch: git merge --no-edit origin/{base_branch} "
                f"(status={compare_status}, behind_by={behind_by})"
            )
        return

    if base_update_method == "rebase":
        _run_merge_phase_rebase(
            ctx=ctx,
            works_dir=works_dir,
            has_self_review_target=has_self_review_target,
            state_comment=state_comment,
            compare_status=compare_status,
            behind_by=behind_by,
            commits_by_phase=commits_by_phase,
            claude_limit_reached=claude_limit_reached,
            extra_env=extra_env,
        )
    else:
        _run_merge_phase_merge(
            ctx=ctx,
            works_dir=works_dir,
            has_self_review_target=has_self_review_target,
            state_comment=state_comment,
            compare_status=compare_status,
            behind_by=behind_by,
            commits_by_phase=commits_by_phase,
            claude_limit_reached=claude_limit_reached,
            extra_env=extra_env,
        )


def _run_merge_phase_merge(
    *,
    ctx: PRContext,
    works_dir: Any,
    has_self_review_target: bool,
    state_comment: Any,
    compare_status: str,
    behind_by: int,
    commits_by_phase: list[str],
    claude_limit_reached: bool,
    extra_env: dict[str, str] | None = None,
) -> None:
    """merge パスのベースブランチ取り込み処理。"""
    repo = ctx.repo
    pr_number = ctx.pr_number
    base_branch = ctx.base_branch
    branch_name = ctx.branch_name

    print(
        f"[merge-base] {_pr_ref(repo, pr_number)}: git merge --no-edit origin/{base_branch} "
        f"(status={compare_status}, behind_by={behind_by})"
    )
    try:
        merged_changes, had_conflicts = merge_base_branch(works_dir, base_branch)
    except Exception as e:
        print(
            f"[merge-base:error] {_pr_ref(repo, pr_number)}: merge failed "
            f"(base={base_branch}, head={branch_name}, status={compare_status}, behind_by={behind_by})",
            file=sys.stderr,
        )
        print(f"  details: {e}", file=sys.stderr)
        raise

    if merged_changes:
        merge_log = _run_git(
            "log", "--oneline", "-1", cwd=works_dir, check=False, timeout=10
        ).stdout.strip()
        commits_by_phase.append(merge_log or f"merge origin/{base_branch}")
        ctx.committed_prs.add((repo, pr_number))
        if not had_conflicts:
            print(f"[merge-base] {_pr_ref(repo, pr_number)}: merged successfully")

    if had_conflicts and not claude_limit_reached:
        print(
            f"[merge-base] {_pr_ref(repo, pr_number)}: conflict detected; "
            "running Claude for conflict resolution"
        )
        conflict_prompt = build_conflict_resolution_prompt(
            pr_number, ctx.title, base_branch
        )
        try:
            (conflict_commits, _stdout) = run_claude_prompt(
                works_dir=works_dir,
                prompt=conflict_prompt,
                model=ctx.fix_model,
                silent=ctx.silent,
                phase_label="merge-conflict-resolution",
                extra_env=extra_env,
            )
        except Exception as e:
            print(
                f"[merge-base:error] {_pr_ref(repo, pr_number)}: Claude conflict-resolution failed",
                file=sys.stderr,
            )
            print(f"  details: {e}", file=sys.stderr)
            raise
        if conflict_commits:
            commits_by_phase.append(conflict_commits)
            ctx.committed_prs.add((repo, pr_number))
        ctx.claude_prs.add((repo, pr_number))
        has_conflicts = has_merge_conflicts(works_dir)
        merge_head_exists = (works_dir / ".git" / "MERGE_HEAD").exists()
        conflict_resolved = not has_conflicts and not merge_head_exists
        print(
            f"[merge-base] {_pr_ref(repo, pr_number)}: conflict resolution check -> "
            f"{'resolved' if conflict_resolved else 'still_conflicted'}"
            f" (conflicts={has_conflicts}, merge_head={merge_head_exists})"
        )
        if not conflict_resolved:
            raise RuntimeError(
                "Merge conflict markers remain or MERGE_HEAD not cleared after conflict-resolution phase"
            )
    elif had_conflicts and claude_limit_reached:
        print(
            f"[merge-base] {_pr_ref(repo, pr_number)}: conflict detected but Claude limit reached; "
            "aborting merge to avoid leaving conflict markers"
        )
        _run_git("merge", "--abort", cwd=works_dir, check=False, timeout=30)


def _run_merge_phase_rebase(
    *,
    ctx: PRContext,
    works_dir: Any,
    has_self_review_target: bool,
    state_comment: Any,
    compare_status: str,
    behind_by: int,
    commits_by_phase: list[str],
    claude_limit_reached: bool,
    extra_env: dict[str, str] | None = None,
) -> None:
    """rebase パスのベースブランチ取り込み処理。"""
    repo = ctx.repo
    pr_number = ctx.pr_number
    base_branch = ctx.base_branch
    branch_name = ctx.branch_name
    _REBASE_CONFLICT_LOOP_LIMIT = 20

    print(
        f"[merge-base] {_pr_ref(repo, pr_number)}: git rebase origin/{base_branch} "
        f"(status={compare_status}, behind_by={behind_by})"
    )
    try:
        rebased_changes, had_conflicts = rebase_base_branch(works_dir, base_branch)
    except Exception as e:
        print(
            f"[merge-base:error] {_pr_ref(repo, pr_number)}: rebase failed "
            f"(base={base_branch}, head={branch_name}, status={compare_status}, behind_by={behind_by})",
            file=sys.stderr,
        )
        print(f"  details: {e}", file=sys.stderr)
        raise

    if had_conflicts and claude_limit_reached:
        print(
            f"[merge-base] {_pr_ref(repo, pr_number)}: rebase conflict detected but Claude limit reached; "
            "aborting rebase to avoid leaving conflict markers"
        )
        abort_rebase(works_dir)
        return

    if had_conflicts:
        print(
            f"[merge-base] {_pr_ref(repo, pr_number)}: rebase conflict detected; "
            "running Claude for conflict resolution"
        )
        conflict_prompt = build_conflict_resolution_prompt(
            pr_number, ctx.title, base_branch
        )
        for _iteration in range(_REBASE_CONFLICT_LOOP_LIMIT):
            try:
                (conflict_commits, _stdout) = run_claude_prompt(
                    works_dir=works_dir,
                    prompt=conflict_prompt,
                    model=ctx.fix_model,
                    silent=ctx.silent,
                    phase_label="merge-conflict-resolution",
                    extra_env=extra_env,
                )
            except Exception as e:
                print(
                    f"[merge-base:error] {_pr_ref(repo, pr_number)}: Claude conflict-resolution failed",
                    file=sys.stderr,
                )
                print(f"  details: {e}", file=sys.stderr)
                abort_rebase(works_dir)
                raise
            if conflict_commits:
                commits_by_phase.append(conflict_commits)
                ctx.committed_prs.add((repo, pr_number))
            ctx.claude_prs.add((repo, pr_number))
            _run_git("add", ".", cwd=works_dir, timeout=30)
            if not is_rebase_in_progress(works_dir):
                ancestor_check = _run_git(
                    "merge-base",
                    "--is-ancestor",
                    f"origin/{base_branch}",
                    "HEAD",
                    cwd=works_dir,
                    check=False,
                    timeout=30,
                )
                if ancestor_check.returncode != 0:
                    raise RuntimeError(
                        f"Rebase is not in progress but origin/{base_branch} is not an ancestor of HEAD; "
                        "rebase may have been aborted by Claude"
                    )
                rebase_done = True
                print(
                    f"[merge-base] {_pr_ref(repo, pr_number)}: "
                    "rebase already completed by Claude",
                )
            else:
                try:
                    rebase_done = continue_rebase(works_dir)
                except Exception as e:
                    print(
                        f"[merge-base:error] {_pr_ref(repo, pr_number)}: git rebase --continue failed",
                        file=sys.stderr,
                    )
                    print(f"  details: {e}", file=sys.stderr)
                    abort_rebase(works_dir)
                    raise RuntimeError(f"git rebase --continue failed: {e}") from e
            if rebase_done:
                break
        else:
            abort_rebase(works_dir)
            raise RuntimeError(
                f"Rebase conflict not resolved after {_REBASE_CONFLICT_LOOP_LIMIT} iterations"
            )

        if is_rebase_in_progress(works_dir):
            abort_rebase(works_dir)
            raise RuntimeError("Rebase still in progress after conflict resolution")
        rebased_changes = True

    if rebased_changes:
        rebase_log = _run_git(
            "log", "--oneline", "-1", cwd=works_dir, check=False, timeout=10
        ).stdout.strip()
        commits_by_phase.append(rebase_log or f"rebase onto origin/{base_branch}")
        ctx.committed_prs.add((repo, pr_number))
        ctx.needs_force_push = True
        print(f"[merge-base] {_pr_ref(repo, pr_number)}: rebased successfully")


def _process_single_pr(
    pr: PRData,
    repo: str,
    dry_run: bool,
    silent: bool,
    review_model: str,
    fix_model: str,
    review_min_severity: str,
    auto_merge_enabled: bool,
    merge_method: str,
    base_update_method: str,
    process_draft_prs: bool,
    state_comment_timezone: str,
    language: str,
    enabled_pr_label_keys: set[str],
    max_modified_prs: int,
    max_committed_prs: int,
    max_claude_prs: int,
    modified_prs: set[tuple[str, int]],
    committed_prs: set[tuple[str, int]],
    claude_prs: set[tuple[str, int]],
    user_name: Any,
    user_email: Any,
    batch_setup: dict | None = None,
    batch_global_setup: dict | None = None,
    python_version: str | None = None,
    node_version: str | None = None,
    backfilled_count: int = 0,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
    ci_pending_wait_seconds: int = 0,
    exclude_authors: list[str] | None = None,
    exclude_labels: list[str] | None = None,
    target_authors: list[str] | None = None,
    auto_merge_authors: list[str] | None = None,
    use_pr_labels: bool = True,
    incremental_review: bool = True,
    error_collector: ErrorCollector | None = None,
) -> tuple[bool, bool, tuple[str, int, str] | None, bool]:
    """Process a single PR within process_repo's main loop.

    Returns:
        (pr_fetch_failed, count_as_processed, commits_entry, cacheable)
    """
    pr_number_raw = pr.get("number")
    if not isinstance(pr_number_raw, int):
        print(f"Skipping PR with invalid number: {pr_number_raw!r}")
        return False, False, None, False
    pr_number = pr_number_raw
    pr_title = str(pr.get("title") or "")
    pr_state = str(pr.get("state") or "")
    if pr_state in ("MERGED", "CLOSED"):
        print(f"\nSkipping {pr_state} {_pr_ref(repo, pr_number)}: {pr_title}")
        return False, False, None, False
    is_draft = bool(pr.get("isDraft"))
    if is_draft and not process_draft_prs:
        print(f"\nSkipping DRAFT {_pr_ref(repo, pr_number)}: {pr_title}")
        return False, False, None, False

    if exclude_authors:
        pr_author = pr.get("author", {}) or {}
        author_login = pr_author.get("login", "") or ""
        if any(fnmatch.fnmatchcase(author_login, pat) for pat in exclude_authors):
            print(
                f"\nSkipping {_pr_ref(repo, pr_number)} "
                f"(author '{author_login}' matches exclude_authors): {pr_title}"
            )
            return False, False, None, False

    if exclude_labels:
        pr_labels = pr.get("labels", []) or []
        pr_label_names = [
            lbl.get("name", "") for lbl in pr_labels if isinstance(lbl, dict)
        ]
        matched_label = next(
            (
                lbl_name
                for lbl_name in pr_label_names
                for pat in exclude_labels
                if fnmatch.fnmatchcase(lbl_name, pat)
            ),
            None,
        )
        if matched_label is not None:
            print(
                f"\nSkipping {_pr_ref(repo, pr_number)} "
                f"(label '{matched_label}' matches exclude_labels): {pr_title}"
            )
            return False, False, None, False

    if target_authors:
        pr_author = pr.get("author", {}) or {}
        author_login = pr_author.get("login", "") or ""
        if not any(fnmatch.fnmatchcase(author_login, pat) for pat in target_authors):
            print(
                f"\nSkipping {_pr_ref(repo, pr_number)} "
                f"(author '{author_login}' not in target_authors): {pr_title}"
            )
            return False, False, None, False

    if auto_merge_enabled and auto_merge_authors:
        pr_author = pr.get("author", {}) or {}
        author_login = pr_author.get("login", "") or ""
        if not any(
            fnmatch.fnmatchcase(author_login, pat) for pat in auto_merge_authors
        ):
            print(
                f"{_pr_ref(repo, pr_number)}: "
                f"author '{author_login}' not in auto_merge_authors; "
                "auto-merge disabled for this PR"
            )
            auto_merge_enabled = False

    if (
        max_modified_prs > 0
        and len(modified_prs) + backfilled_count >= max_modified_prs
    ):
        print(
            f"\nSkipping {_pr_ref(repo, pr_number)}: "
            f"max_modified_prs_per_run limit reached ({max_modified_prs})"
        )
        return False, False, None, False

    print(f"\nChecking {_pr_ref(repo, pr_number)}: {pr_title}")

    try:
        pr_data = fetch_pr_details(repo, pr_number)
    except Exception as e:
        print(f"Error fetching PR details: {e}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(
                repo, pr_number, f"Failed to fetch PR details: {e}"
            )
        return True, False, None, False

    branch_name = pr_data.get("headRefName")
    base_branch = pr_data.get("baseRefName")
    if not branch_name:
        print(f"Could not find branch name for {_pr_ref(repo, pr_number)}, skipping")
        return False, False, None, False
    if not base_branch:
        print(f"Could not find base branch for {_pr_ref(repo, pr_number)}, skipping")
        return False, False, None, False

    try:
        state_comment: StateComment = load_state_comment(repo, pr_number)
    except Exception as e:
        print(f"Error fetching state comment: {e}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(
                repo, pr_number, f"Failed to load state comment: {e}"
            )
        return True, False, None, False

    head_sha = str(pr_data.get("headRefOid") or "")

    compare_status, behind_by = get_branch_compare_status(
        repo, base_branch, branch_name
    )
    is_behind = needs_base_merge(compare_status, behind_by)
    if is_behind:
        print(
            f"{_pr_ref(repo, pr_number)} is behind base branch: "
            f"status={compare_status}, behind_by={behind_by}"
        )

    ctx = PRContext(
        repo=repo,
        pr_number=pr_number,
        title=pr_title,
        is_draft=is_draft,
        branch_name=branch_name,
        base_branch=base_branch,
        works_dir=None,
        labels=cast(list[LabelInfo], pr_data.get("labels", [])),
        dry_run=dry_run,
        silent=silent,
        review_model=review_model,
        fix_model=fix_model,
        review_min_severity=review_min_severity,
        auto_merge_enabled=auto_merge_enabled,
        enabled_pr_label_keys=enabled_pr_label_keys,
        process_draft_prs=process_draft_prs,
        state_comment_timezone=state_comment_timezone,
        language=language,
        max_modified_prs_per_run=max_modified_prs,
        max_committed_prs_per_run=max_committed_prs,
        max_claude_prs_per_run=max_claude_prs,
        modified_prs=modified_prs,
        committed_prs=committed_prs,
        claude_prs=claude_prs,
        ci_empty_as_success=ci_empty_as_success,
        ci_empty_grace_minutes=ci_empty_grace_minutes,
        ci_pending_wait_seconds=ci_pending_wait_seconds,
        merge_method=merge_method,
        base_update_method=base_update_method,
        use_pr_labels=use_pr_labels,
        incremental_review=incremental_review,
    )

    commit_limit_reached = (
        max_committed_prs > 0 and len(committed_prs) >= max_committed_prs
    )
    claude_limit_reached = max_claude_prs > 0 and len(claude_prs) >= max_claude_prs

    # idempotency: 既にレビュー済みの head_sha なら何もしない
    if not is_behind and head_sha and state_comment.last_reviewed_head == head_sha:
        print(
            f"{_pr_ref(repo, pr_number)}: head {head_sha[:7]} already reviewed; "
            "skipping self-review/fix."
        )
        _done_updated, _ = update_done_label_if_completed(
            repo=repo,
            pr_number=pr_number,
            has_self_review_target=False,
            self_review_ran=False,
            fix_added_commits=False,
            fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data=pr_data,
            dry_run=dry_run,
            auto_merge_enabled=auto_merge_enabled,
            merge_method=merge_method,
            enabled_pr_label_keys=enabled_pr_label_keys,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            ci_pending_wait_seconds=ci_pending_wait_seconds,
            use_pr_labels=use_pr_labels,
            state_comment=state_comment,
            error_collector=error_collector,
        )
        if _done_updated:
            modified_prs.add((repo, pr_number))
        return False, True, None, True

    try:
        log_group("Git repository setup")
        works_dir, setup_env = prepare_repository(
            repo,
            branch_name,
            user_name,
            user_email,
            batch_setup=batch_setup,
            batch_global_setup=batch_global_setup,
            python_version=python_version,
            node_version=node_version,
        )
        log_endgroup()
    except Exception as e:
        log_endgroup()
        print(f"Error preparing repository: {e}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(
                repo, pr_number, f"Failed to prepare repository: {e}"
            )
        return False, True, None, False

    ctx.works_dir = works_dir
    commits_by_phase: list[str] = []
    self_review_ran = False
    fix_added_commits = False
    fix_failed = False
    state_saved = False

    _has_done_label = resolve_workflow_status(state_comment, pr_data) == "done"
    _ran_set_running = False

    try:
        if is_behind and not commit_limit_reached:
            if not dry_run and _has_done_label and not _ran_set_running:
                if set_pr_running_label(
                    repo,
                    pr_number,
                    pr_data=pr_data,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    use_pr_labels=use_pr_labels,
                    state_comment=state_comment,
                ):
                    modified_prs.add((repo, pr_number))
                    _mark_pr_data_as_running(pr_data)
                    _ran_set_running = True
            _run_merge_phase(
                ctx,
                works_dir,
                False,
                state_comment,
                compare_status,
                behind_by,
                commits_by_phase,
                extra_env=setup_env,
            )
            # base 取り込みが入った場合は head_sha が変わるため再 fetch
            if commits_by_phase:
                try:
                    pr_data = fetch_pr_details(repo, pr_number)
                    head_sha = str(pr_data.get("headRefOid") or "")
                except Exception as e:
                    print(
                        f"Warning: failed to re-fetch PR details after merge phase: {e}",
                        file=sys.stderr,
                    )
        elif is_behind and commit_limit_reached:
            print(
                f"[merge-base] {_pr_ref(repo, pr_number)}: "
                "skipped due to max_committed_prs_per_run limit"
            )
    except Exception:
        if _ran_set_running:
            try:
                update_workflow_status(repo, pr_number, "done", _preloaded_state=None)
            except Exception:
                pass
            if use_pr_labels:
                edit_pr_label(
                    repo,
                    pr_number,
                    add=False,
                    label=REFIX_RUNNING_LABEL,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    error_collector=error_collector,
                )
                edit_pr_label(
                    repo,
                    pr_number,
                    add=True,
                    label=REFIX_DONE_LABEL,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    error_collector=error_collector,
                )
        raise

    # base 取り込み後に既にレビュー済み head になっていれば再レビュー不要
    if (
        head_sha
        and state_comment.last_reviewed_head == head_sha
        and not commits_by_phase
    ):
        _done_updated, _ = update_done_label_if_completed(
            repo=repo,
            pr_number=pr_number,
            has_self_review_target=False,
            self_review_ran=False,
            fix_added_commits=False,
            fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data=pr_data,
            dry_run=dry_run,
            auto_merge_enabled=auto_merge_enabled,
            merge_method=merge_method,
            enabled_pr_label_keys=enabled_pr_label_keys,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            ci_pending_wait_seconds=ci_pending_wait_seconds,
            use_pr_labels=use_pr_labels,
            state_comment=state_comment,
            error_collector=error_collector,
        )
        if _done_updated:
            modified_prs.add((repo, pr_number))
        return False, True, None, True

    self_review: SelfReviewResult | None = None
    try:
        if claude_limit_reached:
            print(
                f"[self-review] {_pr_ref(repo, pr_number)}: skipped due to "
                f"max_claude_prs_per_run limit ({max_claude_prs})"
            )
        else:
            self_review = _run_self_review_phase(
                ctx,
                pr_data,
                works_dir,
                state_comment,
                extra_env=setup_env,
            )
            ctx.claude_prs.add((repo, pr_number))
            self_review_ran = True
    except Exception:
        if _ran_set_running and use_pr_labels:
            try:
                update_workflow_status(repo, pr_number, "done", _preloaded_state=None)
            except Exception:
                pass
            edit_pr_label(
                repo,
                pr_number,
                add=False,
                label=REFIX_RUNNING_LABEL,
                enabled_pr_label_keys=enabled_pr_label_keys,
                error_collector=error_collector,
            )
        raise

    if self_review is None:
        # dry_run / Claude 上限
        if commits_by_phase and not dry_run:
            _push_if_needed(ctx, works_dir, branch_name)
        state_saved = True
        _done_updated, _ = update_done_label_if_completed(
            repo=repo,
            pr_number=pr_number,
            has_self_review_target=False,
            self_review_ran=False,
            fix_added_commits=False,
            fix_failed=False,
            state_saved=state_saved,
            commits_by_phase=commits_by_phase,
            pr_data=pr_data,
            dry_run=dry_run,
            auto_merge_enabled=auto_merge_enabled,
            merge_method=merge_method,
            enabled_pr_label_keys=enabled_pr_label_keys,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            ci_pending_wait_seconds=ci_pending_wait_seconds,
            use_pr_labels=use_pr_labels,
            state_comment=state_comment,
            error_collector=error_collector,
        )
        if _done_updated:
            modified_prs.add((repo, pr_number))
        if commits_by_phase:
            return (
                False,
                True,
                (repo, pr_number, "\n".join(commits_by_phase)),
                False,
            )
        return False, True, None, False

    if not self_review.findings:
        print(
            f"[self-review] {_pr_ref(repo, pr_number)}: no findings; "
            "recording no-issues entry."
        )
        state_saved = _record_no_findings_entry(
            ctx, self_review, state_comment, error_collector
        )
        _done_updated, _ = update_done_label_if_completed(
            repo=repo,
            pr_number=pr_number,
            has_self_review_target=False,
            self_review_ran=self_review_ran,
            fix_added_commits=False,
            fix_failed=False,
            state_saved=state_saved,
            commits_by_phase=commits_by_phase,
            pr_data=pr_data,
            dry_run=dry_run,
            auto_merge_enabled=auto_merge_enabled,
            merge_method=merge_method,
            enabled_pr_label_keys=enabled_pr_label_keys,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            ci_pending_wait_seconds=ci_pending_wait_seconds,
            use_pr_labels=use_pr_labels,
            state_comment=state_comment,
            error_collector=error_collector,
        )
        if _done_updated:
            modified_prs.add((repo, pr_number))
        if commits_by_phase:
            return (
                False,
                True,
                (repo, pr_number, "\n".join(commits_by_phase)),
                True,
            )
        return False, True, None, True

    # fix phase 実行
    if commit_limit_reached:
        print(
            f"[fix] {_pr_ref(repo, pr_number)}: skipped due to "
            f"max_committed_prs_per_run limit ({max_committed_prs})"
        )
        # base 取り込みのみ残っていれば push
        if commits_by_phase and not dry_run:
            _push_if_needed(ctx, works_dir, branch_name)
        return False, True, None, False

    print(
        f"[fix] {_pr_ref(repo, pr_number)}: applying {len(self_review.findings)} "
        f"finding(s) (model={ctx.fix_model})"
    )
    fix_started, fix_added_commits, state_saved, fix_failed = _run_fix_phase(
        ctx,
        pr_data,
        works_dir,
        self_review,
        state_comment,
        commits_by_phase,
        error_collector=error_collector,
        extra_env=setup_env,
    )

    _done_updated, _ci_grace = update_done_label_if_completed(
        repo=repo,
        pr_number=pr_number,
        has_self_review_target=True,
        self_review_ran=self_review_ran,
        fix_added_commits=fix_added_commits,
        fix_failed=fix_failed,
        state_saved=state_saved,
        commits_by_phase=commits_by_phase,
        pr_data=pr_data,
        dry_run=dry_run,
        auto_merge_enabled=auto_merge_enabled,
        merge_method=merge_method,
        enabled_pr_label_keys=enabled_pr_label_keys,
        ci_empty_as_success=ci_empty_as_success,
        ci_empty_grace_minutes=ci_empty_grace_minutes,
        ci_pending_wait_seconds=ci_pending_wait_seconds,
        use_pr_labels=use_pr_labels,
        state_comment=state_comment,
        error_collector=error_collector,
    )
    if _done_updated:
        modified_prs.add((repo, pr_number))
    _cacheable = not dry_run and state_saved and not fix_failed and not _ci_grace
    if commits_by_phase:
        return (
            False,
            True,
            (repo, pr_number, "\n".join(commits_by_phase)),
            _cacheable,
        )
    return False, True, None, _cacheable


def process_repo(
    repo_info: RepositoryEntry,
    dry_run: bool = False,
    silent: bool = False,
    config: AppConfig | None = None,
    global_modified_prs: set[tuple[str, int]] | None = None,
    global_committed_prs: set[tuple[str, int]] | None = None,
    global_claude_prs: set[tuple[str, int]] | None = None,
    global_backfilled_count: list[int] | None = None,
    error_collector: ErrorCollector | None = None,
    target_pr_number: int | None = None,
) -> list[tuple[str, int, str]]:
    """Process a single repository for PR self-review + fix."""
    runtime_config = config or DEFAULT_CONFIG
    model_config = runtime_config.get("models", {})
    review_model = str(
        model_config.get("review", DEFAULT_CONFIG["models"]["review"])
    ).strip()
    fix_model = str(model_config.get("fix", DEFAULT_CONFIG["models"]["fix"])).strip()
    auto_merge_enabled = bool(
        runtime_config.get("auto_merge", DEFAULT_CONFIG["auto_merge"])
    )
    process_draft_prs = get_process_draft_prs(runtime_config, DEFAULT_CONFIG)
    enabled_pr_label_keys = get_enabled_pr_label_keys(runtime_config, DEFAULT_CONFIG)
    use_pr_labels = get_use_pr_labels(runtime_config, DEFAULT_CONFIG)
    incremental_review = get_incremental_review(runtime_config, DEFAULT_CONFIG)
    exclude_authors = list(
        runtime_config.get("exclude_authors") or DEFAULT_CONFIG["exclude_authors"]
    )
    exclude_labels = list(
        runtime_config.get("exclude_labels") or DEFAULT_CONFIG["exclude_labels"]
    )
    target_authors = list(
        runtime_config.get("target_authors") or DEFAULT_CONFIG["target_authors"]
    )
    auto_merge_authors = list(
        runtime_config.get("auto_merge_authors") or DEFAULT_CONFIG["auto_merge_authors"]
    )
    state_comment_timezone = (
        str(
            runtime_config.get(
                "state_comment_timezone", DEFAULT_CONFIG["state_comment_timezone"]
            )
        ).strip()
        or DEFAULT_CONFIG["state_comment_timezone"]
    )
    language = (
        str(runtime_config.get("language", DEFAULT_CONFIG["language"])).strip()
        or DEFAULT_CONFIG["language"]
    )
    max_modified_prs = int(
        runtime_config.get("max_modified_prs_per_run")
        or DEFAULT_CONFIG["max_modified_prs_per_run"]
    )
    max_committed_prs = int(
        runtime_config.get("max_committed_prs_per_run")
        or DEFAULT_CONFIG["max_committed_prs_per_run"]
    )
    max_claude_prs = int(
        runtime_config.get("max_claude_prs_per_run")
        or DEFAULT_CONFIG["max_claude_prs_per_run"]
    )
    ci_empty_as_success = bool(
        runtime_config.get("ci_empty_as_success", DEFAULT_CONFIG["ci_empty_as_success"])
    )
    ci_empty_grace_minutes = int(
        runtime_config.get("ci_empty_grace_minutes")
        or DEFAULT_CONFIG["ci_empty_grace_minutes"]
    )
    # 0 は「実行内ポーリング無効」の有効値なので or ではなく明示的 None チェックを使う。
    _ci_pending_wait = runtime_config.get("ci_pending_wait_seconds")
    ci_pending_wait_seconds = (
        DEFAULT_CONFIG["ci_pending_wait_seconds"]
        if _ci_pending_wait is None
        else int(_ci_pending_wait)
    )
    merge_method = (
        str(runtime_config.get("merge_method", DEFAULT_CONFIG["merge_method"])).strip()
        or DEFAULT_CONFIG["merge_method"]
    )
    base_update_method = (
        str(
            runtime_config.get(
                "base_update_method", DEFAULT_CONFIG["base_update_method"]
            )
        ).strip()
        or DEFAULT_CONFIG["base_update_method"]
    )
    review_min_severity = (
        str(
            runtime_config.get(
                "review_min_severity", DEFAULT_CONFIG["review_min_severity"]
            )
        )
        .strip()
        .lower()
        or DEFAULT_CONFIG["review_min_severity"]
    )

    repo_value = repo_info.get("repo")
    if not isinstance(repo_value, str) or not repo_value.strip():
        raise ValueError("repo_info['repo'] must be a non-empty string")
    repo = repo_value
    user_name = repo_info.get("user_name") or runtime_config.get("user_name")
    user_email = repo_info.get("user_email") or runtime_config.get("user_email")
    global_setup = runtime_config.get("global_setup") if runtime_config else None
    batch_setup = runtime_config.get("setup") if runtime_config else None
    python_version = runtime_config.get("python_version") if runtime_config else None
    if python_version is not None and not isinstance(python_version, str):
        python_version = None
    node_version = runtime_config.get("node_version") if runtime_config else None
    if node_version is not None and not isinstance(node_version, str):
        node_version = None

    print(f"\n{'=' * SEPARATOR_LEN}")
    print(f"Processing: {repo}")
    if user_name or user_email:
        print(f"Git user: {user_name or 'default'} <{user_email or 'default'}>")
    print("=" * SEPARATOR_LEN)

    commits_added_to: list[tuple[str, int, str]] = []
    processed_count = 0
    modified_prs: set[tuple[str, int]] = (
        global_modified_prs if global_modified_prs is not None else set()
    )
    committed_prs: set[tuple[str, int]] = (
        global_committed_prs if global_committed_prs is not None else set()
    )
    claude_prs: set[tuple[str, int]] = (
        global_claude_prs if global_claude_prs is not None else set()
    )
    fetch_failed = False
    pr_fetch_failed = False

    if target_pr_number is not None:
        try:
            prs = [fetch_single_pr(repo, target_pr_number)]
        except Exception as e:
            print(
                f"Error fetching PR #{target_pr_number} for {repo}: {e}",
                file=sys.stderr,
            )
            fetch_failed = True
            if error_collector:
                error_collector.add_repo_error(
                    repo, f"Failed to fetch PR #{target_pr_number}: {e}"
                )
            return []
    else:
        try:
            prs = fetch_open_prs(repo, limit=1000)
        except Exception as e:
            print(f"Error fetching PRs for {repo}: {e}", file=sys.stderr)
            fetch_failed = True
            if error_collector:
                error_collector.add_repo_error(repo, f"Failed to fetch PRs: {e}")
            return []
    backfilled_count = 0
    if auto_merge_enabled and not dry_run and target_pr_number is None:
        prev_total = len(modified_prs) + (
            global_backfilled_count[0] if global_backfilled_count is not None else 0
        )
        backfill_limit = (
            max(0, max_modified_prs - prev_total) if max_modified_prs > 0 else 100
        )
        backfilled_count = backfill_merged_labels(
            repo,
            limit=backfill_limit,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
        if global_backfilled_count is not None:
            global_backfilled_count[0] += backfilled_count
    total_backfilled = (
        global_backfilled_count[0]
        if global_backfilled_count is not None
        else backfilled_count
    )

    if not prs:
        print(f"No open PRs found in {repo}")
        return []

    print(f"Found {len(prs)} open PR(s)")
    for pr in prs:
        try:
            this_pr_fetch_failed, count_as_processed, commits_entry, _cacheable = (
                _process_single_pr(
                    pr=pr,
                    repo=repo,
                    dry_run=dry_run,
                    silent=silent,
                    review_model=review_model,
                    fix_model=fix_model,
                    review_min_severity=review_min_severity,
                    auto_merge_enabled=auto_merge_enabled,
                    merge_method=merge_method,
                    base_update_method=base_update_method,
                    process_draft_prs=process_draft_prs,
                    state_comment_timezone=state_comment_timezone,
                    language=language,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    max_modified_prs=max_modified_prs,
                    max_committed_prs=max_committed_prs,
                    max_claude_prs=max_claude_prs,
                    modified_prs=modified_prs,
                    committed_prs=committed_prs,
                    claude_prs=claude_prs,
                    user_name=user_name,
                    user_email=user_email,
                    batch_setup=batch_setup,
                    batch_global_setup=global_setup,
                    python_version=python_version,
                    node_version=node_version,
                    backfilled_count=total_backfilled,
                    ci_empty_as_success=ci_empty_as_success,
                    ci_empty_grace_minutes=ci_empty_grace_minutes,
                    ci_pending_wait_seconds=ci_pending_wait_seconds,
                    exclude_authors=exclude_authors,
                    exclude_labels=exclude_labels,
                    target_authors=target_authors,
                    auto_merge_authors=auto_merge_authors,
                    use_pr_labels=use_pr_labels,
                    incremental_review=incremental_review,
                    error_collector=error_collector,
                )
            )
            if this_pr_fetch_failed:
                pr_fetch_failed = True
            if count_as_processed:
                processed_count += 1
            if commits_entry:
                commits_added_to.append(commits_entry)
        except ClaudeCommandFailedError:
            raise
        except Exception as e:
            print(
                f"Error processing {repo} PR #{pr.get('number', '?')} "
                f"(id={pr.get('id', '?')}): {e}",
                file=sys.stderr,
            )
            pr_fetch_failed = True
            if error_collector:
                error_collector.add_pr_error(repo, pr.get("number", 0), str(e))
            continue

    if processed_count == 0 and not fetch_failed and not pr_fetch_failed:
        print(f"No open PRs requiring self-review/fix in {repo}")
    if auto_merge_enabled and not dry_run and target_pr_number is None:
        if max_modified_prs > 0:
            remaining = max_modified_prs - len(modified_prs) - total_backfilled
            if remaining > 0:
                additional = backfill_merged_labels(
                    repo,
                    limit=remaining,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    error_collector=error_collector,
                )
                if global_backfilled_count is not None:
                    global_backfilled_count[0] += additional
        else:
            backfill_merged_labels(
                repo,
                enabled_pr_label_keys=enabled_pr_label_keys,
                error_collector=error_collector,
            )
    return commits_added_to


def _fetch_all_open_pr_numbers(repo: str) -> list[int]:
    """全 open PR 番号リストを返す。"""
    from subprocess_helpers import run_command

    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        "1000",
        "--json",
        "number",
        "--jq",
        ".[].number",
    ]
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"_fetch_all_open_pr_numbers: gh pr list failed: {result.stderr}"
        )
    if not result.stdout.strip():
        return []
    return [int(n) for n in result.stdout.strip().splitlines() if n.strip().isdigit()]


def _resolve_action_targets(repo: str) -> list[int]:
    """GitHub Actions イベントから処理対象の PR 番号リストを返す。

    対応イベント: pull_request, pull_request_review, schedule, workflow_dispatch
    """
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_name or not event_path:
        print("Error: GITHUB_EVENT_NAME/GITHUB_EVENT_PATH not set", file=sys.stderr)
        sys.exit(1)

    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)

    if event_name in ("pull_request", "pull_request_review"):
        pr_number = event.get("pull_request", {}).get("number")
        return [pr_number] if pr_number else []

    if event_name == "schedule":
        return _fetch_all_open_pr_numbers(repo)

    if event_name == "workflow_dispatch":
        inputs = event.get("inputs") or {}
        pr_str = inputs.get("pr-number") if isinstance(inputs, dict) else None
        if pr_str and str(pr_str).strip().isdigit():
            return [int(pr_str)]
        return _fetch_all_open_pr_numbers(repo)

    print(f"Unsupported event: {event_name}; skipping.")
    return []


_DEFAULT_BATCH_CONFIG = str(Path(__file__).resolve().parents[1] / ".refix-batch.yaml")


def _resolve_single_config_path(args_config: str) -> tuple[str | None, str]:
    """シングルモード用の設定ファイルパスを解決する。"""
    if args_config != _DEFAULT_BATCH_CONFIG and Path(args_config).exists():
        return args_config, f"--config: {args_config}"

    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        fallback = Path(workspace) / ".refix.yaml"
        if fallback.exists():
            return str(fallback), f"repo file: {fallback}"

    repo_root = Path(__file__).resolve().parents[1]
    fallback = repo_root / ".refix.yaml"
    if fallback.exists():
        return str(fallback), f"repo file: {fallback}"

    return None, "defaults"


def _resolve_batch_config_path(args_config: str) -> tuple[str, str]:
    """バッチモード用の設定ファイルパスを解決する。"""
    if Path(args_config).exists():
        return args_config, f"--config: {args_config}"

    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        fallback = Path(workspace) / ".refix-batch.yaml"
        if fallback.exists():
            return str(fallback), f"repo file: {fallback}"

    return args_config, f"--config: {args_config} (not found)"


def main():
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(stdout_reconfigure):
        stdout_reconfigure(line_buffering=True)
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if callable(stderr_reconfigure):
        stderr_reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Refix - Claude self-review + auto-fix tool"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"refix {__version__}",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show claude command without executing",
    )
    _default_config = Path(__file__).resolve().parents[1] / ".refix-batch.yaml"
    parser.add_argument(
        "--config",
        default=str(_default_config),
        help="Path to YAML config file (default: <repo_root>/.refix-batch.yaml)",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Minimize log output (default: show debug-level logs)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Target repository in 'owner/repo' format for single-PR mode (requires --pr)",
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="PR number for single-PR mode (requires --repo)",
    )
    parser.add_argument(
        "--action",
        action="store_true",
        default=False,
        help="GitHub Actions mode: auto-detect PR targets from GITHUB_EVENT_NAME/GITHUB_EVENT_PATH",
    )

    args = parser.parse_args()

    if not args.action and (args.repo is None) != (args.pr is None):
        print("Error: --repo and --pr must be specified together.", file=sys.stderr)
        sys.exit(1)

    load_dotenv()

    if args.action:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        if not repo:
            print(
                "Error: --repo or GITHUB_REPOSITORY must be set in action mode.",
                file=sys.stderr,
            )
            sys.exit(1)

        config_path, config_source = _resolve_single_config_path(args.config)
        print(f"Config: {config_source}")
        try:
            config = load_single_config(config_path)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        set_language(config.get("language", DEFAULT_CONFIG["language"]))
        configure_local_state(use_local_state=config.get("use_local_state", False))
        targets = _resolve_action_targets(repo)
        if not targets:
            print("No actionable PRs found for this event; skipping.")
            return
        config["repositories"] = [
            {
                "repo": repo,
                "user_name": config.get("user_name"),
                "user_email": config.get("user_email"),
            }
        ]
        repos: list[RepositoryEntry] = config["repositories"]  # type: ignore[assignment]

        if args.dry_run:
            print("[DRY RUN MODE]")

        error_collector = ErrorCollector()
        global_modified_prs: set[tuple[str, int]] = set()
        global_committed_prs: set[tuple[str, int]] = set()
        global_claude_prs: set[tuple[str, int]] = set()
        for pr_number in targets:
            print(f"Processing PR: {repo} #{pr_number}")
            try:
                process_repo(
                    repos[0],
                    dry_run=args.dry_run,
                    silent=args.silent,
                    config=config,
                    global_modified_prs=global_modified_prs,
                    global_committed_prs=global_committed_prs,
                    global_claude_prs=global_claude_prs,
                    error_collector=error_collector,
                    target_pr_number=pr_number,
                )
            except KeyboardInterrupt:
                print("\nInterrupted by user")
                sys.exit(0)
            except ClaudeCommandFailedError as e:
                print(f"Error: {e}. Failing CI immediately.", file=sys.stderr)
                if e.stdout.strip():
                    print(f"  stdout: {e.stdout.strip()}", file=sys.stderr)
                if e.stderr.strip():
                    print(f"  stderr: {e.stderr.strip()}", file=sys.stderr)
                try:
                    edit_pr_label(
                        repo,
                        pr_number,
                        add=False,
                        label=REFIX_RUNNING_LABEL,
                        enabled_pr_label_keys=get_enabled_pr_label_keys(
                            config, DEFAULT_CONFIG
                        ),
                    )
                except Exception:
                    pass
                sys.exit(1)
            except Exception as e:
                print(f"Error processing {repo} PR #{pr_number}: {e}", file=sys.stderr)
                error_collector.add_repo_error(repo, str(e))
                try:
                    edit_pr_label(
                        repo,
                        pr_number,
                        add=False,
                        label=REFIX_RUNNING_LABEL,
                        enabled_pr_label_keys=get_enabled_pr_label_keys(
                            config, DEFAULT_CONFIG
                        ),
                    )
                except Exception:
                    pass

        print("\nDone!")
        if error_collector.has_errors:
            error_collector.print_summary()
            sys.exit(1)
        return

    elif args.repo is not None and args.pr is not None:
        config_path, config_source = _resolve_single_config_path(args.config)
        print(f"Config: {config_source}")
        try:
            config = load_single_config(config_path)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        set_language(config.get("language", DEFAULT_CONFIG["language"]))
        configure_local_state(use_local_state=config.get("use_local_state", False))
        config["repositories"] = [
            {
                "repo": args.repo,
                "user_name": config.get("user_name"),
                "user_email": config.get("user_email"),
            }
        ]
        repos = config["repositories"]  # type: ignore[assignment]

        print(f"Processing single PR: {args.repo} #{args.pr}")
        if args.dry_run:
            print("[DRY RUN MODE]")

        error_collector = ErrorCollector()
        try:
            process_repo(
                repos[0],
                dry_run=args.dry_run,
                silent=args.silent,
                config=config,
                error_collector=error_collector,
                target_pr_number=args.pr,
            )
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            sys.exit(0)
        except ClaudeCommandFailedError as e:
            print(f"Error: {e}. Failing CI immediately.", file=sys.stderr)
            if e.stdout.strip():
                print(f"  stdout: {e.stdout.strip()}", file=sys.stderr)
            if e.stderr.strip():
                print(f"  stderr: {e.stderr.strip()}", file=sys.stderr)
            try:
                edit_pr_label(
                    args.repo,
                    args.pr,
                    add=False,
                    label=REFIX_RUNNING_LABEL,
                    enabled_pr_label_keys=get_enabled_pr_label_keys(
                        config, DEFAULT_CONFIG
                    ),
                )
            except Exception:
                pass
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {args.repo} PR #{args.pr}: {e}", file=sys.stderr)
            error_collector.add_repo_error(args.repo, str(e))
            try:
                edit_pr_label(
                    args.repo,
                    args.pr,
                    add=False,
                    label=REFIX_RUNNING_LABEL,
                    enabled_pr_label_keys=get_enabled_pr_label_keys(
                        config, DEFAULT_CONFIG
                    ),
                )
            except Exception:
                pass

        print("\nDone!")
        if error_collector.has_errors:
            error_collector.print_summary()
            sys.exit(1)
        return

    batch_config_path, batch_config_source = _resolve_batch_config_path(args.config)
    print(f"Config: {batch_config_source}")
    try:
        config = load_config(batch_config_path)
        repos = expand_repositories(
            config["repositories"],
            include_fork_repositories=config.get(
                "include_fork_repositories",
                DEFAULT_CONFIG["include_fork_repositories"],
            ),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    set_language(config.get("language", DEFAULT_CONFIG["language"]))
    configure_local_state(use_local_state=config.get("use_local_state", False))

    if not repos:
        log_error(
            "No target repositories after expansion. Check your config.", title="config"
        )
        sys.exit(1)

    print(f"Processing {len(repos)} repository(ies)")
    if args.dry_run:
        print("[DRY RUN MODE]")

    commits_added_to: list[tuple[str, int, str]] = []
    global_modified_prs = set()
    global_committed_prs = set()
    global_claude_prs = set()
    global_backfilled_count: list[int] = [0]
    error_collector = ErrorCollector()
    for repo_info in repos:
        try:
            merged_config = merge_repo_config(config, repo_info)
            set_language(merged_config.get("language", DEFAULT_CONFIG["language"]))
            configure_local_state(
                use_local_state=merged_config.get("use_local_state", False)
            )
            effective_repo_info: RepositoryEntry = {
                "repo": repo_info["repo"],
                "user_name": merged_config.get("user_name"),
                "user_email": merged_config.get("user_email"),
            }
            results = process_repo(
                effective_repo_info,
                dry_run=args.dry_run,
                silent=args.silent,
                config=merged_config,
                global_modified_prs=global_modified_prs,
                global_committed_prs=global_committed_prs,
                global_claude_prs=global_claude_prs,
                global_backfilled_count=global_backfilled_count,
                error_collector=error_collector,
            )
            if results:
                commits_added_to.extend(results)
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            sys.exit(0)
        except ClaudeCommandFailedError as e:
            print(f"Error: {e}. Failing CI immediately.", file=sys.stderr)
            if e.stdout.strip():
                print(f"  stdout: {e.stdout.strip()}", file=sys.stderr)
            if e.stderr.strip():
                print(f"  stderr: {e.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            repo_name = str(repo_info.get("repo") or "<unknown-repo>")
            print(f"Error processing {repo_name}: {e}", file=sys.stderr)
            error_collector.add_repo_error(repo_name, str(e))
            continue

    if commits_added_to:
        print("\n" + "=" * SEPARATOR_LEN)
        print("コミットを追加した PR 一覧:")
        for repo, pr_number, new_commits in commits_added_to:
            print(f"  - {repo} PR #{pr_number}")
            for line in new_commits.splitlines():
                print(f"      {line}")
        print("=" * SEPARATOR_LEN)
    print("\nDone!")
    if error_collector.has_errors:
        error_collector.print_summary()
        sys.exit(1)


if __name__ == "__main__":
    main()
