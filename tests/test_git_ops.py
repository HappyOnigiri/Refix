"""Unit tests for git_ops module."""

from pathlib import Path

import pytest

import git_ops
from errors import ProjectConfigError


# ---------------------------------------------------------------------------
# get_branch_compare_status
# ---------------------------------------------------------------------------


def test_get_branch_compare_status_returns_status_and_behind_by(
    mocker, make_cmd_result
):
    payload = '{"status": "diverged", "behind_by": 3}'
    mock_run = mocker.patch.object(
        git_ops, "run_command", return_value=make_cmd_result(payload)
    )
    status, behind_by = git_ops.get_branch_compare_status(
        "owner/repo", "main", "feature"
    )

    assert status == "diverged"
    assert behind_by == 3
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "repos/owner/repo/compare/main...feature" in args[-1]


def test_get_branch_compare_status_raises_on_nonzero_returncode(
    mocker, make_cmd_result
):
    mocker.patch.object(
        git_ops,
        "run_command",
        return_value=make_cmd_result("", returncode=1, stderr="not found"),
    )
    with pytest.raises(RuntimeError, match="Error fetching compare status"):
        git_ops.get_branch_compare_status("owner/repo", "main", "feature")


def test_get_branch_compare_status_raises_on_invalid_json(mocker, make_cmd_result):
    mocker.patch.object(
        git_ops, "run_command", return_value=make_cmd_result("not-json")
    )
    with pytest.raises(RuntimeError, match="Failed to parse compare status"):
        git_ops.get_branch_compare_status("owner/repo", "main", "feature")


def test_get_branch_compare_status_raises_on_missing_fields(mocker, make_cmd_result):
    payload = '{"status": "ahead"}'  # behind_by missing
    mocker.patch.object(git_ops, "run_command", return_value=make_cmd_result(payload))
    with pytest.raises(RuntimeError, match="Unexpected compare payload"):
        git_ops.get_branch_compare_status("owner/repo", "main", "feature")


def test_get_branch_compare_status_url_encodes_branch_names(mocker, make_cmd_result):
    payload = '{"status": "identical", "behind_by": 0}'
    mock_run = mocker.patch.object(
        git_ops, "run_command", return_value=make_cmd_result(payload)
    )
    git_ops.get_branch_compare_status("owner/repo", "main", "feature/my branch")

    args = mock_run.call_args[0][0]
    assert "feature%2Fmy%20branch" in args[-1]


# ---------------------------------------------------------------------------
# needs_base_merge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("compare_status", "behind_by", "expected"),
    [
        ("identical", 0, False),
        ("ahead", 0, False),
        ("behind", 0, True),
        ("diverged", 0, True),
        ("ahead", 1, True),
        ("identical", 2, True),
    ],
)
def test_needs_base_merge(compare_status, behind_by, expected):
    assert git_ops.needs_base_merge(compare_status, behind_by) == expected


# ---------------------------------------------------------------------------
# has_merge_conflicts
# ---------------------------------------------------------------------------


def test_has_merge_conflicts_returns_true_when_unmerged_files(mocker, make_cmd_result):
    mocker.patch.object(
        git_ops, "run_git", return_value=make_cmd_result("conflict.py\n")
    )
    assert git_ops.has_merge_conflicts(Path("/some/repo")) is True


def test_has_merge_conflicts_returns_false_when_no_unmerged_files(
    mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result(""))
    assert git_ops.has_merge_conflicts(Path("/some/repo")) is False


def test_has_merge_conflicts_raises_on_nonzero_returncode(mocker, make_cmd_result):
    mocker.patch.object(
        git_ops, "run_git", return_value=make_cmd_result("", returncode=1)
    )
    with pytest.raises(RuntimeError, match="failed to detect merge conflicts"):
        git_ops.has_merge_conflicts(Path("/some/repo"))


