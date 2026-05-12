"""Unit tests for state_manager."""

from __future__ import annotations

import json

import pytest

import i18n
import state_manager
from state_manager import (
    LAST_REVIEWED_HEAD_MARKER_PATTERN,
    REFIX_LOG_SECTION_END_MARKER,
    REFIX_LOG_SECTION_START_MARKER,
    STATE_COMMENT_MARKER,
    STATE_COMMENT_MAX_LENGTH,
    StateComment,
    append_refix_log_entry,
    parse_last_reviewed_head,
    parse_refix_log,
    render_refix_log_section,
    render_state_comment,
    upsert_state_comment,
)
from type_defs import LoggedCommit, SelfReviewFinding, SelfReviewLogEntry


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language("en")


def make_finding(
    *,
    severity: str = "minor",
    path: str = "src/foo.py",
    line: int | None = 42,
    title: str = "Sample issue",
    body: str = "This is the body explaining the issue.",
    fix_approach: str = "Adjust the off-by-one and propagate to callers.",
) -> SelfReviewFinding:
    return SelfReviewFinding(
        finding_id="",
        severity=severity,
        path=path,
        line=line,
        title=title,
        body=body,
        fix_approach=fix_approach,
    )


def make_entry(
    *,
    head_sha: str = "abcdef1234567890",
    reviewed_at: str = "2026-05-12 14:30:00 JST",
    summary: str = "Two issues found in src/foo.py.",
    findings: list[SelfReviewFinding] | None = None,
    commits: list[LoggedCommit] | None = None,
    fix_failed: bool = False,
) -> SelfReviewLogEntry:
    if findings is None:
        findings = [
            make_finding(severity="major", title="Major issue", line=10),
            make_finding(severity="minor", title="Minor issue", line=20),
        ]
    if commits is None:
        commits = [
            LoggedCommit(sha="aaaaaaa", message="fix: major issue"),
            LoggedCommit(sha="bbbbbbb", message="fix: minor issue"),
        ]
    return SelfReviewLogEntry(
        head_sha=head_sha,
        reviewed_at=reviewed_at,
        summary=summary,
        findings=findings,
        commits=commits,
        fix_failed=fix_failed,
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


class TestRenderRefixLogSection:
    def test_empty_entries_returns_empty(self):
        assert render_refix_log_section([]) == ""

    def test_rendered_section_contains_markers(self):
        section = render_refix_log_section([make_entry()])
        assert REFIX_LOG_SECTION_START_MARKER in section
        assert REFIX_LOG_SECTION_END_MARKER in section
        assert "abcdef1" in section
        assert "**Findings:**" in section
        assert "[major]" in section
        assert "Applied commits" in section

    def test_no_findings_entry_uses_no_findings_string(self):
        entry = SelfReviewLogEntry(
            head_sha="abc1234abcdef",
            reviewed_at="2026-05-12 14:30:00 JST",
            summary="",
            findings=[],
            commits=[],
            fix_failed=False,
        )
        section = render_refix_log_section([entry])
        assert "No issues found" in section
        assert "Applied commits" not in section

    def test_fix_failed_entry_includes_notice(self):
        entry = make_entry(commits=[], fix_failed=True)
        section = render_refix_log_section([entry])
        assert "Fix failed" in section

    def test_renders_commit_links_when_repo_and_pr_given(self):
        entry = make_entry(
            head_sha="abcdef1234567890abcdef1234567890abcdef12",
            commits=[
                LoggedCommit(
                    sha="1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    message="fix: a",
                ),
            ],
        )
        section = render_refix_log_section([entry], repo="owner/repo", pr_number=42)
        # Entry header is a link
        assert (
            "[abcdef1](https://github.com/owner/repo/pull/42/commits/"
            "abcdef1234567890abcdef1234567890abcdef12)" in section
        )
        # Commit row is a link
        assert (
            "- [1111111](https://github.com/owner/repo/pull/42/commits/"
            "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) fix: a" in section
        )

    def test_falls_back_to_inline_code_when_repo_missing(self):
        entry = make_entry()
        section = render_refix_log_section([entry])
        assert "`abcdef1`" in section
        assert "https://github.com" not in section


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
        long_body = "x" * 400
        findings = [
            make_finding(title=f"Finding {i}", body=long_body) for i in range(3)
        ]
        entries = [
            make_entry(head_sha=f"head{i:03d}1234abc", findings=findings)
            for i in range(5)
        ]
        body = render_state_comment(entries)
        # 最新（末尾）が残り、最古（先頭）が落ちる
        assert "head0041234abc"[:7] in body


class TestParseRefixLogRoundTrip:
    def test_render_then_parse_recovers_entry(self):
        entry = make_entry()
        section = render_refix_log_section([entry])
        body = f"{STATE_COMMENT_MARKER}\n" + section
        parsed = parse_refix_log(body)
        assert len(parsed) == 1
        recovered = parsed[0]
        assert recovered.head_sha == entry.head_sha
        assert recovered.reviewed_at == entry.reviewed_at
        assert len(recovered.findings) == len(entry.findings)
        assert recovered.findings[0].severity == entry.findings[0].severity
        assert recovered.findings[0].title == entry.findings[0].title
        assert recovered.commits == entry.commits

    def test_multiple_entries_preserve_order(self):
        e1 = make_entry(
            head_sha="aaaaaaa1234567abc", reviewed_at="2026-05-01 00:00:00 JST"
        )
        e2 = make_entry(
            head_sha="bbbbbbb1234567abc", reviewed_at="2026-05-02 00:00:00 JST"
        )
        body = f"{STATE_COMMENT_MARKER}\n" + render_refix_log_section([e1, e2])
        parsed = parse_refix_log(body)
        assert len(parsed) == 2
        assert parsed[0].head_sha == e1.head_sha
        assert parsed[1].head_sha == e2.head_sha

    def test_multiple_entries_are_separated_by_hr(self):
        """エントリ間に `---` の水平線が挿入される。"""
        e1 = make_entry(
            head_sha="aaaaaaa1234567abc", reviewed_at="2026-05-01 00:00:00 JST"
        )
        e2 = make_entry(
            head_sha="bbbbbbb1234567abc", reviewed_at="2026-05-02 00:00:00 JST"
        )
        section = render_refix_log_section([e1, e2])
        assert "\n\n---\n\n" in section
        # 単一エントリのときは区切りが入らない
        single = render_refix_log_section([e1])
        assert "\n---\n" not in single

    def test_fix_failed_round_trip(self):
        entry = make_entry(commits=[], fix_failed=True)
        body = f"{STATE_COMMENT_MARKER}\n" + render_refix_log_section([entry])
        parsed = parse_refix_log(body)
        assert len(parsed) == 1
        assert parsed[0].fix_failed is True
        assert parsed[0].commits == []

    def test_link_format_round_trip(self):
        entry = make_entry(
            head_sha="abcdef1234567890abcdef1234567890abcdef12",
            commits=[
                LoggedCommit(
                    sha="1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    message="fix: a",
                ),
                LoggedCommit(
                    sha="2222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    message="fix: b",
                ),
            ],
        )
        section = render_refix_log_section([entry], repo="owner/repo", pr_number=42)
        body = f"{STATE_COMMENT_MARKER}\n" + section
        parsed = parse_refix_log(body)
        assert len(parsed) == 1
        recovered = parsed[0]
        assert recovered.head_sha == entry.head_sha
        assert len(recovered.commits) == 2
        assert recovered.commits[0].sha == "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert recovered.commits[0].message == "fix: a"


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
            refix_log=[make_entry()],
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
            refix_log=[make_entry()],
            _preloaded_state=preloaded,
        )
        assert any(
            "issues/comments/42" in " ".join(call.args[0]) and "PATCH" in call.args[0]
            for call in run_command_mock.call_args_list
        )


