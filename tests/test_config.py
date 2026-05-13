"""Unit tests for config loading and repository expansion."""

from __future__ import annotations

import pytest

from config import (
    ALLOWED_MODEL_KEYS,
    DEFAULT_CONFIG,
    PR_LABEL_KEYS,
    get_enabled_pr_label_keys,
    get_incremental_review,
    load_config,
    load_single_config,
    merge_repo_config,
)
from errors import ConfigError


def _write(tmp_path, text: str):
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestDefaults:
    def test_default_models_review_and_fix(self):
        assert DEFAULT_CONFIG["models"]["review"] == "opus"
        assert DEFAULT_CONFIG["models"]["fix"] == "sonnet"

    def test_allowed_model_keys(self):
        assert ALLOWED_MODEL_KEYS == {"review", "fix"}

    def test_pr_label_keys_no_ci_pending(self):
        assert "ci_pending" not in PR_LABEL_KEYS
        assert set(PR_LABEL_KEYS) == {
            "running",
            "done",
            "merged",
            "auto_merge_requested",
        }

    def test_no_coderabbit_keys_in_defaults(self):
        for key in DEFAULT_CONFIG:
            assert not key.startswith("coderabbit_")
            assert key != "triggers"
            assert key != "ci_log_max_lines"


class TestLoadSingleConfig:
    def test_loads_models_review_and_fix(self, tmp_path):
        path = _write(
            tmp_path,
            "models:\n  review: opus\n  fix: sonnet\nlanguage: en\n",
        )
        cfg = load_single_config(path)
        assert cfg["models"] == {"review": "opus", "fix": "sonnet"}

    def test_rejects_summarize_model_key(self, tmp_path):
        path = _write(tmp_path, "models:\n  summarize: haiku\n")
        with pytest.raises(ConfigError, match="Unknown config key"):
            load_single_config(path)

    def test_rejects_coderabbit_keys(self, tmp_path):
        path = _write(tmp_path, "coderabbit_auto_resume: true\n")
        with pytest.raises(ConfigError, match="Unknown config key"):
            load_single_config(path)

    def test_rejects_ci_pending_label(self, tmp_path):
        path = _write(
            tmp_path,
            "enabled_pr_labels:\n  - running\n  - done\n  - ci_pending\n",
        )
        with pytest.raises(ConfigError):
            load_single_config(path)

    def test_rejects_triggers_key(self, tmp_path):
        path = _write(tmp_path, "triggers:\n  issue_comment:\n    authors: []\n")
        with pytest.raises(ConfigError, match="Unknown config key"):
            load_single_config(path)

    def test_models_review_must_be_non_empty_string(self, tmp_path):
        path = _write(tmp_path, "models:\n  review: ''\n")
        with pytest.raises(ConfigError, match="models.review"):
            load_single_config(path)


class TestLoadBatchConfig:
    def test_loads_minimal_batch(self, tmp_path):
        path = _write(
            tmp_path,
            "global:\n  models:\n    review: opus\nrepositories:\n  - repo: owner/r\n",
        )
        cfg = load_config(path)
        assert cfg["models"]["review"] == "opus"
        assert cfg["repositories"][0]["repo"] == "owner/r"

    def test_rejects_unknown_key(self, tmp_path):
        path = _write(
            tmp_path,
            "global:\n  coderabbit_require_review: true\nrepositories:\n  - repo: owner/r\n",
        )
        with pytest.raises(ConfigError):
            load_config(path)


class TestReviewMinSeverity:
    def test_default_is_nitpick(self):
        assert DEFAULT_CONFIG["review_min_severity"] == "nitpick"

    def test_loads_valid_value(self, tmp_path):
        path = _write(tmp_path, "review_min_severity: major\n")
        cfg = load_single_config(path)
        assert cfg["review_min_severity"] == "major"

    def test_normalizes_case(self, tmp_path):
        path = _write(tmp_path, "review_min_severity: MAJOR\n")
        cfg = load_single_config(path)
        assert cfg["review_min_severity"] == "major"

    def test_rejects_invalid_value(self, tmp_path):
        path = _write(tmp_path, "review_min_severity: blocker\n")
        with pytest.raises(ConfigError, match="review_min_severity"):
            load_single_config(path)

    def test_rejects_empty_value(self, tmp_path):
        path = _write(tmp_path, 'review_min_severity: ""\n')
        with pytest.raises(ConfigError, match="review_min_severity"):
            load_single_config(path)

    def test_loadable_in_batch_global(self, tmp_path):
        path = _write(
            tmp_path,
            "global:\n  review_min_severity: minor\nrepositories:\n  - repo: o/r\n",
        )
        cfg = load_config(path)
        assert cfg["review_min_severity"] == "minor"


class TestEnabledPrLabels:
    def test_get_enabled_pr_label_keys(self):
        result = get_enabled_pr_label_keys(
            {"enabled_pr_labels": ["running", "done"]},
            DEFAULT_CONFIG,
        )
        assert result == {"running", "done"}


class TestIncrementalReview:
    def test_default_is_true(self):
        assert DEFAULT_CONFIG["incremental_review"] is True

    def test_getter_returns_default(self):
        assert get_incremental_review({}, DEFAULT_CONFIG) is True

    def test_getter_returns_false_when_set(self):
        assert (
            get_incremental_review({"incremental_review": False}, DEFAULT_CONFIG)
            is False
        )

    def test_non_bool_raises(self, tmp_path):
        cfg_path = _write(tmp_path, "incremental_review: 1\n")
        with pytest.raises(ConfigError, match="incremental_review"):
            load_single_config(cfg_path)

    def test_string_raises(self, tmp_path):
        cfg_path = _write(tmp_path, 'incremental_review: "true"\n')
        with pytest.raises(ConfigError, match="incremental_review"):
            load_single_config(cfg_path)

    def test_loadable_in_single_mode(self, tmp_path):
        cfg_path = _write(tmp_path, "incremental_review: false\n")
        cfg = load_single_config(cfg_path)
        assert cfg["incremental_review"] is False

    def test_loadable_in_batch_global(self, tmp_path):
        cfg_path = _write(
            tmp_path,
            "global:\n  incremental_review: false\nrepositories:\n  - repo: o/r\n",
        )
        cfg = load_config(cfg_path)
        assert cfg["incremental_review"] is False


class TestMergeRepoConfig:
    def test_repo_overrides_global(self):
        global_cfg = {"models": {"review": "opus", "fix": "sonnet"}}
        repo_entry = {"repo": "o/r", "models": {"fix": "opus"}}
        merged = merge_repo_config(global_cfg, repo_entry)
        assert merged["models"] == {"review": "opus", "fix": "opus"}