# ---------------------------------------------------------------------------
# merge_base_branch
# ---------------------------------------------------------------------------


def test_merge_base_branch_returns_merged_changes_true_when_head_changed(
    mocker, make_cmd_result
):
    works_dir = Path("/some/repo")
    side_effects = [
        make_cmd_result(),
        # fetch
        make_cmd_result("abc123\n"),
        # rev-parse HEAD before
        make_cmd_result(),
        # merge (success)
        make_cmd_result("def456\n"),
        # rev-parse HEAD after
    ]
    mocker.patch.object(git_ops, "run_git", side_effect=side_effects)
    merged_changes, has_conflicts = git_ops.merge_base_branch(works_dir, "main")

    assert merged_changes is True
    assert has_conflicts is False


def test_merge_base_branch_returns_merged_changes_false_when_head_unchanged(
    mocker, make_cmd_result
):
    works_dir = Path("/some/repo")
    same_sha = "abc123"
    side_effects = [
        make_cmd_result(),  # fetch
        make_cmd_result(f"{same_sha}\n"),  # rev-parse HEAD before
        make_cmd_result(),  # merge (success)
        make_cmd_result(f"{same_sha}\n"),  # rev-parse HEAD after
    ]
    mocker.patch.object(git_ops, "run_git", side_effect=side_effects)
    merged_changes, has_conflicts = git_ops.merge_base_branch(works_dir, "main")

    assert merged_changes is False
    assert has_conflicts is False


def test_merge_base_branch_returns_has_conflicts_true_when_merge_fails_with_conflicts(
    mocker, make_cmd_result
):
    works_dir = Path("/some/repo")
    side_effects = [
        make_cmd_result(),  # fetch
        make_cmd_result("abc123\n"),  # rev-parse HEAD before
        make_cmd_result("", returncode=1),  # merge fails
        make_cmd_result("file.py\n"),  # has_merge_conflicts -> run_git diff
    ]
    mocker.patch.object(git_ops, "run_git", side_effect=side_effects)
    merged_changes, has_conflicts = git_ops.merge_base_branch(works_dir, "main")

    assert merged_changes is False
    assert has_conflicts is True


def test_merge_base_branch_raises_when_merge_fails_without_conflicts(
    mocker, make_cmd_result
):
    works_dir = Path("/some/repo")
    side_effects = [
        make_cmd_result(),  # fetch
        make_cmd_result("abc123\n"),  # rev-parse HEAD before
        make_cmd_result("", returncode=1, stderr="some other error"),  # merge fails
        make_cmd_result(""),  # has_merge_conflicts -> no conflicts
    ]
    mocker.patch.object(git_ops, "run_git", side_effect=side_effects)
    with pytest.raises(RuntimeError, match="git merge failed without conflict markers"):
        git_ops.merge_base_branch(works_dir, "main")


# ---------------------------------------------------------------------------
# prepare_repository
# ---------------------------------------------------------------------------


