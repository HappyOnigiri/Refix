"""Unit tests for pr_reviewer helpers."""

from unittest.mock import Mock, patch

import pytest

import pr_reviewer
from subprocess_helpers import SubprocessError


def test_fetch_pr_reviews_normalizes_ids_and_urls():
    result = Mock(
        returncode=0,
        stdout='[[{"id": 123, "user": {"login": "coderabbitai[bot]"}, "body": "fix", "state": "COMMENTED", "submitted_at": "2026-03-11T12:00:00Z", "html_url": "https://github.com/owner/repo/pull/1#pullrequestreview-123"}]]',
        stderr="",
    )

    with patch("pr_reviewer.run_command", return_value=result):
        reviews = pr_reviewer.fetch_pr_reviews("owner/repo", 1)

    assert reviews == [
        {
            "id": "r123",
            "databaseId": 123,
            "author": {"login": "coderabbitai[bot]"},
            "body": "fix",
            "state": "COMMENTED",
            "submittedAt": "2026-03-11T12:00:00Z",
            "url": "https://github.com/owner/repo/pull/1#pullrequestreview-123",
        }
    ]


def test_fetch_pr_review_comments_flattens_paginated_response():
    result = Mock(
        returncode=0,
        stdout='[[{"id": 10, "body": "a"}], [{"id": 11, "body": "b"}]]',
        stderr="",
    )

    with patch("pr_reviewer.run_command", return_value=result):
        comments = pr_reviewer.fetch_pr_review_comments("owner/repo", 1)

    assert comments == [{"id": 10, "body": "a"}, {"id": 11, "body": "b"}]


def test_fetch_issue_comments_flattens_paginated_response():
    result = Mock(
        returncode=0,
        stdout='[[{"id": 21, "body": "a"}], [{"id": 22, "body": "b"}]]',
        stderr="",
    )

    with patch("pr_reviewer.run_command", return_value=result):
        comments = pr_reviewer.fetch_issue_comments("owner/repo", 1)

    assert comments == [{"id": 21, "body": "a"}, {"id": 22, "body": "b"}]


def test_fetch_pr_reviews_subprocess_error_raises():
    with patch("pr_reviewer.run_command", side_effect=SubprocessError("net error")):
        with pytest.raises(RuntimeError, match="Failed to fetch PR reviews"):
            pr_reviewer.fetch_pr_reviews("owner/repo", 1)


def test_fetch_pr_reviews_nonzero_exit_raises():
    result = Mock(returncode=1, stdout="", stderr="API error")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(RuntimeError, match="Failed to fetch PR reviews"):
            pr_reviewer.fetch_pr_reviews("owner/repo", 1)


def test_fetch_pr_reviews_parse_failure_raises():
    result = Mock(returncode=0, stdout="not-json", stderr="")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(RuntimeError, match="Failed to parse PR reviews response"):
            pr_reviewer.fetch_pr_reviews("owner/repo", 1)


def test_fetch_pr_review_comments_subprocess_error_raises():
    with patch("pr_reviewer.run_command", side_effect=SubprocessError("net error")):
        with pytest.raises(RuntimeError, match="Failed to fetch review comments"):
            pr_reviewer.fetch_pr_review_comments("owner/repo", 1)


def test_fetch_pr_review_comments_nonzero_exit_raises():
    result = Mock(returncode=1, stdout="", stderr="API error")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(RuntimeError, match="Failed to fetch review comments"):
            pr_reviewer.fetch_pr_review_comments("owner/repo", 1)


def test_fetch_pr_review_comments_parse_failure_raises():
    result = Mock(returncode=0, stdout="not-json", stderr="")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(
            RuntimeError, match="Failed to parse review comments response"
        ):
            pr_reviewer.fetch_pr_review_comments("owner/repo", 1)


def test_fetch_review_threads_subprocess_error_raises():
    with patch("pr_reviewer.run_command", side_effect=SubprocessError("net error")):
        with pytest.raises(RuntimeError, match="Failed to fetch review threads"):
            pr_reviewer.fetch_review_threads("owner/repo", 1)


def test_fetch_review_threads_nonzero_exit_raises():
    result = Mock(returncode=1, stdout="", stderr="API error")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(RuntimeError, match="Failed to fetch review threads"):
            pr_reviewer.fetch_review_threads("owner/repo", 1)


def test_fetch_review_threads_parse_failure_raises():
    result = Mock(returncode=0, stdout="not-json", stderr="")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(
            RuntimeError, match="Failed to parse review threads response"
        ):
            pr_reviewer.fetch_review_threads("owner/repo", 1)


def test_resolve_review_thread_subprocess_error_raises():
    with patch("pr_reviewer.run_command", side_effect=SubprocessError("net error")):
        with pytest.raises(RuntimeError, match="Failed to resolve thread"):
            pr_reviewer.resolve_review_thread("thread-node-id")


def test_resolve_review_thread_nonzero_exit_raises():
    result = Mock(returncode=1, stdout="", stderr="permission denied")
    with patch("pr_reviewer.run_command", return_value=result):
        with pytest.raises(RuntimeError, match="Failed to resolve thread"):
            pr_reviewer.resolve_review_thread("thread-node-id")


def test_resolve_review_thread_success_returns_true():
    result = Mock(returncode=0, stdout='{"data": {}}', stderr="")
    with patch("pr_reviewer.run_command", return_value=result):
        assert pr_reviewer.resolve_review_thread("thread-node-id") is True
