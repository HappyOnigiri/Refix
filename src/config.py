"""設定ファイル（.refix.yaml）の読み込みと検証を行うモジュール。"""

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from state_manager import ensure_valid_state_timezone

# --- デフォルト設定 ---
DEFAULT_CONFIG: dict[str, Any] = {
    "models": {
        "summarize": "haiku",
        "fix": "sonnet",
    },
    "ci_log_max_lines": 120,
    "execution_report": False,
    "auto_merge": False,
    "enabled_pr_labels": ["running", "done", "merged", "auto_merge_requested"],
    "coderabbit_auto_resume": False,
    "coderabbit_auto_resume_max_per_run": 1,
    "process_draft_prs": False,
    "state_comment_timezone": "JST",
    "max_modified_prs_per_run": 0,
    "max_committed_prs_per_run": 2,
    "max_claude_prs_per_run": 0,
    "ci_empty_as_success": True,
    "ci_empty_grace_minutes": 5,
    "repositories": [],
}

# --- 許可キー定義 ---
ALLOWED_CONFIG_TOP_LEVEL_KEYS = {
    "models",
    "ci_log_max_lines",
    "execution_report",
    "auto_merge",
    "enabled_pr_labels",
    "coderabbit_auto_resume",
    "coderabbit_auto_resume_max_per_run",
    "process_draft_prs",
    "state_comment_timezone",
    "max_modified_prs_per_run",
    "max_committed_prs_per_run",
    "max_claude_prs_per_run",
    "ci_empty_as_success",
    "ci_empty_grace_minutes",
    "repositories",
}
ALLOWED_MODEL_KEYS = {"summarize", "fix"}
ALLOWED_REPOSITORY_KEYS = {"repo", "user_name", "user_email"}

# --- PR ラベルキー定義（config 用） ---
PR_LABEL_KEYS = ("running", "done", "merged", "auto_merge_requested")


def _warn_unknown_config_keys(
    config_section: dict[str, Any], allowed_keys: set[str]
) -> None:
    unknown_keys = sorted(set(config_section.keys()) - allowed_keys)
    for key in unknown_keys:
        print(f"Warning: Unknown key '{key}' found in config.", file=sys.stderr)


def _normalize_auto_resume_state(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
    auto_resume_run_state: dict[str, int] | None = None,
) -> dict[str, int]:
    """CodeRabbit の auto-resume 状態を正規化する。"""
    raw_max_per_run = runtime_config.get(
        "coderabbit_auto_resume_max_per_run",
        default_config["coderabbit_auto_resume_max_per_run"],
    )
    if (
        isinstance(raw_max_per_run, int)
        and not isinstance(raw_max_per_run, bool)
        and raw_max_per_run >= 1
    ):
        max_per_run = raw_max_per_run
    else:
        max_per_run = default_config["coderabbit_auto_resume_max_per_run"]

    if auto_resume_run_state is None:
        auto_resume_run_state = {"posted": 0, "max_per_run": max_per_run}
    else:
        auto_resume_run_state["posted"] = int(auto_resume_run_state.get("posted", 0))
        auto_resume_run_state["max_per_run"] = max_per_run

    return auto_resume_run_state


def get_process_draft_prs(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
) -> bool:
    """process_draft_prs フラグを取得する。"""
    return bool(
        runtime_config.get("process_draft_prs", default_config["process_draft_prs"])
    )


def get_enabled_pr_label_keys(
    runtime_config: dict[str, Any],
    default_config: dict[str, Any],
) -> set[str]:
    """有効な PR ラベルキーの集合を取得する。"""
    configured_labels = runtime_config.get(
        "enabled_pr_labels", default_config["enabled_pr_labels"]
    )
    if not isinstance(configured_labels, list):
        configured_labels = default_config["enabled_pr_labels"]
    return {
        label_key
        for label_key in configured_labels
        if isinstance(label_key, str) and label_key in PR_LABEL_KEYS
    }


