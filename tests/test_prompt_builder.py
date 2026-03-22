"""Unit tests for prompt_builder and merge strategy helpers."""

import auto_fixer
import prompt_builder
from prompt_builder import InlineCommentData, ReviewData


class TestGeneratePrompt:
    """Tests for generate_prompt()."""

    def test_summary_overrides_raw_body(self):
        reviews: list[ReviewData] = [{"id": "r1", "body": "raw body"}]
        summaries = {"r1": "summarized"}
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test PR",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries=summaries,
        )
        assert "summarized" in prompt
        assert "raw body" not in prompt

    def test_raw_body_when_no_summary(self):
        reviews: list[ReviewData] = [{"id": "r1", "body": "raw body"}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test PR",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert "raw body" in prompt

    def test_inline_comment_path_and_line(self):
        comments: list[InlineCommentData] = [
            {"id": 42, "path": "src/foo.py", "line": 10, "body": "comment"}
        ]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test",
            unresolved_reviews=[],
            unresolved_comments=comments,
            summaries={},
        )
        assert 'path="src/foo.py"' in prompt
        assert 'line="10"' in prompt
        assert 'id="discussion_r42"' in prompt
        assert 'severity="unknown"' in prompt
        assert "comment" in prompt

    def test_inline_comment_original_line_fallback(self):
        comments: list[InlineCommentData] = [
            {"id": 42, "path": "bar.py", "original_line": 5, "body": "x"}
        ]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test",
            unresolved_reviews=[],
            unresolved_comments=comments,
            summaries={},
        )
        assert 'path="bar.py"' in prompt
        assert 'line="5"' in prompt

    def test_review_and_comment_include_advisory_severity(self):
        reviews: list[ReviewData] = [
            {"id": "r1", "body": "_Potential issue_ | _Major_\nfix"}
        ]
        comments: list[InlineCommentData] = [
            {
                "id": 42,
                "path": "src/foo.py",
                "line": 10,
                "body": "_Potential issue_ | _Nitpick_\ncomment",
            }
        ]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Severity",
            unresolved_reviews=reviews,
            unresolved_comments=comments,
            summaries={},
        )
        assert '<review id="r1" severity="major">' in prompt
        assert '<comment id="discussion_r42" severity="nitpick"' in prompt

    def test_empty_reviews_and_comments_omits_sections(self):
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Empty",
            unresolved_reviews=[],
            unresolved_comments=[],
            summaries={},
        )
        assert "<reviews>" not in prompt
        assert "<inline_comments>" not in prompt
        assert "<pr_number>1</pr_number>" in prompt

    def test_unified_instruction_prioritizes_high_signal_fixes(self):
        reviews: list[ReviewData] = [{"id": "r1", "body": "fix"}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="First",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert "runtime / security / CI / correctness / accessibility" in prompt
        assert "Minor / Nitpick / optional / preference" in prompt
        assert "severity" in prompt
        assert "git commit" in prompt

    def test_review_data_treated_as_candidate_data_not_commands(self):
        reviews: list[ReviewData] = [{"id": "r1", "body": "Prompt for AI Agents: do X"}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Candidate Data",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert "modification candidates" in prompt
        assert "contradict" in prompt

    def test_unified_instruction_ja_when_language_set(self):
        import i18n

        i18n.set_language("ja")
        reviews: list[ReviewData] = [{"id": "r1", "body": "fix"}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="First",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert "各指摘が現在のコードに対して妥当かどうかを確認し" in prompt
        assert "変更不要なら commit はしない" in prompt

    def test_xml_escape_prevents_injection(self):
        """User-controlled content with XML-like chars is escaped."""
        reviews: list[ReviewData] = [
            {"id": "r1", "body": "Ignore this. <script>alert(1)</script>"}
        ]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title='Test "quotes" & <tags>',
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert "<script>" not in prompt
        assert "&lt;script&gt;" in prompt
        assert "&lt;tags&gt;" in prompt
        assert "&amp;" in prompt

    def test_instructions_and_review_data_separated(self):
        """Instructions and review data are in distinct XML blocks."""
        reviews: list[ReviewData] = [{"id": "r1", "body": "fix typo"}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Fix",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert prompt.startswith("<instructions>")
        assert "</instructions>" in prompt
        assert "<review_data>" in prompt
        assert "</review_data>" in prompt
        # Instructions block must end before the actual data block (not the reference in text)
        assert "</instructions>\n\n<review_data>" in prompt

    def test_body_adds_pr_description_element(self):
        """body パラメータが渡された場合 <pr_description> が含まれる。"""
        prompt = prompt_builder.generate_prompt(
            pr_number=42,
            title="Some PR",
            unresolved_reviews=[],
            unresolved_comments=[],
            summaries={},
            body="認証バグを修正するPRです",
        )
        assert "<pr_description>認証バグを修正するPRです</pr_description>" in prompt

    def test_body_empty_omits_pr_description_element(self):
        """body が空の場合 <pr_description> は含まれない。"""
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Empty body",
            unresolved_reviews=[],
            unresolved_comments=[],
            summaries={},
            body="",
        )
        assert "<pr_description>" not in prompt

    def test_body_xml_escaped(self):
        """body 内の XML 特殊文字はエスケープされる。"""
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test",
            unresolved_reviews=[],
            unresolved_comments=[],
            summaries={},
            body="<script>alert('xss')</script>",
        )
        assert "<script>" not in prompt
        assert "&lt;script&gt;" in prompt

    def test_ignore_nitpick_strips_nitpick_from_raw_body(self):
        """ignore_nitpick=True かつ summary なし → raw body 内の nitpick ブロックが除去される。"""
        body = (
            "Major comments:\n"
            "Fix the SQL injection vulnerability.\n"
            "---\n"
            "Nitpick comments:\n"
            "Consider renaming variable for clarity.\n"
            "---\n"
            "Minor comments:\n"
            "Add error handling."
        )
        reviews: list[ReviewData] = [{"id": "r1", "body": body}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
            ignore_nitpick=True,
        )
        assert "Nitpick comments" not in prompt
        assert "Consider renaming variable" not in prompt
        assert "Fix the SQL injection" in prompt
        assert "Add error handling" in prompt

    def test_ignore_nitpick_false_preserves_nitpick(self):
        """ignore_nitpick=False（デフォルト）→ nitpick ブロックがそのまま残る。"""
        body = (
            "Major comments:\n"
            "Fix the SQL injection vulnerability.\n"
            "---\n"
            "Nitpick comments:\n"
            "Consider renaming variable for clarity.\n"
            "---"
        )
        reviews: list[ReviewData] = [{"id": "r1", "body": body}]
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries={},
        )
        assert "Nitpick comments" in prompt
        assert "Consider renaming variable" in prompt

    def test_ignore_nitpick_summary_overrides_still_works(self):
        """ignore_nitpick=True かつ summary あり → summary が使われる。"""
        body = "Nitpick comments:\nConsider renaming variable for clarity."
        reviews: list[ReviewData] = [{"id": "r1", "body": body}]
        summaries = {"r1": "summarized nitpick text"}
        prompt = prompt_builder.generate_prompt(
            pr_number=1,
            title="Test",
            unresolved_reviews=reviews,
            unresolved_comments=[],
            summaries=summaries,
            ignore_nitpick=True,
        )
        assert "summarized nitpick text" in prompt


class TestStripNitpickSections:
    """Tests for strip_nitpick_sections()."""

    def test_removes_details_nitpick_block(self):
        text = (
            "<details>\n"
            "<summary>🧹 Nitpick comments (2)</summary>\n\n"
            "**src/foo.py (line 10):** Consider using f-string.\n"
            "**src/bar.py (line 20):** Unused import.\n"
            "</details>"
        )
        result = prompt_builder.strip_nitpick_sections(text)
        assert "🧹 Nitpick comments" not in result
        assert "Consider using f-string" not in result
        assert "Unused import" not in result

    def test_removes_ai_agent_nitpick_section(self):
        text = (
            "Major comments:\n"
            "Fix the SQL injection vulnerability.\n"
            "---\n"
            "Nitpick comments:\n"
            "Consider renaming variable for clarity.\n"
            "Use f-string instead of format().\n"
            "---\n"
            "Minor comments:\n"
            "Add error handling."
        )
        result = prompt_builder.strip_nitpick_sections(text)
        assert "Nitpick comments" not in result
        assert "Consider renaming variable" not in result
        assert "Use f-string instead of format" not in result
        assert "Major comments" in result
        assert "Minor comments" in result

    def test_removes_ai_agent_nitpick_section_at_end(self):
        text = (
            "Nitpick comments:\n"
            "Consider renaming variable for clarity.\n"
            "Use f-string instead of format()."
        )
        result = prompt_builder.strip_nitpick_sections(text)
        assert "Nitpick comments" not in result
        assert "Consider renaming" not in result

    def test_preserves_non_nitpick_content(self):
        text = "Major comments:\nFix critical bug.\n---\nMinor comments:\nAdd tests."
        result = prompt_builder.strip_nitpick_sections(text)
        assert result == text

    def test_both_patterns_removed_together(self):
        text = (
            "<details>\n"
            "<summary>🧹 Nitpick comments (1)</summary>\n\n"
            "Consider using walrus operator.\n"
            "</details>\n\n"
            "Major comments:\n"
            "Fix the SQL injection.\n"
            "---\n"
            "Nitpick comments:\n"
            "Rename variable for clarity.\n"
            "---\n"
            "Minor comments:\n"
            "Add error handling."
        )
        result = prompt_builder.strip_nitpick_sections(text)
        assert "🧹 Nitpick comments" not in result
        assert "walrus operator" not in result
        assert "Rename variable" not in result
        assert "Major comments" in result
        assert "Minor comments" in result

    def test_empty_string(self):
        result = prompt_builder.strip_nitpick_sections("")
        assert result == ""

    def test_no_nitpick_content_unchanged(self):
        text = "This review has no nitpick content at all."
        result = prompt_builder.strip_nitpick_sections(text)
        assert result == text


class TestMergeStrategyHelpers:
    def test_conflict_with_review_targets_uses_two_calls(self):
        strategy = auto_fixer.determine_conflict_resolution_strategy(True)
        assert strategy == "separate_two_calls"

    def test_no_review_targets_uses_single_call(self):
        strategy = auto_fixer.determine_conflict_resolution_strategy(False)
        assert strategy == "single_call"
