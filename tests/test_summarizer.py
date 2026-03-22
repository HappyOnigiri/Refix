"""Unit tests for summarizer module."""

import pytest

import summarizer
from claude_limit import ClaudeCommandFailedError, ClaudeUsageLimitError


class TestSanitizeJsonText:
    """Tests for _sanitize_json_text()."""

    def test_trailing_comma_in_json_array(self):
        """Trailing comma before ] is removed so json.loads succeeds."""
        raw = '[{"id": "r1", "summary": "s1"},]'
        result = summarizer._sanitize_json_text(raw)
        import json

        parsed = json.loads(result)
        assert parsed == [{"id": "r1", "summary": "s1"}]

    def test_unescaped_newline_in_json_string(self):
        """Unescaped newline inside a JSON string value is escaped."""
        raw = '[{"id": "r1", "summary": "line1\nline2"}]'
        result = summarizer._sanitize_json_text(raw)
        import json

        parsed = json.loads(result)
        assert parsed[0]["summary"] == "line1\nline2"

    def test_markdown_code_fence_stripped(self):
        """Markdown ```json fences are removed before parsing."""
        raw = '```json\n[{"id": "r1", "summary": "s1"}]\n```\n'
        result = summarizer._sanitize_json_text(raw)
        import json

        parsed = json.loads(result.strip())
        assert parsed == [{"id": "r1", "summary": "s1"}]

    def test_unescaped_newline_with_bracket_in_text(self):
        """Summary containing [0] text with unescaped newline does not cause TypeError."""
        raw = '[{"id": "r1", "summary": "fix pull_requests[0]\nand more"}]'
        result = summarizer._sanitize_json_text(raw)
        import json

        parsed = json.loads(result)
        assert "pull_requests[0]" in parsed[0]["summary"]

    def test_trailing_comma_with_code_fence(self):
        """Combination of code fence and trailing comma is handled."""
        raw = '```json\n[{"id": "r1", "summary": "s1"},]\n```\n'
        result = summarizer._sanitize_json_text(raw)
        import json

        parsed = json.loads(result.strip())
        assert parsed == [{"id": "r1", "summary": "s1"}]


