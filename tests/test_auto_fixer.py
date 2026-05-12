"""Unit tests for auto_fixer module — self-review + fix flow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import auto_fixer
from state_manager import StateComment
from type_defs import (
    PRData,
    SelfReviewFinding,
    SelfReviewResult,
)


@dataclass
class _FakeRunGitResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _pr_data(head_sha: str = "newhead1234567") -> PRData:
    return {
        "number": 7,
        "title": "Test PR",
        "body": "PR body",
        "headRefName": "feat/x",
        "baseRefName": "main",
        "headRefOid": head_sha,
        "labels": [],
        "isDraft": False,
        "state": "OPEN",
    }


def _build_ctx(tmp_path: Path) -> auto_fixer.PRContext:
    return auto_fixer.PRContext(
        repo="owner/repo",
        pr_number=7,
        title="Test PR",
        is_draft=False,
        branch_name="feat/x",
        base_branch="main",
        works_dir=tmp_path,
        labels=[],
        dry_run=False,
        silent=True,
        write_result_to_comment=False,
        review_model="opus",
        fix_model="sonnet",
        auto_merge_enabled=False,
        enabled_pr_label_keys={"running", "done"},
        process_draft_prs=False,
        state_comment_timezone="JST",
        language="en",
        max_modified_prs_per_run=0,
        max_committed_prs_per_run=0,
        max_claude_prs_per_run=0,
        modified_prs=set(),
        committed_prs=set(),
        claude_prs=set(),
        ci_empty_as_success=True,
        ci_empty_grace_minutes=5,
        merge_method="auto",
        base_update_method="merge",
        use_pr_labels=False,
    )


def _make_self_review(
    findings: list[SelfReviewFinding] | None = None,
) -> SelfReviewResult:
    return SelfReviewResult(
        head_sha="newhead1234567",
        reviewed_at="2026-05-12 14:30:00 JST",
        summary="ok",
        findings=findings or [],
        raw_xml="<self_review/>",
    )


def _finding(severity: str = "major") -> SelfReviewFinding:
    return SelfReviewFinding(
        finding_id="f1",
        severity=severity,
        path="src/x.py",
        line=10,
        title="t",
        body="b",
        suggested_fix="fix",
    )


class TestRunSelfReviewPhase:
    def test_happy_path_writes_and_parses_xml(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        pr_data = _pr_data()
        diff_stub = _FakeRunGitResult(stdout="diff --git a/x b/x\n+1\n")
        name_only_stub = _FakeRunGitResult(stdout="src/x.py\n")
        mocker.patch.object(
            auto_fixer,
            "_run_git",
            side_effect=[diff_stub, name_only_stub],
        )
        xml_text = (
            '<self_review version="1" head_sha="newhead1234567" reviewed_at="x">'
            "<summary>ok</summary>"
            "<findings>"
            '<finding id="f1" severity="major" path="src/x.py">'
            "<title>t</title><body>b</body><suggested_fix>do f</suggested_fix>"
            "</finding>"
            "</findings>"
            "</self_review>"
        )

        def fake_run_claude(*args, **kwargs):
            output_path = tmp_path / "_self_review.xml"
            output_path.write_text(xml_text, encoding="utf-8")
            return ("", "review stdout")

        mocker.patch.object(
            auto_fixer, "run_claude_prompt", side_effect=fake_run_claude
        )

        result = auto_fixer._run_self_review_phase(
            ctx, pr_data, tmp_path, StateComment(github_comment_id=None, body=""), []
        )
        assert result is not None
        assert len(result.findings) == 1
        assert result.head_sha == "newhead1234567"

    def test_dry_run_returns_none(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        ctx.dry_run = True
        pr_data = _pr_data()
        mocker.patch.object(
            auto_fixer,
            "_run_git",
            side_effect=[
                _FakeRunGitResult(stdout=""),
                _FakeRunGitResult(stdout=""),
            ],
        )
        run_claude_mock = mocker.patch.object(auto_fixer, "run_claude_prompt")
        result = auto_fixer._run_self_review_phase(
            ctx, pr_data, tmp_path, StateComment(github_comment_id=None, body=""), []
        )
        assert result is None
        run_claude_mock.assert_not_called()

    def test_review_session_committing_raises(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        pr_data = _pr_data()
        mocker.patch.object(
            auto_fixer,
            "_run_git",
            side_effect=[
                _FakeRunGitResult(stdout=""),
                _FakeRunGitResult(stdout=""),
            ],
        )
        mocker.patch.object(
            auto_fixer,
            "run_claude_prompt",
            return_value=("aaaaaaa\n", "out"),
        )
        with pytest.raises(RuntimeError, match="unexpected commits"):
            auto_fixer._run_self_review_phase(
                ctx,
                pr_data,
                tmp_path,
                StateComment(github_comment_id=None, body=""),
                [],
            )

    def test_malformed_xml_raises(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        pr_data = _pr_data()
        mocker.patch.object(
            auto_fixer,
            "_run_git",
            side_effect=[
                _FakeRunGitResult(stdout=""),
                _FakeRunGitResult(stdout=""),
            ],
        )

        def fake_run_claude(*args, **kwargs):
            (tmp_path / "_self_review.xml").write_text("not xml", encoding="utf-8")
            return ("", "")

        mocker.patch.object(
            auto_fixer, "run_claude_prompt", side_effect=fake_run_claude
        )
        with pytest.raises(ValueError):
            auto_fixer._run_self_review_phase(
                ctx,
                pr_data,
                tmp_path,
                StateComment(github_comment_id=None, body=""),
                [],
            )


class TestRunFixPhase:
    def test_happy_path_appends_log_entry_and_updates_head(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        pr_data = _pr_data()
        self_review = _make_self_review([_finding()])
        mocker.patch.object(
            auto_fixer,
            "_run_git",
            return_value=_FakeRunGitResult(stdout=""),
        )
        mocker.patch.object(
            auto_fixer,
            "_push_if_needed",
            return_value=_FakeRunGitResult(stdout=""),
        )
        mocker.patch.object(
            auto_fixer,
            "run_claude_prompt",
            return_value=("aaaaaaa fix\n", "fix stdout"),
        )
        mocker.patch.object(auto_fixer, "set_pr_running_label")
        mocker.patch.object(
            auto_fixer,
            "load_state_comment",
            return_value=StateComment(github_comment_id=1, body="", self_review_log=[]),
        )
        upsert_mock = mocker.patch.object(auto_fixer, "upsert_state_comment")

        fix_started, fix_added_commits, state_saved, fix_failed = (
            auto_fixer._run_fix_phase(
                ctx,
                pr_data,
                tmp_path,
                self_review,
                StateComment(github_comment_id=1, body=""),
                [],
                ["aaaaaaa fix"],
            )
        )
        assert fix_started is True
        assert fix_added_commits is True
        assert state_saved is True
        assert fix_failed is False
        upsert_call = upsert_mock.call_args
        assert upsert_call.kwargs["last_reviewed_head"] == self_review.head_sha
        assert upsert_call.kwargs["self_review_log"][0].head_sha == self_review.head_sha

    def test_dry_run_no_claude_calls(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        ctx.dry_run = True
        run_claude_mock = mocker.patch.object(auto_fixer, "run_claude_prompt")
        result = auto_fixer._run_fix_phase(
            ctx,
            _pr_data(),
            tmp_path,
            _make_self_review([_finding()]),
            StateComment(github_comment_id=None, body=""),
            [],
            [],
        )
        assert result == (False, False, False, False)
        run_claude_mock.assert_not_called()


class TestNoFindingsAndFailedRecording:
    def test_no_findings_entry_updates_head(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        self_review = _make_self_review([])
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(auto_fixer, "append_self_review_entry", side_effect=capture)
        ok = auto_fixer._record_no_findings_entry(
            ctx, self_review, StateComment(github_comment_id=None, body="")
        )
        assert ok is True
        assert captured["kwargs"]["update_last_reviewed_head"] is True

    def test_failed_fix_log_does_not_update_head(self, mocker, tmp_path):
        ctx = _build_ctx(tmp_path)
        self_review = _make_self_review([_finding()])
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(auto_fixer, "append_self_review_entry", side_effect=capture)
        auto_fixer._record_failed_fix_log_entry(
            ctx, self_review, StateComment(github_comment_id=None, body="")
        )
        assert captured["kwargs"]["update_last_reviewed_head"] is False


class TestIdempotencyAndFailureIntegration:
    def test_idempotent_same_head_skips_claude(self, mocker, tmp_path):
        """state_comment.last_reviewed_head == pr_data["headRefOid"] → Claude 呼び出し 0 回。"""
        run_claude_mock = mocker.patch.object(auto_fixer, "run_claude_prompt")
        prepare_mock = mocker.patch.object(auto_fixer, "prepare_repository")
        update_done_mock = mocker.patch.object(
            auto_fixer, "update_done_label_if_completed", return_value=(False, False)
        )
        mocker.patch.object(auto_fixer, "fetch_pr_details", return_value=_pr_data())
        mocker.patch.object(
            auto_fixer,
            "load_state_comment",
            return_value=StateComment(
                github_comment_id=1,
                body="",
                self_review_log=[],
                last_reviewed_head="newhead1234567",
            ),
        )
        mocker.patch.object(
            auto_fixer, "get_branch_compare_status", return_value=("identical", 0)
        )
        mocker.patch.object(auto_fixer, "needs_base_merge", return_value=False)

        pr_input: PRData = {
            "number": 7,
            "title": "x",
            "state": "OPEN",
            "isDraft": False,
        }
        fetch_failed, processed, commits_entry, cacheable = (
            auto_fixer._process_single_pr(
                pr=pr_input,
                repo="owner/repo",
                dry_run=False,
                silent=True,
                review_model="opus",
                fix_model="sonnet",
                write_result_to_comment=False,
                auto_merge_enabled=False,
                merge_method="auto",
                base_update_method="merge",
                process_draft_prs=False,
                state_comment_timezone="JST",
                language="en",
                enabled_pr_label_keys=set(),
                max_modified_prs=0,
                max_committed_prs=0,
                max_claude_prs=0,
                modified_prs=set(),
                committed_prs=set(),
                claude_prs=set(),
                user_name=None,
                user_email=None,
            )
        )
        assert run_claude_mock.call_count == 0
        prepare_mock.assert_not_called()
        update_done_mock.assert_called_once()
        assert processed is True
        assert cacheable is True

    def test_fix_phase_failure_does_not_update_last_reviewed_head(
        self, mocker, tmp_path
    ):
        """fix セッションが CalledProcessError で失敗した時 last_reviewed_head は更新されない。"""
        import subprocess

        ctx = _build_ctx(tmp_path)
        pr_data = _pr_data()
        self_review = _make_self_review([_finding()])

        mocker.patch.object(
            auto_fixer,
            "_run_git",
            return_value=_FakeRunGitResult(stdout=""),
        )
        mocker.patch.object(auto_fixer, "set_pr_running_label")
        mocker.patch.object(
            auto_fixer,
            "run_claude_prompt",
            side_effect=subprocess.CalledProcessError(1, "claude", "out", "err"),
        )
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(
            auto_fixer,
            "append_self_review_entry",
            side_effect=capture,
        )

        _, _, _, fix_failed = auto_fixer._run_fix_phase(
            ctx,
            pr_data,
            tmp_path,
            self_review,
            StateComment(github_comment_id=1, body=""),
            [],
            [],
        )
        assert fix_failed is True
        assert captured["kwargs"]["update_last_reviewed_head"] is False


class TestResolveActionTargets:
    def test_pull_request_event(self, tmp_path, monkeypatch):
        event_path = tmp_path / "event.json"
        event_path.write_text('{"pull_request": {"number": 42}}')
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        targets = auto_fixer._resolve_action_targets("owner/repo")
        assert targets == [42]

    def test_unsupported_event_returns_empty(self, tmp_path, monkeypatch):
        event_path = tmp_path / "event.json"
        event_path.write_text("{}")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "check_suite")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        assert auto_fixer._resolve_action_targets("owner/repo") == []

    def test_workflow_dispatch_with_pr_number(self, tmp_path, monkeypatch):
        event_path = tmp_path / "event.json"
        event_path.write_text('{"inputs": {"pr-number": "99"}}')
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        assert auto_fixer._resolve_action_targets("owner/repo") == [99]