def load_config(filepath: str) -> dict[str, Any]:
    """YAML 設定ファイルを読み込み、検証する。"""
    try:
        config_text = Path(filepath).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Error: config file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    try:
        parsed = yaml.safe_load(config_text)
    except yaml.YAMLError as e:
        print(f"Error: failed to parse YAML config '{filepath}': {e}", file=sys.stderr)
        sys.exit(1)

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        print("Error: config root must be a mapping/object.", file=sys.stderr)
        sys.exit(1)

    _warn_unknown_config_keys(parsed, ALLOWED_CONFIG_TOP_LEVEL_KEYS)

    config: dict[str, Any] = {
        "models": dict(DEFAULT_CONFIG["models"]),
        "ci_log_max_lines": DEFAULT_CONFIG["ci_log_max_lines"],
        "execution_report": DEFAULT_CONFIG["execution_report"],
        "auto_merge": DEFAULT_CONFIG["auto_merge"],
        "enabled_pr_labels": list(DEFAULT_CONFIG["enabled_pr_labels"]),
        "coderabbit_auto_resume": DEFAULT_CONFIG["coderabbit_auto_resume"],
        "coderabbit_auto_resume_max_per_run": DEFAULT_CONFIG[
            "coderabbit_auto_resume_max_per_run"
        ],
        "process_draft_prs": DEFAULT_CONFIG["process_draft_prs"],
        "state_comment_timezone": DEFAULT_CONFIG["state_comment_timezone"],
        "max_modified_prs_per_run": DEFAULT_CONFIG["max_modified_prs_per_run"],
        "max_committed_prs_per_run": DEFAULT_CONFIG["max_committed_prs_per_run"],
        "max_claude_prs_per_run": DEFAULT_CONFIG["max_claude_prs_per_run"],
        "ci_empty_as_success": DEFAULT_CONFIG["ci_empty_as_success"],
        "ci_empty_grace_minutes": DEFAULT_CONFIG["ci_empty_grace_minutes"],
        "repositories": [],
    }

    models = parsed.get("models")
    if models is not None:
        if not isinstance(models, dict):
            print("Error: models must be a mapping/object.", file=sys.stderr)
            sys.exit(1)
        _warn_unknown_config_keys(models, ALLOWED_MODEL_KEYS)

        summarize_model = models.get("summarize")
        if summarize_model is not None:
            if not isinstance(summarize_model, str) or not summarize_model.strip():
                print(
                    "Error: models.summarize must be a non-empty string.",
                    file=sys.stderr,
                )
                sys.exit(1)
            config["models"]["summarize"] = summarize_model.strip()

        fix_model = models.get("fix")
        if fix_model is not None:
            if not isinstance(fix_model, str) or not fix_model.strip():
                print("Error: models.fix must be a non-empty string.", file=sys.stderr)
                sys.exit(1)
            config["models"]["fix"] = fix_model.strip()

    ci_log_max_lines = parsed.get("ci_log_max_lines")
    if ci_log_max_lines is not None:
        try:
            config["ci_log_max_lines"] = max(20, int(ci_log_max_lines))
        except (TypeError, ValueError):
            print("Error: ci_log_max_lines must be an integer.", file=sys.stderr)
            sys.exit(1)

    execution_report = parsed.get("execution_report")
    if execution_report is not None:
        if not isinstance(execution_report, bool):
            print("Error: execution_report must be a boolean.", file=sys.stderr)
            sys.exit(1)
        config["execution_report"] = execution_report

    auto_merge = parsed.get("auto_merge")
    if auto_merge is not None:
        if not isinstance(auto_merge, bool):
            print("Error: auto_merge must be a boolean.", file=sys.stderr)
            sys.exit(1)
        config["auto_merge"] = auto_merge

    enabled_pr_labels = parsed.get("enabled_pr_labels")
    if enabled_pr_labels is not None:
        if not isinstance(enabled_pr_labels, list):
            print("Error: enabled_pr_labels must be a list.", file=sys.stderr)
            sys.exit(1)
        normalized_enabled_labels: list[str] = []
        seen_enabled_labels: set[str] = set()
        allowed_label_keys = ", ".join(sorted(PR_LABEL_KEYS))
        for index, label_key in enumerate(enabled_pr_labels):
            if not isinstance(label_key, str) or not label_key.strip():
                print(
                    f"Error: enabled_pr_labels[{index}] must be a non-empty string.",
                    file=sys.stderr,
                )
                sys.exit(1)
            normalized_label_key = label_key.strip()
            if normalized_label_key not in PR_LABEL_KEYS:
                print(
                    f"Error: enabled_pr_labels[{index}] must be one of: {allowed_label_keys}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if normalized_label_key in seen_enabled_labels:
                continue
            seen_enabled_labels.add(normalized_label_key)
            normalized_enabled_labels.append(normalized_label_key)
        if "merged" in seen_enabled_labels and not (
            seen_enabled_labels & {"running", "done", "auto_merge_requested"}
        ):
            allowed_merge_sub_keys = ", ".join(
                sorted({"running", "done", "auto_merge_requested"})
            )
            print(
                f'Error: enabled_pr_labels includes "merged" but none of: {allowed_merge_sub_keys}. '
                f'At least one of these must be included alongside "merged".',
                file=sys.stderr,
            )
            sys.exit(1)
        config["enabled_pr_labels"] = normalized_enabled_labels

    coderabbit_auto_resume = parsed.get("coderabbit_auto_resume")
    if coderabbit_auto_resume is not None:
        if not isinstance(coderabbit_auto_resume, bool):
            print("Error: coderabbit_auto_resume must be a boolean.", file=sys.stderr)
            sys.exit(1)
        config["coderabbit_auto_resume"] = coderabbit_auto_resume

    coderabbit_auto_resume_max_per_run = parsed.get(
        "coderabbit_auto_resume_max_per_run"
    )
    if coderabbit_auto_resume_max_per_run is not None:
        if (
            not isinstance(coderabbit_auto_resume_max_per_run, int)
            or isinstance(coderabbit_auto_resume_max_per_run, bool)
            or coderabbit_auto_resume_max_per_run < 1
        ):
            print(
                "Error: coderabbit_auto_resume_max_per_run must be an integer >= 1.",
                file=sys.stderr,
            )
            sys.exit(1)
        config["coderabbit_auto_resume_max_per_run"] = (
            coderabbit_auto_resume_max_per_run
        )

    process_draft_prs = parsed.get("process_draft_prs")
    if process_draft_prs is not None:
        if not isinstance(process_draft_prs, bool):
            print("Error: process_draft_prs must be a boolean.", file=sys.stderr)
            sys.exit(1)
        config["process_draft_prs"] = process_draft_prs

    state_comment_timezone = parsed.get("state_comment_timezone")
    if state_comment_timezone is not None:
        if (
            not isinstance(state_comment_timezone, str)
            or not state_comment_timezone.strip()
        ):
            print(
                "Error: state_comment_timezone must be a non-empty string.",
                file=sys.stderr,
            )
            sys.exit(1)
        timezone_name = state_comment_timezone.strip()
        try:
            ensure_valid_state_timezone(timezone_name)
        except ValueError:
            print(
                "Error: state_comment_timezone must be a valid IANA timezone (e.g. Asia/Tokyo) or JST.",
                file=sys.stderr,
            )
            sys.exit(1)
        config["state_comment_timezone"] = timezone_name

    for limit_key in (
        "max_modified_prs_per_run",
        "max_committed_prs_per_run",
        "max_claude_prs_per_run",
    ):
        raw_value = parsed.get(limit_key)
        if raw_value is not None:
            if isinstance(raw_value, bool):
                print(
                    f"Error: {limit_key} must be a non-negative integer.",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                int_value = int(raw_value)
            except (TypeError, ValueError):
                print(
                    f"Error: {limit_key} must be a non-negative integer.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if int_value < 0:
                print(
                    f"Error: {limit_key} must be a non-negative integer.",
                    file=sys.stderr,
                )
                sys.exit(1)
            config[limit_key] = int_value

    ci_empty_as_success = parsed.get("ci_empty_as_success")
    if ci_empty_as_success is not None:
        if not isinstance(ci_empty_as_success, bool):
            print("Error: ci_empty_as_success must be a boolean.", file=sys.stderr)
            sys.exit(1)
        config["ci_empty_as_success"] = ci_empty_as_success

    ci_empty_grace_minutes = parsed.get("ci_empty_grace_minutes")
    if ci_empty_grace_minutes is not None:
        if isinstance(ci_empty_grace_minutes, bool) or isinstance(
            ci_empty_grace_minutes, float
        ):
            print(
                "Error: ci_empty_grace_minutes must be a non-negative integer.",
                file=sys.stderr,
            )
            sys.exit(1)
        if isinstance(ci_empty_grace_minutes, int):
            grace_int = ci_empty_grace_minutes
        elif (
            isinstance(ci_empty_grace_minutes, str) and ci_empty_grace_minutes.isdigit()
        ):
            grace_int = int(ci_empty_grace_minutes)
        else:
            print(
                "Error: ci_empty_grace_minutes must be a non-negative integer.",
                file=sys.stderr,
            )
            sys.exit(1)
        if grace_int < 0:
            print(
                "Error: ci_empty_grace_minutes must be a non-negative integer.",
                file=sys.stderr,
            )
            sys.exit(1)
        config["ci_empty_grace_minutes"] = grace_int

    repositories = parsed.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        print(
            "Error: repositories is required and must be a non-empty list.",
            file=sys.stderr,
        )
        sys.exit(1)

    normalized_repositories: list[dict[str, str | None]] = []
    for index, item in enumerate(repositories):
        if not isinstance(item, dict):
            print(
                f"Error: repositories[{index}] must be a mapping/object.",
                file=sys.stderr,
            )
            sys.exit(1)
        _warn_unknown_config_keys(item, ALLOWED_REPOSITORY_KEYS)

        repo_name = item.get("repo")
        if not isinstance(repo_name, str) or not repo_name.strip():
            print(
                f"Error: repositories[{index}].repo is required and must be a non-empty string.",
                file=sys.stderr,
            )
            sys.exit(1)
        repo_slug = repo_name.strip()
        if (
            "/" not in repo_slug
            or repo_slug.count("/") != 1
            or repo_slug.startswith("/")
            or repo_slug.endswith("/")
        ):
            print(
                f"Error: repositories[{index}].repo must be in 'owner/repo' format.",
                file=sys.stderr,
            )
            sys.exit(1)

        user_name = item.get("user_name")
        if user_name is not None and not isinstance(user_name, str):
            print(
                f"Error: repositories[{index}].user_name must be a string when specified.",
                file=sys.stderr,
            )
            sys.exit(1)

        user_email = item.get("user_email")
        if user_email is not None and not isinstance(user_email, str):
            print(
                f"Error: repositories[{index}].user_email must be a string when specified.",
                file=sys.stderr,
            )
            sys.exit(1)

        normalized_repositories.append(
            {
                "repo": repo_name.strip(),
                "user_name": user_name.strip()
                if isinstance(user_name, str) and user_name.strip()
                else None,
                "user_email": user_email.strip()
                if isinstance(user_email, str) and user_email.strip()
                else None,
            }
        )

    config["repositories"] = normalized_repositories
    return config


def expand_repositories(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ワイルドカード（例: owner/*）を含むリポジトリ定義を gh cli で展開する。"""
    expanded: list[dict[str, Any]] = []
    for repo_info in repos:
        repo_name = repo_info["repo"]
        if repo_name.endswith("/*"):
            owner = repo_name[:-2]
            print(f"Expanding wildcard repository: {repo_name}")
            cmd = [
                "gh",
                "repo",
                "list",
                owner,
                "--json",
                "nameWithOwner",
                "--jq",
                ".[].nameWithOwner",
                "--limit",
                "1000",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
            )
            if result.returncode != 0:
                print(
                    f"Error: failed to expand {repo_name}: {(result.stderr or '').strip()}",
                    file=sys.stderr,
                )
                sys.exit(1)

            lines = result.stdout.strip().splitlines()
            if not lines:
                print(f"Error: no repositories found for {repo_name}", file=sys.stderr)
                sys.exit(1)

            for line in lines:
                resolved_name = line.strip()
                if resolved_name:
                    new_info = dict(repo_info)
                    new_info["repo"] = resolved_name
                    expanded.append(new_info)
        else:
            expanded.append(repo_info)
    return expanded
