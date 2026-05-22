"""PR ラベルの作成・設定・管理を行うモジュール。"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import cast
from urllib.parse import quote

from error_collector import ErrorCollector
from pr_reviewer import _fetch_classic_statuses_via_rest, _filter_check_runs
from state_manager import StateComment, update_workflow_status
from subprocess_helpers import SubprocessError, run_command, run_gh_api
from type_defs import CheckRunData, PRData

# --- ラベル定数 ---
REFIX_RUNNING_LABEL = "refix: running"
REFIX_DONE_LABEL = "refix: done"
REFIX_MERGED_LABEL = "refix: merged"
REFIX_AUTO_MERGE_REQUESTED_LABEL = "refix: auto-merge-requested"

PR_LABEL_KEY_TO_NAME: dict[str, str] = {
    "running": REFIX_RUNNING_LABEL,
    "done": REFIX_DONE_LABEL,
    "merged": REFIX_MERGED_LABEL,
    "auto_merge_requested": REFIX_AUTO_MERGE_REQUESTED_LABEL,
}
PR_LABEL_NAME_TO_KEY: dict[str, str] = {
    label_name: label_key for label_key, label_name in PR_LABEL_KEY_TO_NAME.items()
}
DEFAULT_ENABLED_PR_LABEL_KEYS: tuple[str, ...] = tuple(PR_LABEL_KEY_TO_NAME.keys())

# --- ラベルカラー ---
REFIX_RUNNING_LABEL_COLOR = "FBCA04"
REFIX_DONE_LABEL_COLOR = "0E8A16"
REFIX_MERGED_LABEL_COLOR = "5319E7"
REFIX_AUTO_MERGE_REQUESTED_LABEL_COLOR = "C2E0C6"

# --- CI 判定定数 ---
_SUCCESSFUL_CI_STATES = {"SUCCESS", "SKIPPED", "NEUTRAL"}


class CIStatus(Enum):
    """PR の CI チェック総合状態。"""

    SUCCESS = "success"  # 全チェック完了かつ成功扱い
    FAILURE = "failure"  # 完了したが成功でないチェックがある
    PENDING = "pending"  # まだ完了していないチェックがある
    UNAVAILABLE = "unavailable"  # 取得不能 / 猶予期間中（判定保留）


_CI_POLL_INTERVAL_SECONDS = 15


def _pr_ref(repo: str, pr_number: int) -> str:
    """ログ向けの PR 識別子を返す。"""
    return f"{repo} PR #{pr_number}"


def _resolve_enabled_pr_label_keys(
    enabled_pr_label_keys: set[str] | None = None,
) -> set[str]:
    """有効な PR ラベルキーセットを解決する。None の場合はデフォルトを返す。"""
    if enabled_pr_label_keys is None:
        return set(DEFAULT_ENABLED_PR_LABEL_KEYS)
    return {
        label_key
        for label_key in enabled_pr_label_keys
        if label_key in PR_LABEL_KEY_TO_NAME
    }


def _evaluate_ci_status(
    repo: str,
    pr_number: int,
    *,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
    error_collector: ErrorCollector | None = None,
) -> CIStatus:
    """REST API 経由で PR の CI チェック総合状態を判定する。

    Returns:
        CIStatus.SUCCESS: 全 CI チェックが完了かつ成功扱い
        CIStatus.FAILURE: 完了したが成功でないチェックがある（失敗確定）
        CIStatus.PENDING: まだ完了していないチェックがある
        CIStatus.UNAVAILABLE: CI 情報取得不能 / 猶予期間中（判定保留）
    """
    try:
        head_result = run_command(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}", "--jq", ".head.sha"],
            check=False,
            timeout=60,
        )
    except Exception:
        msg = (
            f"timed out fetching head SHA for {_pr_ref(repo, pr_number)}; "
            "skip refix: done labeling."
        )
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return CIStatus.UNAVAILABLE
    if head_result.returncode != 0 or not (
        head_sha := (head_result.stdout or "").strip()
    ):
        msg = (
            f"CI checks unavailable for {_pr_ref(repo, pr_number)}; "
            "skip refix: done labeling."
        )
        print(msg)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return CIStatus.UNAVAILABLE

    try:
        result = run_command(
            [
                "gh",
                "api",
                f"repos/{repo}/commits/{head_sha}/check-runs",
                "--paginate",
                "--slurp",
            ],
            check=False,
            timeout=60,
        )
    except Exception:
        msg = (
            f"timed out fetching check runs for {_pr_ref(repo, pr_number)}; "
            "skip refix: done labeling."
        )
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return CIStatus.UNAVAILABLE
    runs: list[CheckRunData] = []
    if result.returncode != 0:
        msg = (
            f"check-runs API failed for {_pr_ref(repo, pr_number)} "
            f"(exit {result.returncode}); skip refix: done labeling."
        )
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return CIStatus.UNAVAILABLE
    try:
        data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        msg = f"failed to parse CI check state for {_pr_ref(repo, pr_number)}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return CIStatus.UNAVAILABLE

    for page in data if isinstance(data, list) else [data]:
        if isinstance(page, dict):
            runs.extend(
                cast(
                    list[CheckRunData],
                    [r for r in (page.get("check_runs") or []) if isinstance(r, dict)],
                )
            )
    runs = _filter_check_runs(runs, repo)
    classic = _fetch_classic_statuses_via_rest(repo, head_sha)

    if not runs and not classic:
        if not ci_empty_as_success:
            print(
                f"CI checks unavailable for {_pr_ref(repo, pr_number)}; "
                "skip refix: done labeling."
            )
            return CIStatus.FAILURE
        try:
            commit_result = run_command(
                [
                    "gh",
                    "api",
                    f"repos/{repo}/commits/{head_sha}",
                    "--jq",
                    ".commit.committer.date",
                ],
                check=False,
                timeout=60,
            )
        except Exception:
            msg = (
                f"timed out fetching commit date for {_pr_ref(repo, pr_number)}; "
                "skip refix: done labeling."
            )
            print(f"Warning: {msg}", file=sys.stderr)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return CIStatus.UNAVAILABLE
        if commit_result.returncode != 0 or not (
            date_str := (commit_result.stdout or "").strip()
        ):
            msg = (
                f"CI checks unavailable for {_pr_ref(repo, pr_number)}; "
                "skip refix: done labeling."
            )
            print(msg)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return CIStatus.UNAVAILABLE
        try:
            if date_str.startswith('"') and date_str.endswith('"'):
                date_str = json.loads(date_str)
            commit_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if commit_dt.tzinfo is None:
                commit_dt = commit_dt.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - commit_dt
            if elapsed < timedelta(minutes=ci_empty_grace_minutes):
                print(
                    f"CI checks unavailable for {_pr_ref(repo, pr_number)} "
                    f"(empty, commit < {ci_empty_grace_minutes}min ago); skip refix: done labeling."
                )
                return CIStatus.UNAVAILABLE
        except (ValueError, TypeError):
            msg = (
                f"CI checks unavailable for {_pr_ref(repo, pr_number)}; "
                "skip refix: done labeling."
            )
            print(msg)
            if error_collector:
                error_collector.add_pr_error(repo, pr_number, msg)
            return CIStatus.UNAVAILABLE
        print(
            f"{_pr_ref(repo, pr_number)}: no CI checks, "
            f"commit >{ci_empty_grace_minutes}min ago; treat as success."
        )
        return CIStatus.SUCCESS

    conclusions: list[str] = []
    has_pending = False
    for r in runs:
        if not isinstance(r, dict):
            continue
        status = str(r.get("status") or "").upper()
        conclusion = str(r.get("conclusion") or "").upper()
        if status != "COMPLETED":
            has_pending = True
            continue
        conclusions.append(conclusion)

    for cs in classic:
        if not isinstance(cs, dict):
            continue
        state = str(cs.get("conclusion") or cs.get("state") or "").upper()
        if not state or state == "PENDING":
            has_pending = True
            continue
        conclusions.append(state)

    has_failure = any(c not in _SUCCESSFUL_CI_STATES for c in conclusions)
    if has_failure:
        print(
            f"CI checks not all successful for {_pr_ref(repo, pr_number)}: "
            f"{', '.join(conclusions)}"
        )
        return CIStatus.FAILURE
    if has_pending:
        print(f"CI checks still in progress for {_pr_ref(repo, pr_number)}.")
        return CIStatus.PENDING
    if not conclusions:
        print(
            f"CI checks unavailable for {_pr_ref(repo, pr_number)}; "
            "skip refix: done labeling."
        )
        return CIStatus.UNAVAILABLE
    return CIStatus.SUCCESS


def _wait_for_ci_status(
    repo: str,
    pr_number: int,
    *,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
    ci_pending_wait_seconds: int = 0,
    error_collector: ErrorCollector | None = None,
) -> CIStatus:
    """CI が PENDING の間だけ予算内で再評価する。PENDING 以外になれば即返す。

    ci_pending_wait_seconds=0 のとき deadline は現在時刻となり、PENDING なら即
    CIStatus.PENDING を返す（従来のシングルショット動作）。
    """
    deadline = time.monotonic() + max(0, ci_pending_wait_seconds)
    while True:
        status = _evaluate_ci_status(
            repo,
            pr_number,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            error_collector=error_collector,
        )
        if status is not CIStatus.PENDING:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return CIStatus.PENDING
        time.sleep(min(_CI_POLL_INTERVAL_SECONDS, remaining))


def _ensure_repo_label_exists(
    repo: str,
    label: str,
    *,
    color: str,
    description: str,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """リポジトリにラベルが存在しなければ作成する。"""
    encoded_label = quote(label, safe="")
    get_cmd = ["gh", "api", f"repos/{repo}/labels/{encoded_label}"]
    try:
        get_result = run_command(get_cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to check label '{label}' on {repo}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return False
    if get_result.returncode == 0:
        return True

    stderr_lower = (get_result.stderr or "").lower()
    not_found = "not found" in stderr_lower or "404" in stderr_lower
    if not not_found:
        msg = f"failed to verify label '{label}' on {repo}: {(get_result.stderr or '').strip()}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return False

    create_cmd = [
        "gh",
        "api",
        f"repos/{repo}/labels",
        "-X",
        "POST",
        "-f",
        f"name={label}",
        "-f",
        f"color={color}",
        "-f",
        f"description={description}",
    ]
    try:
        create_result = run_command(create_cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to create label '{label}' in {repo}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return False
    if create_result.returncode == 0:
        print(f"Created missing label '{label}' in {repo}")
        return True

    create_stderr = (create_result.stderr or "").lower()
    if "already_exists" in create_stderr or "already exists" in create_stderr:
        return True

    msg = f"failed to create label '{label}' in {repo}: {(create_result.stderr or '').strip()}"
    print(f"Warning: {msg}", file=sys.stderr)
    if error_collector:
        error_collector.add_repo_error(repo, msg)
    return False


def _ensure_refix_labels(
    repo: str,
    *,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> None:
    """必要な refix ラベルをリポジトリに作成する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if "running" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_RUNNING_LABEL,
            color=REFIX_RUNNING_LABEL_COLOR,
            description="Refix is currently processing self-review and fixes.",
            error_collector=error_collector,
        )
    if "done" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_DONE_LABEL,
            color=REFIX_DONE_LABEL_COLOR,
            description="Refix finished self-review/fix for the current head.",
            error_collector=error_collector,
        )
    if "merged" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_MERGED_LABEL,
            color=REFIX_MERGED_LABEL_COLOR,
            description="PR has been merged after Refix auto-merge.",
            error_collector=error_collector,
        )
    if "auto_merge_requested" in enabled:
        _ensure_repo_label_exists(
            repo,
            REFIX_AUTO_MERGE_REQUESTED_LABEL,
            color=REFIX_AUTO_MERGE_REQUESTED_LABEL_COLOR,
            description="Refix has requested auto-merge for this PR.",
            error_collector=error_collector,
        )