class TestSummarizeReviews:
    """Tests for summarize_reviews(). subprocess is always mocked."""

    def test_empty_input_returns_empty_dict_no_subprocess(self, mocker):
        """Zero items returns {} without calling claude."""
        mock_run = mocker.patch("summarizer.subprocess.run")
        result = summarizer.summarize_reviews([], [])
        assert result == {}
        mock_run.assert_not_called()

    def test_valid_json_array_success(self, mocker, make_cmd_result):
        """Pure JSON array parses correctly."""
        fake_stdout = (
            '[{"id": "r1", "summary": "s1"}, {"id": "discussion_r2", "summary": "s2"}]'
        )
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [{"id": 2, "body": "y"}],
        )
        assert result == {"r1": "s1", "discussion_r2": "s2"}

    def test_prefixed_text_extracts_first_json_array(self, mocker, make_cmd_result):
        """Text before JSON array is skipped, first [..] is parsed."""
        fake_stdout = 'Here is the result:\n[{"id": "a", "summary": "b"}]'
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "a", "body": "x"}],
            [],
        )
        assert result == {"a": "b"}

    def test_markdown_code_block_with_first_to_last_bracket_extraction(
        self, mocker, make_cmd_result
    ):
        """JSON wrapped in ```json block and/or with prefix/suffix messages parses via first [ to last ]."""
        fake_stdout = """Here is the summarized result:

```json
[
  {"id": "r1", "summary": "summary one"},
  {"id": "r2", "summary": "summary two"}
]
```

Hope this helps!"""
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}, {"id": "r2", "body": "y"}],
            [],
        )
        assert result == {"r1": "summary one", "r2": "summary two"}

    def test_success_logs_raw_output_in_foldable_group(
        self, capsys, mocker, make_cmd_result
    ):
        """Successful summarization also prints raw output in group logs."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result(fake_stdout, stderr="raw-stderr"),
        )
        mock_group = mocker.patch("summarizer.log_group")
        mock_endgroup = mocker.patch("summarizer.log_endgroup")
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
            silent=False,
        )

        assert result == {"r1": "s1"}
        mock_group.assert_any_call("Summarizer raw output (exit 0)")
        mock_endgroup.assert_called()
        out = capsys.readouterr().out
        assert "--- stdout ---" in out
        assert fake_stdout in out
        assert "--- stderr ---" in out
        assert "raw-stderr" in out

    def test_raw_output_preserves_leading_trailing_whitespace(
        self, capsys, mocker, make_cmd_result
    ):
        """Raw output preserves leading/trailing whitespace without stripping."""
        fake_stdout = '\n  [{"id": "r1", "summary": "s1"}]  \n'
        fake_stderr = "\nerr\n"
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result(fake_stdout, stderr=fake_stderr),
        )
        mocker.patch("summarizer.log_group")
        mocker.patch("summarizer.log_endgroup")
        summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
            silent=False,
        )
        out = capsys.readouterr().out
        stdout_marker = "  --- stdout ---\n"
        stderr_marker = "  --- stderr ---\n"
        idx_stdout = out.index(stdout_marker) + len(stdout_marker)
        idx_stderr = out.index(stderr_marker)
        assert out[idx_stdout:idx_stderr] == fake_stdout
        idx_stderr_content = idx_stderr + len(stderr_marker)
        assert (
            out[idx_stderr_content : idx_stderr_content + len(fake_stderr)]
            == fake_stderr
        )

    def test_success_silent_does_not_log_raw_output(self, mocker, make_cmd_result):
        """Silent mode suppresses raw output logs even on success."""
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result(fake_stdout, stderr="raw-stderr"),
        )
        mock_group = mocker.patch("summarizer.log_group")
        mock_endgroup = mocker.patch("summarizer.log_endgroup")
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
            silent=True,
        )

        assert result == {"r1": "s1"}
        mock_group.assert_called_once_with("Summarizer command details")
        mock_endgroup.assert_called_once()

    def test_returncode_nonzero_raises(self, mocker, make_cmd_result):
        """Failed subprocess must fail fast."""
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result("", returncode=1, stderr="error"),
        )
        with pytest.raises(ClaudeCommandFailedError):
            summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )

    def test_usage_limit_nonzero_raises(self, mocker, make_cmd_result):
        """Usage limit must fail fast instead of fallback."""
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result(
                "You've hit your limit · resets Mar 13, 4pm (UTC)", returncode=1
            ),
        )
        with pytest.raises(ClaudeUsageLimitError):
            summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )

    def test_usage_limit_phrase_in_success_output_does_not_raise(
        self, mocker, make_cmd_result
    ):
        """Success output containing marker phrase should not be misclassified."""
        fake_stdout = (
            "note: claude usage limit reached is one of known markers\n"
            '[{"id": "r1", "summary": "ok"}]'
        )
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
        )
        assert result == {"r1": "ok"}

    def test_failure_logs_raw_output_in_foldable_group(
        self, capsys, mocker, make_cmd_result
    ):
        """Failed summarization (returncode=1) prints raw output then raises."""
        fake_stdout = "some partial output"
        fake_stderr = "raw-stderr-error"
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result(fake_stdout, returncode=1, stderr=fake_stderr),
        )
        mock_group = mocker.patch("summarizer.log_group")
        mock_endgroup = mocker.patch("summarizer.log_endgroup")
        with pytest.raises(ClaudeCommandFailedError):
            summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
                silent=False,
            )

        mock_group.assert_any_call("Summarizer raw output (exit 1)")
        mock_endgroup.assert_called()
        out = capsys.readouterr().out
        assert "--- stderr ---" in out
        assert fake_stderr in out

    def test_invalid_json_returns_empty_dict(self, mocker, make_cmd_result):
        """Malformed JSON falls back to {}."""
        mocker.patch(
            "summarizer.subprocess.run",
            return_value=make_cmd_result("not valid json at all {{{"),
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
        )
        assert result == {}

    def test_subprocess_exception_raises(self, mocker):
        """subprocess.run raising an exception fails fast."""
        mocker.patch(
            "summarizer.subprocess.run",
            side_effect=FileNotFoundError("claude not found"),
        )
        with pytest.raises(ClaudeCommandFailedError):
            summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )

    def test_subprocess_timeout_raises(self, mocker):
        """subprocess.run raising TimeoutError fails fast."""
        mocker.patch(
            "summarizer.subprocess.run",
            side_effect=TimeoutError("timed out"),
        )
        with pytest.raises(ClaudeCommandFailedError):
            summarizer.summarize_reviews(
                [{"id": "r1", "body": "x"}],
                [],
            )

    def test_comment_id_normalized_to_discussion_r(self, mocker, make_cmd_result):
        """Inline comment id becomes discussion_r<id>."""
        fake_stdout = '[{"id": "discussion_r99", "summary": "ok"}]'
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [],
            [{"id": 99, "body": "comment"}],
        )
        assert result == {"discussion_r99": "ok"}

    def _capture_prompt(
        self, mocker, make_cmd_result, fake_stdout: str, reviews, comments, **kwargs
    ):
        """Helper: capture the prompt file contents written by summarize_reviews."""
        import pathlib
        import re

        written_prompts = []

        def fake_run(cmd, **kwargs_inner):
            for arg in cmd:
                m = re.search(r"Read the file (.+\.md) and follow", arg)
                if m:
                    written_prompts.append(
                        pathlib.Path(m.group(1)).read_text(encoding="utf-8")
                    )
            return make_cmd_result(fake_stdout)

        mocker.patch("summarizer.subprocess.run", side_effect=fake_run)
        result = summarizer.summarize_reviews(reviews, comments, **kwargs)
        return result, written_prompts

    def test_pr_body_included_in_prompt(self, mocker, make_cmd_result):
        """pr_body is included in the summarization prompt."""
        result, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}]',
            [{"id": "r1", "body": "x"}],
            [],
            pr_body="これはテスト用のPR概要です",
        )
        assert result == {"r1": "s1"}
        assert written_prompts, "prompt file was not written"
        assert "これはテスト用のPR概要です" in written_prompts[0]
        assert "PR Overview Data" in written_prompts[0]
        assert "<<<PR_BODY>>>" not in written_prompts[0]
        assert "<<<END_PR_BODY>>>" not in written_prompts[0]

    def test_pr_body_truncated_to_2000_chars(self, mocker, make_cmd_result):
        """pr_body longer than 2000 chars is truncated."""
        long_body = "x" * 3000
        _, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}]',
            [{"id": "r1", "body": "x"}],
            [],
            pr_body=long_body,
        )
        assert written_prompts
        assert "x" * 3000 not in written_prompts[0]
        assert "x" * 2000 in written_prompts[0]

    def test_pr_body_empty_not_included_in_prompt(self, mocker, make_cmd_result):
        """Empty pr_body does not add PR概要 section to prompt."""
        _, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}]',
            [{"id": "r1", "body": "x"}],
            [],
            pr_body="",
        )
        assert written_prompts
        assert "PR概要データ（以下は参考情報" not in written_prompts[0]

    def test_pr_body_empty_omits_pr_body_output_rule_and_format(
        self, mocker, make_cmd_result
    ):
        """Empty pr_body omits _pr_body instruction and format example from prompt."""
        _, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}]',
            [{"id": "r1", "body": "x"}],
            [],
            pr_body="",
        )
        assert written_prompts
        assert "_pr_body" not in written_prompts[0]

    def test_stage2_rejects_non_dict_array(self, mocker, make_cmd_result):
        """Stage 2 raw_decode must not accept a list of non-dict items as valid."""
        # Output has text before a valid array containing a plain list like [0]
        # followed by the real JSON array — stage 2 should skip [0] and find the real one.
        fake_stdout = (
            'note: pull_requests[0] is checked\n[{"id": "r1", "summary": "ok"}]'
        )
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
        )
        assert result == {"r1": "ok"}

    def test_ignore_nitpick_strips_body_before_summarization(
        self, mocker, make_cmd_result
    ):
        """ignore_nitpick=True → prompt ファイルに nitpick セクションが含まれない。"""
        nitpick_body = (
            "Major comments:\n"
            "Fix the SQL injection vulnerability.\n"
            "---\n"
            "Nitpick comments:\n"
            "Consider renaming variable for clarity.\n"
            "---\n"
            "Minor comments:\n"
            "Add error handling."
        )
        result, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}]',
            [{"id": "r1", "body": nitpick_body}],
            [],
            ignore_nitpick=True,
        )
        assert result == {"r1": "s1"}
        assert written_prompts, "prompt file was not written"
        assert "Nitpick comments" not in written_prompts[0]
        assert "Consider renaming variable" not in written_prompts[0]
        assert "Fix the SQL injection" in written_prompts[0]
        assert "Add error handling" in written_prompts[0]

    def test_ignore_nitpick_false_keeps_body(self, mocker, make_cmd_result):
        """ignore_nitpick=False → nitpick セクションがそのまま渡される。"""
        nitpick_body = (
            "Major comments:\n"
            "Fix the SQL injection vulnerability.\n"
            "---\n"
            "Nitpick comments:\n"
            "Consider renaming variable for clarity.\n"
            "---"
        )
        _, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}]',
            [{"id": "r1", "body": nitpick_body}],
            [],
            ignore_nitpick=False,
        )
        assert written_prompts, "prompt file was not written"
        assert "Nitpick comments" in written_prompts[0]
        assert "Consider renaming variable" in written_prompts[0]

    def test_pr_body_summary_returned_as_pr_body_key(self, mocker, make_cmd_result):
        """_pr_body key in JSON response is included in returned dict."""
        fake_stdout = '[{"id": "_pr_body", "summary": "PR概要の要約"}, {"id": "r1", "summary": "s1"}]'
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}],
            [],
            pr_body="some body",
        )
        assert result == {"_pr_body": "PR概要の要約", "r1": "s1"}

    def test_missing_id_emits_warning(self, capsys, mocker, make_cmd_result):
        """IDが欠落している場合、stderrに警告ログが出力される。"""
        # r1 は返却されるが r2 は省略されたケース
        fake_stdout = '[{"id": "r1", "summary": "s1"}]'
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        result = summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}, {"id": "r2", "body": "y"}],
            [],
        )
        assert result == {"r1": "s1"}
        err = capsys.readouterr().err
        assert "Warning: summarizer missed 1 item(s)" in err
        assert "r2" in err

    def test_no_warning_when_all_ids_present(self, capsys, mocker, make_cmd_result):
        """全IDが返却された場合、警告ログは出力されない。"""
        fake_stdout = '[{"id": "r1", "summary": "s1"}, {"id": "r2", "summary": "s2"}]'
        mocker.patch(
            "summarizer.subprocess.run", return_value=make_cmd_result(fake_stdout)
        )
        summarizer.summarize_reviews(
            [{"id": "r1", "body": "x"}, {"id": "r2", "body": "y"}],
            [],
        )
        err = capsys.readouterr().err
        assert "Warning" not in err

    def test_prompt_includes_item_count_and_completeness_instruction(
        self, mocker, make_cmd_result
    ):
        """プロンプトに件数と省略禁止の指示が含まれること。"""
        result, written_prompts = self._capture_prompt(
            mocker,
            make_cmd_result,
            '[{"id": "r1", "summary": "s1"}, {"id": "r2", "summary": "s2"}]',
            [{"id": "r1", "body": "x"}, {"id": "r2", "body": "y"}],
            [],
        )
        assert written_prompts
        prompt_text = written_prompts[0]
        assert "2" in prompt_text
        assert "omit" in prompt_text.lower() or "省略" in prompt_text