def test_prepare_repository_calls_run_project_setup_with_is_first_clone_false(
    tmp_path, mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mock_load = mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    result = git_ops.prepare_repository("owner/repo", "main")

    mock_load.assert_called_once_with(result)
    mock_setup.assert_called_once_with(None, result, is_first_clone=False, env=None)


def test_prepare_repository_calls_run_project_setup_with_is_first_clone_true(
    tmp_path, mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mock_load = mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=False)
    mocker.patch.object(Path, "mkdir")
    result = git_ops.prepare_repository("owner/repo", "main")

    mock_load.assert_called_once_with(result)
    mock_setup.assert_called_once_with(None, result, is_first_clone=True, env=None)


def test_prepare_repository_propagates_project_config_error(
    tmp_path, mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(
        git_ops, "load_project_config", side_effect=ProjectConfigError("bad config")
    )
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    with pytest.raises(ProjectConfigError, match="bad config"):
        git_ops.prepare_repository("owner/repo", "main")


def test_prepare_repository_global_setup_only(tmp_path, mocker, make_cmd_result):
    """batch_global_setup のみ指定した場合、global setup が実行される。"""
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mock_load = mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")

    global_setup = {"when": "always", "commands": [{"run": "npm install -g tool"}]}
    result = git_ops.prepare_repository(
        "owner/repo", "main", batch_global_setup=global_setup
    )

    # global setup が実行される（{"setup": global_setup} でラップ）
    assert mock_setup.call_count == 2
    first_call_args = mock_setup.call_args_list[0]
    assert first_call_args[0][0] == {"setup": global_setup}
    # repo setup は load_project_config にフォールバック
    mock_load.assert_called_once_with(result)
    second_call_args = mock_setup.call_args_list[1]
    assert second_call_args[0][0] is None


def test_prepare_repository_global_and_repo_setup(tmp_path, mocker, make_cmd_result):
    """global setup と repo setup 両方指定した場合、global が先に実行される。"""
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")

    global_setup = {"when": "always", "commands": [{"run": "npm install -g tool"}]}
    repo_setup = {"when": "clone_only", "commands": [{"run": "npm install"}]}
    git_ops.prepare_repository(
        "owner/repo",
        "main",
        batch_setup=repo_setup,
        batch_global_setup=global_setup,
    )

    assert mock_setup.call_count == 2
    # 1番目: global setup
    first_call_args = mock_setup.call_args_list[0]
    assert first_call_args[0][0] == {"setup": global_setup}
    # 2番目: repo setup
    second_call_args = mock_setup.call_args_list[1]
    assert second_call_args[0][0] == {"setup": repo_setup}


# ---------------------------------------------------------------------------
# _install_python
# ---------------------------------------------------------------------------


def test_install_python_returns_env_with_updated_path(mocker, make_cmd_result):
    mocker.patch.object(
        git_ops,
        "run_command",
        return_value=make_cmd_result("/usr/local/bin/python3.11"),
    )
    env = git_ops._install_python("3.11")
    assert "PATH" in env
    assert "/usr/local/bin" in env["PATH"]


def test_install_python_calls_uv_install_then_find(mocker, make_cmd_result):
    mock_run = mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # uv python install
            make_cmd_result("/home/user/.local/bin/python3.11"),  # uv python find
        ],
    )
    git_ops._install_python("3.11")
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][0] == ["uv", "python", "install", "3.11"]
    assert mock_run.call_args_list[1][0][0] == ["uv", "python", "find", "3.11"]


def test_install_python_raises_when_find_returns_empty(mocker, make_cmd_result):
    mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # uv python install
            make_cmd_result(""),  # uv python find returns empty
        ],
    )
    with pytest.raises(RuntimeError, match="no output"):
        git_ops._install_python("3.11")


# ---------------------------------------------------------------------------
# prepare_repository with python_version
# ---------------------------------------------------------------------------


def test_prepare_repository_calls_install_python_when_python_version_set(
    mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    mock_install = mocker.patch.object(
        git_ops, "_install_python", return_value={"PATH": "/new/bin:/usr/bin"}
    )

    git_ops.prepare_repository("owner/repo", "main", python_version="3.11")
    mock_install.assert_called_once_with("3.11")


def test_prepare_repository_passes_env_to_setup_when_python_version_set(
    mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    setup_env = {"PATH": "/new/bin:/usr/bin"}
    mocker.patch.object(git_ops, "_install_python", return_value=setup_env)

    git_ops.prepare_repository("owner/repo", "main", python_version="3.11")

    call_kwargs = mock_setup.call_args_list[0][1]
    assert call_kwargs.get("env") == setup_env


def test_prepare_repository_does_not_call_install_python_when_python_version_none(
    mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    mock_install = mocker.patch.object(git_ops, "_install_python")

    git_ops.prepare_repository("owner/repo", "main")
    mock_install.assert_not_called()
