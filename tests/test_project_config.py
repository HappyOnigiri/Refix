"""Unit tests for project_config module."""

from unittest.mock import call

import pytest

import project_config
from errors import ProjectConfigError, SubprocessError


# ---------------------------------------------------------------------------
# load_project_config
# ---------------------------------------------------------------------------


def test_returns_none_when_file_not_found(tmp_path):
    result = project_config.load_project_config(tmp_path)
    assert result is None


def test_returns_parsed_config_with_name(tmp_path):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  commands:\n    - name: install\n      run: npm ci\n"
    )
    result = project_config.load_project_config(tmp_path)
    assert result == {
        "setup": {"when": "always", "commands": [{"name": "install", "run": "npm ci"}]},
    }


def test_returns_parsed_config_without_name(tmp_path):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands:\n    - run: npm ci\n")
    result = project_config.load_project_config(tmp_path)
    assert result == {
        "setup": {"when": "always", "commands": [{"run": "npm ci"}]},
    }


def test_when_defaults_to_always(tmp_path):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands:\n    - run: npm ci\n")
    result = project_config.load_project_config(tmp_path)
    assert result is not None
    assert result["setup"]["when"] == "always"


def test_when_clone_only_is_parsed(tmp_path):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  when: clone_only\n  commands:\n    - run: npm ci\n"
    )
    result = project_config.load_project_config(tmp_path)
    assert result is not None
    assert result["setup"]["when"] == "clone_only"


def test_raises_on_invalid_when_value(tmp_path):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  when: on_push\n  commands:\n    - run: npm ci\n"
    )
    with pytest.raises(ProjectConfigError, match="setup.when の値が不正"):
        project_config.load_project_config(tmp_path)


def test_raises_on_invalid_yaml(tmp_path):
    (tmp_path / ".refix.yaml").write_text("key: [unclosed")
    with pytest.raises(ProjectConfigError, match="YAML パースエラー"):
        project_config.load_project_config(tmp_path)


def test_raises_on_non_dict_root(tmp_path):
    (tmp_path / ".refix.yaml").write_text("- item1\n- item2\n")
    with pytest.raises(ProjectConfigError, match="ルートはマッピング"):
        project_config.load_project_config(tmp_path)


def test_ignores_version_and_unknown_keys(tmp_path):
    """version や git など、旧フォーマットのキーは無視される。"""
    (tmp_path / ".refix.yaml").write_text(
        "version: 1\ngit:\n  user_name: Alice\nsetup:\n  commands:\n    - run: npm ci\n"
    )
    result = project_config.load_project_config(tmp_path)
    assert result is not None
    assert "version" not in result
    assert "git" not in result
    assert result["setup"]["commands"] == [{"run": "npm ci"}]


def test_raises_on_non_list_commands(tmp_path):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands: npm ci\n")
    with pytest.raises(ProjectConfigError, match="リストでなければなりません"):
        project_config.load_project_config(tmp_path)


def test_raises_on_command_missing_run_key(tmp_path):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands:\n    - name: install\n")
    with pytest.raises(ProjectConfigError, match="run フィールド"):
        project_config.load_project_config(tmp_path)


def test_raises_on_empty_run_string(tmp_path):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands:\n    - run: ''\n")
    with pytest.raises(ProjectConfigError, match="run フィールド"):
        project_config.load_project_config(tmp_path)


def test_empty_commands_list_is_valid(tmp_path):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands: []\n")
    result = project_config.load_project_config(tmp_path)
    assert result == {
        "setup": {"when": "always", "commands": []},
    }


def test_no_setup_section_returns_defaults(tmp_path):
    (tmp_path / ".refix.yaml").write_text("user_name: Alice\n")
    result = project_config.load_project_config(tmp_path)
    assert result == {
        "setup": {"when": "always", "commands": []},
    }


# ---------------------------------------------------------------------------
# run_project_setup
# ---------------------------------------------------------------------------


def test_does_nothing_when_no_config_file(tmp_path, mocker):
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)
    mock_run.assert_not_called()


def test_runs_commands_in_order(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  commands:\n    - run: npm ci\n    - run: cp .env.example .env\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0] == call(
        ["sh", "-c", "npm ci"],
        cwd=tmp_path,
        timeout=project_config.SETUP_COMMAND_TIMEOUT,
        env=None,
    )
    assert mock_run.call_args_list[1] == call(
        ["sh", "-c", "cp .env.example .env"],
        cwd=tmp_path,
        timeout=project_config.SETUP_COMMAND_TIMEOUT,
        env=None,
    )


def test_passes_repo_root_as_cwd(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  commands:\n    - run: echo hello\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)

    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == tmp_path


def test_uses_shell_c_invocation(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  commands:\n    - run: npm ci && echo done\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)

    cmd = mock_run.call_args[0][0]
    assert cmd == ["sh", "-c", "npm ci && echo done"]


def test_propagates_subprocess_error(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands:\n    - run: npm ci\n")
    mocker.patch.object(
        project_config,
        "run_command",
        side_effect=SubprocessError("failed", returncode=1),
    )
    with pytest.raises(SubprocessError):
        project_config.run_project_setup(tmp_path, is_first_clone=True)


def test_prints_command_name_if_present(tmp_path, capsys, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  commands:\n    - name: install deps\n      run: npm ci\n"
    )
    mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)

    captured = capsys.readouterr()
    assert "install deps" in captured.out
    assert "npm ci" in captured.out


def test_prints_run_string_if_name_absent(tmp_path, capsys, mocker):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands:\n    - run: npm ci\n")
    mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)

    captured = capsys.readouterr()
    assert "npm ci" in captured.out


def test_skips_execution_when_commands_empty(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text("setup:\n  commands: []\n")
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)

    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# run_project_setup: when フィールドの挙動
# ---------------------------------------------------------------------------


def test_when_always_runs_on_first_clone(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  when: always\n  commands:\n    - run: npm ci\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)
    mock_run.assert_called_once()


def test_when_always_runs_on_subsequent_update(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  when: always\n  commands:\n    - run: npm ci\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=False)
    mock_run.assert_called_once()


def test_when_clone_only_runs_on_first_clone(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  when: clone_only\n  commands:\n    - run: npm ci\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=True)
    mock_run.assert_called_once()


def test_when_clone_only_skips_on_subsequent_update(tmp_path, mocker):
    (tmp_path / ".refix.yaml").write_text(
        "setup:\n  when: clone_only\n  commands:\n    - run: npm ci\n"
    )
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup(tmp_path, is_first_clone=False)
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# run_project_setup_from_config: env パラメータの伝播
# ---------------------------------------------------------------------------


def test_run_project_setup_from_config_passes_env_to_run_command(tmp_path, mocker):
    config = {
        "setup": {"when": "always", "commands": [{"run": "make setup"}]},
    }
    mock_run = mocker.patch.object(project_config, "run_command")
    env = {"PATH": "/custom/bin:/usr/bin"}
    project_config.run_project_setup_from_config(
        config, tmp_path, is_first_clone=True, env=env
    )
    _, kwargs = mock_run.call_args
    assert kwargs.get("env") == env


def test_run_project_setup_from_config_env_none_by_default(tmp_path, mocker):
    config = {
        "setup": {"when": "always", "commands": [{"run": "make setup"}]},
    }
    mock_run = mocker.patch.object(project_config, "run_command")
    project_config.run_project_setup_from_config(config, tmp_path, is_first_clone=True)
    _, kwargs = mock_run.call_args
    assert kwargs.get("env") is None