class TestAppendRefixLogEntry:
    def test_appends_entry_at_tail_and_updates_head(self, mocker):
        existing = make_entry(head_sha="oldhead1234567abc")
        preloaded = StateComment(
            github_comment_id=10,
            body="",
            refix_log=[existing],
            last_reviewed_head="oldhead1234567abc",
        )
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(
            state_manager,
            "upsert_state_comment",
            side_effect=capture,
        )
        new_entry = make_entry(head_sha="newhead1234567abc")
        append_refix_log_entry(
            "owner/repo",
            5,
            new_entry,
            _preloaded_state=preloaded,
        )
        kwargs = captured["kwargs"]
        # 末尾に新エントリ
        assert kwargs["refix_log"][0].head_sha == "oldhead1234567abc"
        assert kwargs["refix_log"][1].head_sha == "newhead1234567abc"
        assert kwargs["last_reviewed_head"] == "newhead1234567abc"

    def test_failed_fix_does_not_update_head(self, mocker):
        preloaded = StateComment(
            github_comment_id=10,
            body="",
            refix_log=[],
            last_reviewed_head="oldhead1234567abc",
        )
        captured: dict = {}

        def capture(*args, **kwargs):
            captured["kwargs"] = kwargs

        mocker.patch.object(
            state_manager,
            "upsert_state_comment",
            side_effect=capture,
        )
        append_refix_log_entry(
            "owner/repo",
            5,
            make_entry(head_sha="failedhead1234abc", fix_failed=True),
            update_last_reviewed_head=False,
            _preloaded_state=preloaded,
        )
        assert captured["kwargs"]["last_reviewed_head"] == "oldhead1234567abc"


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
        assert result.refix_log == []
        assert result.last_reviewed_head is None
