"""Unit tests for state_manager."""

from __future__ import annotations

import json

import pytest

import i18n
import state_manager
from state_manager import (
    LAST_REVIEWED_HEAD_MARKER_PATTERN,
    SELF_REVIEW_LOG_SECTION_END_MARKER,
    SELF_REVIEW_LOG_SECTION_START_MARKER,
    STATE_COMMENT_MARKER,
    STATE_COMMENT_MAX_LENGTH,
    StateComment,
    append_self_review_entry,
    parse_last_reviewed_head,
    parse_self_review_log,
    render_self_review_log_section,
    render_state_comment,
    upsert_state_comment,
)
from type_defs import SelfReviewLogEntry


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language("en")


def make_entry(
    *,
    head_sha: str = "abcdef1234567890",
    reviewed_at: str = "2026-05-12 14:30:00 JST",
    findings: int = 2,
    breakdown: dict[str, int] | None = None,
    commit_shas: list[str] | None = None,
    raw_xml: str | None = "<self_review/>",
) -> SelfReviewLogEntry:
    return SelfReviewLogEntry(
        head_sha=head_sha,
        reviewed_at=reviewed_at,
        finding_count=findings,
        severity_breakdown=breakdown
        or {"critical": 0, "major": 1, "minor": 1, "nitpick": 0},
        commit_shas=commit_shas or ["aaaaaaa", "bbbbbbb"],
        raw_xml=raw_xml,
    )


class TestParseLastReviewedHead:
    def test_returns_sha_when_marker_present(self):
        body = (
            f"{STATE_COMMENT_MARKER}\n"
            "<!-- refix-last-reviewed-head: abcdef1234567890 -->\n"
            "rest"
        )
        assert parse_last_reviewed_head(body) == "abcdef1234567890"

    def test_returns_none_when_marker_missing(self):
        assert parse_last_reviewed_head("no marker here") is None

    def test_returns_none_for_empty_body(self):
        assert parse_last_reviewed_head("") is None

    def test_pattern_matches_short_sha(self):
        match = LAST_REVIEWED_HEAD_MARKER_PATTERN.search(
            "<!-- refix-last-reviewed-head: abcd -->"
        )
        assert match is not None
        assert match.group(1) == "abcd"


class TestRenderSelfReviewLogSection:
    def test_empty_entries_returns_empty(self):
        assert render_self_review_log_section([]) == ""

    def test_rendered_section_contains_markers(self):
        section = render_self_review_log_section([make_entry()])
        assert SELF_REVIEW_LOG_SECTION_START_MARKER in section
        assert SELF_REVIEW_LOG_SECTION_END_MARKER in section
        assert "abcdef1" in section
        assert "Findings: 2" in section

    def test_no_findings_entry_uses_no_findings_string(self):
        entry = SelfReviewLogEntry(
            head_sha="abc1234",
            reviewed_at="2026-05-12 14:30:00 JST",
            finding_count=0,
            severity_breakdown={"critical": 0, "major": 0, "minor": 0, "nitpick": 0},
            commit_shas=[],
            raw_xml=None,
        )
        section = render_self_review_log_section([entry])
        assert "No issues found" in section
        assert "```xml" not in section


class TestRenderStateComment:
    def test_includes_last_reviewed_head_marker(self):
        body = render_state_comment(
            [],
            last_reviewed_head="abcdef1234567890",
        )
        assert "<!-- refix-last-reviewed-head: abcdef1234567890 -->" in body

    def test_within_size_limit(self):
        body = render_state_comment(
            [
                make_entry(reviewed_at=f"2026-05-{i:02d} 00:00:00 JST")
                for i in range(1, 5)
            ],
            workflow_status="running",
            last_reviewed_head="abc123",
        )
        assert len(body) <= STATE_COMMENT_MAX_LENGTH

    def test_trims_oldest_entries_when_exceeding_limit(self, monkeypatch):
        monkeypatch.setattr(state_manager, "STATE_COMMENT_MAX_LENGTH", 1500)
        long_xml = "x" * 400
        entries = [
            make_entry(head_sha=f"head{i:03d}1234", raw_xml=long_xml) for i in range(5)
        ]
        body = render_state_comment(entries)
        assert "head0001234"[:7] in body


