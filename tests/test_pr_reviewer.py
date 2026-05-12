"""Unit tests for pr_reviewer helpers."""

from __future__ import annotations

import pytest

import pr_reviewer
from subprocess_helpers import SubprocessError


def test_fetch_pr_details_no_review_fields(mocker, make_cmd_result):
    """The --json arg should not include reviews/comments/reviewThreads anymore."""
    captured: dict = {}

    def fake_run_command(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return make_cmd_result(
            '{"number": 1, "title": "t", "headRefOid": "abc", "baseRefName": "main", "headRefName": "feat"}'
        )

    mocker.patch("pr_reviewer.run_command", side_effect=fake_run_command)
    mocker.patch.object(pr_reviewer, "_fetch_check_runs_via_rest", return_value=[])
    mocker.patch.object(
        pr_reviewer, "_fetch_classic_statuses_via_rest", return_value=[]
    )

    data = pr_reviewer.fetch_pr_details("owner/repo", 1)
    assert data.get("number") == 1
    json_arg_idx = captured["cmd"].index("--json")
    fields = captured["cmd"][json_arg_idx + 1]
    assert "reviews" not in fields
    assert "comments" not in fields
    assert "reviewThreads" not in fields


def test_fetch_pr_details_fetch_failure_raises(mocker, make_cmd_result):
    mocker.patch(
        "pr_reviewer.run_command",
        return_value=make_cmd_result("", returncode=1, stderr="boom"),
    )
    with pytest.raises(SubprocessError):
        pr_reviewer.fetch_pr_details("owner/repo", 1)


def test_review_and_comment_fetchers_removed():
    """Old fetch helpers should no longer be exported."""
    assert not hasattr(pr_reviewer, "fetch_pr_reviews")
    assert not hasattr(pr_reviewer, "fetch_pr_review_comments")
    assert not hasattr(pr_reviewer, "fetch_review_threads")
    assert not hasattr(pr_reviewer, "fetch_issue_comments")
    assert not hasattr(pr_reviewer, "resolve_review_thread")
