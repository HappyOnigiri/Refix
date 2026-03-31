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
    works_dir, setup_env = git_ops.prepare_repository("owner/repo", "main")

    assert setup_env is None
    mock_load.assert_called_once_with(works_dir)
    mock_setup.assert_called_once_with(None, works_dir, is_first_clone=False, env=None)


def test_prepare_repository_calls_run_project_setup_with_is_first_clone_true(
    tmp_path, mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mock_load = mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=False)
    mocker.patch.object(Path, "mkdir")
    works_dir, setup_env = git_ops.prepare_repository("owner/repo", "main")

    assert setup_env is None
    mock_load.assert_called_once_with(works_dir)
    mock_setup.assert_called_once_with(None, works_dir, is_first_clone=True, env=None)


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
    works_dir, setup_env = git_ops.prepare_repository(
        "owner/repo", "main", batch_global_setup=global_setup
    )

    assert setup_env is None
    # global setup が実行される（{"setup": global_setup} でラップ）
    assert mock_setup.call_count == 2
    first_call_args = mock_setup.call_args_list[0]
    assert first_call_args[0][0] == {"setup": global_setup}
    # repo setup は load_project_config にフォールバック
    mock_load.assert_called_once_with(works_dir)
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

    works_dir, returned_env = git_ops.prepare_repository(
        "owner/repo", "main", python_version="3.11"
    )

    assert returned_env == setup_env
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


# ---------------------------------------------------------------------------
# _install_node
# ---------------------------------------------------------------------------


def test_install_node_returns_env_with_updated_path(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value="/usr/local/bin/fnm")
    mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/home/user/.fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    env = git_ops._install_node("22")
    assert "PATH" in env
    assert "/home/user/.fnm/node-versions/v22.0.0/installation/bin" in env["PATH"]


def test_install_node_calls_fnm_install_then_exec(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value="/usr/local/bin/fnm")
    mock_run = mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/home/user/.fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    git_ops._install_node("22")
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][0] == ["fnm", "install", "22"]
    assert mock_run.call_args_list[1][0][0] == [
        "fnm",
        "exec",
        "--using",
        "22",
        "which",
        "node",
    ]


def test_install_node_raises_when_exec_returns_empty(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value="/usr/local/bin/fnm")
    mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # fnm install
            make_cmd_result(""),  # fnm exec returns empty
        ],
    )
    with pytest.raises(RuntimeError, match="no output"):
        git_ops._install_node("22")


def test_install_node_auto_installs_fnm_when_not_found(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value=None)
    mock_run = mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # curl install fnm
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/home/user/.fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    git_ops._install_node("22")
    assert mock_run.call_count == 3
    first_call_args = mock_run.call_args_list[0][0][0]
    assert first_call_args[0] == "bash"
    assert "--skip-shell" in first_call_args[-1]


def test_install_node_passes_fnm_bin_to_fnm_commands_after_auto_install(
    mocker, make_cmd_result
):
    mocker.patch("git_ops.shutil.which", return_value=None)
    mocker.patch.dict(
        "os.environ",
        {"PATH": "/original/path", "XDG_DATA_HOME": "/custom/xdg"},
    )
    mock_run = mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # curl install fnm
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/home/user/.fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    git_ops._install_node("22")
    expected_fnm_env = {"PATH": "/custom/xdg/fnm:/original/path"}
    assert mock_run.call_args_list[1].kwargs.get("env") == expected_fnm_env
    assert mock_run.call_args_list[2].kwargs.get("env") == expected_fnm_env


def test_install_node_uses_dotfnm_dir_when_exists(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value=None)
    os_environ = {
        k: v for k, v in __import__("os").environ.items() if k != "XDG_DATA_HOME"
    }
    os_environ["PATH"] = "/original/path"
    mocker.patch.dict("os.environ", os_environ, clear=True)
    mocker.patch.object(Path, "exists", return_value=True)
    mock_run = mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # curl install fnm
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/home/user/.fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    git_ops._install_node("22")
    expected_fnm_bin = str(Path.home() / ".fnm")
    fnm_env = mock_run.call_args_list[1].kwargs.get("env")
    assert fnm_env is not None
    assert fnm_env["PATH"].startswith(expected_fnm_bin)