class TestParseSelfReviewLogRoundTrip:
    def test_render_then_parse_recovers_entries(self):
        entry = make_entry()
        section = render_self_review_log_section([entry])
        body = f"{STATE_COMMENT_MARKER}\n" + section
        parsed = parse_self_review_log(body)
        assert len(parsed) == 1
        assert parsed[0].head_sha == entry.head_sha
        assert parsed[0].finding_count == entry.finding_count
        assert parsed[0].severity_breakdown == entry.severity_breakdown
        assert parsed[0].commit_shas == entry.commit_shas

    def test_multiple_entries(self):
        e1 = make_entry(
            head_sha="aaaaaaa1234567", reviewed_at="2026-05-01 00:00:00 JST"
        )
        e2 = make_entry(
            head_sha="bbbbbbb1234567", reviewed_at="2026-05-02 00:00:00 JST"
        )
        body = f"{STATE_COMMENT_MARKER}\n" + render_self_review_log_section([e1, e2])
        parsed = parse_self_review_log(body)
        assert len(parsed) == 2
        assert parsed[0].head_sha == e1.head_sha
        assert parsed[1].head_sha == e2.head_sha


class TestUpsertStateComment:
    def test_calls_pr_comment_when_no_existing(self, mocker, make_cmd_result):
        mocker.patch.object(state_manager, "_use_local_state", False)
        mocker.patch.object(
            state_manager,
            "load_state_comment",
            return_value=StateComment(github_comment_id=None, body=""),
        )
        run_command_mock = mocker.patch.object(
            state_manager,
            "run_command",
            return_value=make_cmd_result(""),
        )
        upsert_state_comment(
            "owner/repo",
            123,
            self_review_log=[make_entry()],
            last_reviewed_head="abcdef1234567890",
        )
        assert any(
            call.args[0][:3] == ["gh", "pr", "comment"]
            for call in run_command_mock.call_args_list
        )

    def test_patches_existing_comment(self, mocker, make_cmd_result):
        mocker.patch.object(state_manager, "_use_local_state", False)
        preloaded = StateComment(github_comment_id=42, body="existing")
        run_command_mock = mocker.patch.object(
            state_manager,
            "run_command",
            return_value=make_cmd_result(""),
        )
        upsert_state_comment(
            "owner/repo",
            123,
            self_review_log=[make_entry()],
            _preloaded_state=preloaded,
        )
        assert any(
            "issues/comments/42" in " ".join(call.args[0]) and "PATCH" in call.args[0]
            for call in run_command_mock.call_args_list
        )


class TestAppendSelfReviewEntry:
    def test_prepends_entry_and_updates_head(self, mocker):
        existing = make_entry(head_sha="oldhead1234567")
        preloaded = StateComment(
            github_comment_id=10,
            body="",
            self_review_log=[existing],
            last_reviewed_head="oldhead1234567",
        )
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(
            state_manager,
            "upsert_state_comment",
            side_effect=capture,
        )
        new_entry = make_entry(head_sha="newhead1234567")
        append_self_review_entry(
            "owner/repo",
            5,
            new_entry,
            _preloaded_state=preloaded,
        )
        kwargs = captured["kwargs"]
        assert kwargs["self_review_log"][0].head_sha == "newhead1234567"
        assert kwargs["self_review_log"][1].head_sha == "oldhead1234567"
        assert kwargs["last_reviewed_head"] == "newhead1234567"

    def test_failed_fix_does_not_update_head(self, mocker):
        preloaded = StateComment(
            github_comment_id=10,
            body="",
            self_review_log=[],
            last_reviewed_head="oldhead1234567",
        )
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(
            state_manager,
            "upsert_state_comment",
            side_effect=capture,
        )
        append_self_review_entry(
            "owner/repo",
            5,
            make_entry(head_sha="failedhead1234"),
            update_last_reviewed_head=False,
            _preloaded_state=preloaded,
        )
        assert captured["kwargs"]["last_reviewed_head"] == "oldhead1234567"


class TestLoadStateCommentNewFormat:
    def test_legacy_table_body_loads_without_crash(self, mocker, make_cmd_result):
        legacy_body = (
            f"{STATE_COMMENT_MARKER}\n"
            "### 🤖 Refix Status\n\n"
            "| Comment ID | Processed At |\n"
            "|---|---|\n"
            "| r123 | 2025-01-01 |\n"
            "<!-- archived-ids: r1,r2 -->\n"
        )
        comments = [{"id": 1, "body": legacy_body, "user": {"login": "testuser"}}]
        mocker.patch.object(
            state_manager,
            "run_command",
            return_value=make_cmd_result(json.dumps([comments])),
        )
        mocker.patch.object(
            state_manager, "_get_authenticated_github_user", return_value="testuser"
        )
        result = state_manager.load_state_comment("owner/repo", 99)
        assert result.self_review_log == []
        assert result.last_reviewed_head is None