def edit_pr_label(
    repo: str,
    pr_number: int,
    *,
    add: bool,
    label: str,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """PR にラベルを追加または削除する。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    label_key = PR_LABEL_NAME_TO_KEY.get(label)
    if label_key is not None and label_key not in enabled:
        return False

    label_arg = "--add-label" if add else "--remove-label"
    cmd = [
        "gh",
        "pr",
        "edit",
        str(pr_number),
        "--repo",
        repo,
        label_arg,
        label,
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        action = "add" if add else "remove"
        msg = f"failed to {action} label '{label}' on {_pr_ref(repo, pr_number)}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if result.returncode == 0:
        return True

    stderr_lower = (result.stderr or "").lower()
    if (
        not add
        and "label" in stderr_lower
        and ("not found" in stderr_lower or "does not have" in stderr_lower)
    ):
        return True

    action = "add" if add else "remove"
    msg = (
        f"failed to {action} label '{label}' on {_pr_ref(repo, pr_number)}: "
        f"{(result.stderr or '').strip()}"
    )
    print(f"Warning: {msg}", file=sys.stderr)
    if error_collector:
        error_collector.add_pr_error(repo, pr_number, msg)
    return False


def _pr_has_label(pr_data: PRData, label_name: str) -> bool:
    """PR に指定ラベルが付いているか判定する。"""
    labels = pr_data.get("labels", [])
    if not isinstance(labels, list):
        return False
    for label in labels:
        if isinstance(label, dict) and str(label.get("name", "")).strip() == label_name:
            return True
    return False


def resolve_workflow_status(state_comment: StateComment, pr_data: PRData) -> str:
    """コメントからステータスを取得する（ラベルへのフォールバックなし）。"""
    return state_comment.workflow_status or ""


def set_pr_running_label(
    repo: str,
    pr_number: int,
    *,
    pr_data: PRData | None = None,
    enabled_pr_label_keys: set[str] | None = None,
    use_pr_labels: bool = True,
    state_comment: StateComment | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """refix: running を設定し、refix: done を削除する。"""
    _workflow_updated = False
    try:
        update_workflow_status(
            repo, pr_number, "running", _preloaded_state=state_comment
        )
        _workflow_updated = True
    except Exception as e:
        print(
            f"Warning: failed to update workflow status for {_pr_ref(repo, pr_number)}: {e}",
            file=sys.stderr,
        )
    if not use_pr_labels:
        return _workflow_updated
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    running_enabled = "running" in enabled
    done_enabled = "done" in enabled
    if not running_enabled and not done_enabled:
        return _workflow_updated
    if (
        pr_data
        and (not running_enabled or _pr_has_label(pr_data, REFIX_RUNNING_LABEL))
        and (not done_enabled or not _pr_has_label(pr_data, REFIX_DONE_LABEL))
    ):
        return _workflow_updated
    _ensure_refix_labels(
        repo, enabled_pr_label_keys=enabled, error_collector=error_collector
    )
    changed = False
    if done_enabled and (pr_data is None or _pr_has_label(pr_data, REFIX_DONE_LABEL)):
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_DONE_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
    if running_enabled and (
        pr_data is None or not _pr_has_label(pr_data, REFIX_RUNNING_LABEL)
    ):
        if edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_RUNNING_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
    return changed


def _set_pr_done_label(
    repo: str,
    pr_number: int,
    *,
    pr_data: PRData | None = None,
    enabled_pr_label_keys: set[str] | None = None,
    use_pr_labels: bool = True,
    state_comment: StateComment | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """refix: done を設定し、refix: running を削除する。"""
    try:
        update_workflow_status(repo, pr_number, "done", _preloaded_state=state_comment)
    except Exception as e:
        print(
            f"Warning: failed to update workflow status for {_pr_ref(repo, pr_number)}: {e}",
            file=sys.stderr,
        )
    if not use_pr_labels:
        return False
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    done_enabled = "done" in enabled
    running_enabled = "running" in enabled
    if not done_enabled and not running_enabled:
        return False
    if (
        pr_data
        and (not done_enabled or _pr_has_label(pr_data, REFIX_DONE_LABEL))
        and (not running_enabled or not _pr_has_label(pr_data, REFIX_RUNNING_LABEL))
    ):
        return False
    _ensure_refix_labels(
        repo, enabled_pr_label_keys=enabled, error_collector=error_collector
    )
    changed = False
    if running_enabled and (
        pr_data is None or _pr_has_label(pr_data, REFIX_RUNNING_LABEL)
    ):
        if edit_pr_label(
            repo,
            pr_number,
            add=False,
            label=REFIX_RUNNING_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
    if done_enabled and (
        pr_data is None or not _pr_has_label(pr_data, REFIX_DONE_LABEL)
    ):
        if edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_DONE_LABEL,
            enabled_pr_label_keys=enabled,
            error_collector=error_collector,
        ):
            changed = True
    return changed


def _set_pr_merged_label(
    repo: str,
    pr_number: int,
    *,
    enabled_pr_label_keys: set[str] | None = None,
    use_pr_labels: bool = True,
    state_comment: StateComment | None = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """refix: merged を設定し、refix: running / refix: auto-merge-requested を削除する。"""
    try:
        update_workflow_status(
            repo, pr_number, "merged", _preloaded_state=state_comment
        )
    except Exception as e:
        print(
            f"Warning: failed to update workflow status for {_pr_ref(repo, pr_number)}: {e}",
            file=sys.stderr,
        )
    if not use_pr_labels:
        return False
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if not (
        "running" in enabled or "auto_merge_requested" in enabled or "merged" in enabled
    ):
        return False
    changed = False
    _ensure_refix_labels(
        repo, enabled_pr_label_keys=enabled, error_collector=error_collector
    )
    if edit_pr_label(
        repo,
        pr_number,
        add=False,
        label=REFIX_RUNNING_LABEL,
        enabled_pr_label_keys=enabled,
        error_collector=error_collector,
    ):
        changed = True
    if edit_pr_label(
        repo,
        pr_number,
        add=False,
        label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
        enabled_pr_label_keys=enabled,
        error_collector=error_collector,
    ):
        changed = True
    if edit_pr_label(
        repo,
        pr_number,
        add=True,
        label=REFIX_MERGED_LABEL,
        enabled_pr_label_keys=enabled,
        error_collector=error_collector,
    ):
        changed = True
    return changed


def _mark_pr_merged_label_if_needed(
    repo: str,
    pr_number: int,
    *,
    enabled_pr_label_keys: set[str] | None = None,
    use_pr_labels: bool = True,
    state_comment: "StateComment | None" = None,
    error_collector: ErrorCollector | None = None,
) -> bool:
    """マージ済みの PR に refix: merged ラベルを追加する。"""
    from state_manager import load_state_comment as _load_state_comment

    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if use_pr_labels and not ({"running", "auto_merge_requested", "merged"} & enabled):
        return False

    if state_comment is None:
        try:
            state_comment = _load_state_comment(repo, pr_number)
        except Exception:
            state_comment = None

    cmd = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        "mergedAt,labels",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to inspect merge state for {_pr_ref(repo, pr_number)}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if result.returncode != 0:
        msg = (
            f"failed to inspect merge state for {_pr_ref(repo, pr_number)}: "
            f"{(result.stderr or '').strip()}"
        )
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    try:
        pr_data = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        msg = f"failed to parse merge state for {_pr_ref(repo, pr_number)}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False
    if not isinstance(pr_data, dict):
        return False

    pr_data_typed = cast(PRData, pr_data)
    merged_at = str(pr_data_typed.get("mergedAt") or "").strip()
    if not merged_at:
        return False

    sc_status = state_comment.workflow_status if state_comment else ""
    if sc_status not in ("done", "auto_merge_requested", "merged"):
        return False

    if _pr_has_label(pr_data_typed, REFIX_MERGED_LABEL):
        return False

    print(f"{_pr_ref(repo, pr_number)} is merged; adding {REFIX_MERGED_LABEL} label.")
    return _set_pr_merged_label(
        repo,
        pr_number,
        use_pr_labels=use_pr_labels,
        enabled_pr_label_keys=enabled,
        error_collector=error_collector,
    )


def backfill_merged_labels(
    repo: str,
    *,
    limit: int = 100,
    enabled_pr_label_keys: set[str] | None = None,
    error_collector: ErrorCollector | None = None,
) -> int:
    """最近マージされた PR に refix: merged ラベルをバックフィルする。"""
    from state_manager import load_state_comment as _load_state_comment

    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)
    if "merged" not in enabled:
        return 0
    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "merged",
        "--limit",
        str(limit),
        "--json",
        "number",
    ]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        msg = f"failed to list merged PRs for {repo}: {exc}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return 0
    if result.returncode != 0:
        msg = f"failed to list merged PRs for {repo}: {(result.stderr or '').strip()}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return 0
    try:
        prs = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        msg = f"failed to parse merged PR list for {repo}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_repo_error(repo, msg)
        return 0
    if not isinstance(prs, list):
        return 0

    count = 0
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        pr_number = pr.get("number")
        if not isinstance(pr_number, int):
            continue
        try:
            sc = _load_state_comment(repo, pr_number)
        except Exception:
            sc = None
        if sc is None or sc.workflow_status not in (
            "done",
            "auto_merge_requested",
            "merged",
        ):
            continue
        marked = _mark_pr_merged_label_if_needed(
            repo,
            pr_number,
            enabled_pr_label_keys=enabled,
            state_comment=sc,
            error_collector=error_collector,
        )
        if marked:
            count += 1
    if count:
        print(f"Backfilled {REFIX_MERGED_LABEL} on {count} merged PR(s) in {repo}.")
    return count


_MERGE_METHOD_FLAG: dict[str, str] = {
    "merge": "--merge",
    "squash": "--squash",
    "rebase": "--rebase",
}
_MERGE_METHOD_PRIORITY = ("merge", "squash", "rebase")


def _get_allowed_merge_methods(repo: str) -> list[str] | None:
    """リポジトリの許可マージメソッドを API から取得する。"""
    try:
        data = run_gh_api(f"repos/{repo}")
    except SubprocessError:
        return None
    if not isinstance(data, dict):
        return None
    allowed = []
    for method, key in [
        ("merge", "allow_merge_commit"),
        ("squash", "allow_squash_merge"),
        ("rebase", "allow_rebase_merge"),
    ]:
        if data.get(key):
            allowed.append(method)
    return allowed if allowed else None


def _try_gh_merge(
    repo: str,
    pr_number: int,
    method: str,
) -> tuple[bool, str]:
    """指定メソッドで gh pr merge を実行する。(success, combined_lower) を返す。"""
    flag = _MERGE_METHOD_FLAG[method]
    cmd = ["gh", "pr", "merge", str(pr_number), "--repo", repo, "--auto", flag]
    try:
        result = run_command(cmd, check=False)
    except SubprocessError as exc:
        return False, str(exc).lower()
    stderr_text = (result.stderr or "").strip()
    stdout_text = (result.stdout or "").strip()
    combined_lower = f"{stdout_text}\n{stderr_text}".lower()
    if result.returncode == 0:
        return True, combined_lower
    return False, combined_lower


def _trigger_pr_auto_merge(
    repo: str,
    pr_number: int,
    *,
    merge_method: str = "auto",
    enabled_pr_label_keys: set[str] | None = None,
    use_pr_labels: bool = True,
    error_collector: ErrorCollector | None = None,
) -> tuple[bool, bool]:
    """auto-merge を要求する。(merge_state_reached, modified) を返す。"""
    enabled = _resolve_enabled_pr_label_keys(enabled_pr_label_keys)

    def _on_success() -> tuple[bool, bool]:
        print(f"Auto-merge requested for {_pr_ref(repo, pr_number)}.")
        if not use_pr_labels:
            return True, False
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
        modified = edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
        return True, modified

    def _on_already_merged() -> tuple[bool, bool]:
        print(f"{_pr_ref(repo, pr_number)} is already merged.")
        if not use_pr_labels:
            return True, False
        _ensure_refix_labels(
            repo, enabled_pr_label_keys=enabled, error_collector=error_collector
        )
        modified = edit_pr_label(
            repo,
            pr_number,
            add=True,
            label=REFIX_AUTO_MERGE_REQUESTED_LABEL,
            enabled_pr_label_keys=enabled_pr_label_keys,
            error_collector=error_collector,
        )
        return True, modified

    if merge_method == "auto":
        allowed = _get_allowed_merge_methods(repo)
        if allowed is not None:
            methods_to_try = [m for m in _MERGE_METHOD_PRIORITY if m in allowed]
            if not methods_to_try:
                methods_to_try = list(_MERGE_METHOD_PRIORITY)
        else:
            methods_to_try = list(_MERGE_METHOD_PRIORITY)

        last_combined_lower = ""
        for method in methods_to_try:
            success, combined_lower = _try_gh_merge(repo, pr_number, method)
            if success:
                return _on_success()
            last_combined_lower = combined_lower
            if "already merged" in combined_lower:
                return _on_already_merged()
            if "merge method" in combined_lower or "not allowed" in combined_lower:
                continue
            break

        details = last_combined_lower or "unknown error"
        msg = f"failed to auto-merge {_pr_ref(repo, pr_number)}: {details}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False, False
    else:
        success, combined_lower = _try_gh_merge(repo, pr_number, merge_method)
        if success:
            return _on_success()
        if "already merged" in combined_lower:
            return _on_already_merged()
        details = combined_lower or "unknown error"
        msg = f"failed to auto-merge {_pr_ref(repo, pr_number)}: {details}"
        print(f"Warning: {msg}", file=sys.stderr)
        if error_collector:
            error_collector.add_pr_error(repo, pr_number, msg)
        return False, False


def update_done_label_if_completed(
    *,
    repo: str,
    pr_number: int,
    has_self_review_target: bool,
    self_review_ran: bool,
    fix_added_commits: bool,
    fix_failed: bool,
    state_saved: bool,
    commits_by_phase: list[str],
    pr_data: PRData,
    dry_run: bool,
    auto_merge_enabled: bool = False,
    merge_method: str = "auto",
    enabled_pr_label_keys: set[str] | None = None,
    ci_empty_as_success: bool = True,
    ci_empty_grace_minutes: int = 5,
    ci_pending_wait_seconds: int = 0,
    use_pr_labels: bool = True,
    state_comment: StateComment | None = None,
    error_collector: ErrorCollector | None = None,
) -> tuple[bool, bool]:
    """完了条件を満たした場合に refix: done ラベルを設定する。

    Returns:
        (label_was_updated, ci_grace_pending)
    """
    if dry_run:
        return False, False

    is_completed = True
    block_reasons: list[str] = []

    if fix_failed:
        is_completed = False
        block_reasons.append("fix failed")
    if not state_saved:
        is_completed = False
        block_reasons.append("state not saved")
    if commits_by_phase:
        is_completed = False
        block_reasons.append(
            f"commits pushed this run: {len(commits_by_phase)} phase(s)"
        )
    if has_self_review_target and fix_added_commits:
        is_completed = False
        block_reasons.append("fix phase added commits this run")

    ci_grace_pending = False
    if is_completed:
        ci_status = _wait_for_ci_status(
            repo,
            pr_number,
            ci_empty_as_success=ci_empty_as_success,
            ci_empty_grace_minutes=ci_empty_grace_minutes,
            ci_pending_wait_seconds=ci_pending_wait_seconds,
            error_collector=error_collector,
        )
        if ci_status is CIStatus.SUCCESS:
            pass  # is_completed は True のまま
        elif ci_status is CIStatus.FAILURE:
            is_completed = False
            block_reasons.append("CI checks not all successful")
        elif ci_status is CIStatus.PENDING:
            is_completed = False
            ci_grace_pending = True
            block_reasons.append("CI checks still in progress")
        else:  # CIStatus.UNAVAILABLE
            is_completed = False
            ci_grace_pending = True
            block_reasons.append("CI checks unavailable")

    if is_completed:
        print(
            f"{_pr_ref(repo, pr_number)} meets completion conditions; "
            f"switching label to {REFIX_DONE_LABEL}."
        )
        current_pr_data = None if self_review_ran else pr_data
        done_changed = _set_pr_done_label(
            repo,
            pr_number,
            pr_data=current_pr_data,
            enabled_pr_label_keys=enabled_pr_label_keys,
            use_pr_labels=use_pr_labels,
            state_comment=state_comment,
            error_collector=error_collector,
        )
        merge_triggered = False
        if auto_merge_enabled:
            merge_state_reached, label_modified = _trigger_pr_auto_merge(
                repo,
                pr_number,
                merge_method=merge_method,
                enabled_pr_label_keys=enabled_pr_label_keys,
                use_pr_labels=use_pr_labels,
                error_collector=error_collector,
            )
            if merge_state_reached:
                _mark_pr_merged_label_if_needed(
                    repo,
                    pr_number,
                    enabled_pr_label_keys=enabled_pr_label_keys,
                    use_pr_labels=use_pr_labels,
                    error_collector=error_collector,
                )
            merge_triggered = label_modified
        return done_changed or merge_triggered, ci_grace_pending

    if block_reasons:
        print(
            f"{_pr_ref(repo, pr_number)} is not completed yet "
            f"({', '.join(block_reasons)}); "
            f"switching label to {REFIX_RUNNING_LABEL}."
        )
    else:
        print(
            f"{_pr_ref(repo, pr_number)} is not completed yet; "
            f"switching label to {REFIX_RUNNING_LABEL}."
        )
    running_changed = set_pr_running_label(
        repo,
        pr_number,
        pr_data=pr_data,
        enabled_pr_label_keys=enabled_pr_label_keys,
        use_pr_labels=use_pr_labels,
        state_comment=state_comment,
        error_collector=error_collector,
    )
    return running_changed, ci_grace_pending
