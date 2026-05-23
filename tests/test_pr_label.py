"""Unit tests for PR labeling helpers."""

from __future__ import annotations

import json

import pytest

import pr_label
from state_manager import StateComment
from type_defs import PRData


class TestRefixLabeling:
    def test_ensure_repo_label_exists_creates_when_missing(
        self, mocker, make_cmd_result
    ):
        mock_run = mocker.patch(
            "pr_label.run_command",
            side_effect=[
                make_cmd_result("", returncode=1, stderr="404 Not Found"),
                make_cmd_result('{"name":"refix: running"}'),
            ],
        )
        ok = pr_label._ensure_repo_label_exists(
            "owner/repo",
            "refix: running",
            color="FBCA04",
            description="x",
        )
        assert ok is True
        assert mock_run.call_count == 2

    def test_set_pr_running_label_ensures_labels_before_edit(self, mocker):
        mocker.patch("pr_label.update_workflow_status")
        ensure_mock = mocker.patch("pr_label._ensure_refix_labels")
        edit_mock = mocker.patch("pr_label.edit_pr_label", return_value=True)
        pr_data: PRData = {"labels": [{"name": "refix: done"}]}
        pr_label.set_pr_running_label("owner/repo", 1, pr_data=pr_data)
        ensure_mock.assert_called_once()
        assert edit_mock.call_count >= 1

    def test_resolve_workflow_status_returns_state_comment_status(self):
        state = StateComment(github_comment_id=1, body="", workflow_status="running")
        pr_data: PRData = {"labels": []}
        assert pr_label.resolve_workflow_status(state, pr_data) == "running"

    def test_ci_pending_label_not_in_constants(self):
        assert "ci_pending" not in pr_label.DEFAULT_ENABLED_PR_LABEL_KEYS
        assert "ci_pending" not in pr_label.PR_LABEL_KEY_TO_NAME


class TestUpdateDoneLabelIfCompleted:
    @pytest.fixture
    def base_kwargs(self):
        return dict(
            repo="owner/repo",
            pr_number=1,
            has_self_review_target=False,
            self_review_ran=False,
            fix_added_commits=False,
            fix_failed=False,
            state_saved=True,
            commits_by_phase=[],
            pr_data={"labels": []},
            dry_run=False,
        )

    def test_dry_run_returns_false(self, base_kwargs):
        base_kwargs["dry_run"] = True
        result, ci_grace = pr_label.update_done_label_if_completed(**base_kwargs)
        assert result is False
        assert ci_grace is False

    def test_fix_failed_blocks_done(self, mocker, base_kwargs):
        base_kwargs["fix_failed"] = True
        running_mock = mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mocker.patch.object(
            pr_label, "_wait_for_ci_status", return_value=pr_label.CIStatus.SUCCESS
        )
        pr_label.update_done_label_if_completed(**base_kwargs)
        running_mock.assert_called_once()

    def test_completion_calls_done(self, mocker, base_kwargs):
        done_mock = mocker.patch("pr_label._set_pr_done_label", return_value=True)
        mocker.patch.object(
            pr_label, "_wait_for_ci_status", return_value=pr_label.CIStatus.SUCCESS
        )
        pr_label.update_done_label_if_completed(**base_kwargs)
        done_mock.assert_called_once()

    def test_ci_unavailable_returns_grace_pending(self, mocker, base_kwargs):
        running_mock = mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mocker.patch.object(
            pr_label, "_wait_for_ci_status", return_value=pr_label.CIStatus.UNAVAILABLE
        )
        _, ci_grace = pr_label.update_done_label_if_completed(**base_kwargs)
        assert ci_grace is True
        running_mock.assert_called_once()

    def test_ci_failure_blocks_done(self, mocker, base_kwargs):
        running_mock = mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mocker.patch.object(
            pr_label, "_wait_for_ci_status", return_value=pr_label.CIStatus.FAILURE
        )
        _, ci_grace = pr_label.update_done_label_if_completed(**base_kwargs)
        assert ci_grace is False
        running_mock.assert_called_once()

    def test_ci_pending_returns_grace_pending(self, mocker, base_kwargs):
        running_mock = mocker.patch("pr_label.set_pr_running_label", return_value=True)
        mocker.patch.object(
            pr_label, "_wait_for_ci_status", return_value=pr_label.CIStatus.PENDING
        )
        _, ci_grace = pr_label.update_done_label_if_completed(**base_kwargs)
        assert ci_grace is True
        running_mock.assert_called_once()


class TestWaitForCIStatus:
    def test_wait_for_ci_status_polls_until_resolved(self, mocker):
        mocker.patch.object(
            pr_label,
            "_evaluate_ci_status",
            side_effect=[
                pr_label.CIStatus.PENDING,
                pr_label.CIStatus.PENDING,
                pr_label.CIStatus.SUCCESS,
            ],
        )
        sleep_mock = mocker.patch("pr_label.time.sleep")
        result = pr_label._wait_for_ci_status(
            "owner/repo", 1, ci_pending_wait_seconds=120
        )
        assert result is pr_label.CIStatus.SUCCESS
        assert sleep_mock.call_count == 2

    def test_wait_for_ci_status_budget_exhausted(self, mocker):
        mocker.patch.object(
            pr_label, "_evaluate_ci_status", return_value=pr_label.CIStatus.PENDING
        )
        sleep_mock = mocker.patch("pr_label.time.sleep")
        result = pr_label._wait_for_ci_status(
            "owner/repo", 1, ci_pending_wait_seconds=0
        )
        assert result is pr_label.CIStatus.PENDING
        sleep_mock.assert_not_called()


class TestMergeBackfill:
    def test_backfill_merged_labels_skips_when_merge_disabled(self, mocker):
        result = pr_label.backfill_merged_labels(
            "owner/repo",
            enabled_pr_label_keys={"running", "done"},
        )
        assert result == 0

    def test_mark_pr_merged_label_if_needed_skips_when_not_merged(
        self, mocker, make_cmd_result
    ):
        # load_state_comment は関数内ローカル import なので state_manager 側で差し替える
        from state_manager import StateComment as _SC

        mocker.patch(
            "state_manager.load_state_comment",
            return_value=_SC(github_comment_id=None, body=""),
        )
        mocker.patch(
            "pr_label.run_command",
            return_value=make_cmd_result(json.dumps({"mergedAt": "", "labels": []})),
        )
        result = pr_label._mark_pr_merged_label_if_needed(
            "owner/repo",
            1,
        )
        assert result is False