def test_install_node_uses_macos_path_on_darwin_without_xdg(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value=None)
    os_environ = {
        k: v for k, v in __import__("os").environ.items() if k != "XDG_DATA_HOME"
    }
    os_environ["PATH"] = "/original/path"
    mocker.patch.dict("os.environ", os_environ, clear=True)
    mocker.patch.object(git_ops.sys, "platform", "darwin")
    mocker.patch.object(Path, "exists", return_value=False)
    mock_run = mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # curl install fnm
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/Users/user/Library/Application Support/fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    git_ops._install_node("22")
    expected_fnm_bin = str(Path.home() / "Library" / "Application Support" / "fnm")
    fnm_env = mock_run.call_args_list[1].kwargs.get("env")
    assert fnm_env is not None
    assert fnm_env["PATH"].startswith(expected_fnm_bin)


def test_install_node_merges_base_env_path(mocker, make_cmd_result):
    mocker.patch("git_ops.shutil.which", return_value="/usr/local/bin/fnm")
    mocker.patch.object(
        git_ops,
        "run_command",
        side_effect=[
            make_cmd_result(""),  # fnm install
            make_cmd_result(
                "/home/user/.fnm/node-versions/v22.0.0/installation/bin/node"
            ),
        ],
    )
    base_env = {"PATH": "/python/bin:/usr/bin"}
    env = git_ops._install_node("22", base_env=base_env)
    assert "/home/user/.fnm/node-versions/v22.0.0/installation/bin" in env["PATH"]
    assert "/python/bin" in env["PATH"]


# ---------------------------------------------------------------------------
# prepare_repository with node_version
# ---------------------------------------------------------------------------


def test_prepare_repository_calls_install_node_when_node_version_set(
    mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    mock_install = mocker.patch.object(
        git_ops, "_install_node", return_value={"PATH": "/node/bin:/usr/bin"}
    )

    git_ops.prepare_repository("owner/repo", "main", node_version="22")
    mock_install.assert_called_once_with("22", base_env=None)


def test_prepare_repository_passes_node_env_to_setup(mocker, make_cmd_result):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mock_setup = mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    node_env = {"PATH": "/node/bin:/usr/bin"}
    mocker.patch.object(git_ops, "_install_node", return_value=node_env)

    works_dir, returned_env = git_ops.prepare_repository(
        "owner/repo", "main", node_version="22"
    )

    assert returned_env == node_env
    call_kwargs = mock_setup.call_args_list[0][1]
    assert call_kwargs.get("env") == node_env


def test_prepare_repository_does_not_call_install_node_when_node_version_none(
    mocker, make_cmd_result
):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    mock_install = mocker.patch.object(git_ops, "_install_node")

    git_ops.prepare_repository("owner/repo", "main")
    mock_install.assert_not_called()


def test_prepare_repository_merges_python_and_node_env(mocker, make_cmd_result):
    mocker.patch.object(git_ops, "run_git", return_value=make_cmd_result())
    mocker.patch.object(git_ops, "setup_claude_settings")
    mocker.patch.object(git_ops, "load_project_config", return_value=None)
    mocker.patch.object(git_ops, "run_project_setup_from_config")
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch.object(Path, "mkdir")
    python_env = {"PATH": "/python/bin:/usr/bin"}
    node_env = {"PATH": "/node/bin:/python/bin:/usr/bin"}
    mocker.patch.object(git_ops, "_install_python", return_value=python_env)
    mock_node = mocker.patch.object(git_ops, "_install_node", return_value=node_env)

    works_dir, returned_env = git_ops.prepare_repository(
        "owner/repo", "main", python_version="3.11", node_version="22"
    )

    mock_node.assert_called_once_with("22", base_env=python_env)
    assert returned_env == node_env
