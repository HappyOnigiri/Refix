"""Unit tests for prompt_builder."""

from __future__ import annotations

import pytest

import i18n
from prompt_builder import (
    DIFF_TRUNCATE_LIMIT,
    build_fix_prompt,
    build_self_review_prompt,
    parse_self_review_xml,
    severity_breakdown,
    truncate_diff_for_review,
)
from type_defs import SelfReviewFinding


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language("en")


_VALID_XML = """\
<self_review version="1" head_sha="abcdef1234567890" reviewed_at="2026-05-12T14:30:00+09:00">
  <summary>Found 2 issues.</summary>
  <findings>
    <finding id="f1" severity="major" path="src/foo.py" line="42">
      <title>Null reference</title>
      <body>foo() may receive None when caller short-circuits.</body>
      <suggested_fix>Add `if value is None: return DEFAULT` before line 42.</suggested_fix>
    </finding>
    <finding id="f2" severity="minor" path="src/bar.py">
      <title>Magic number</title>
      <body>86400 should be a constant.</body>
      <suggested_fix>Define SECONDS_PER_DAY = 86400 at module top.</suggested_fix>
    </finding>
  </findings>
</self_review>
"""


class TestBuildSelfReviewPrompt:
    def test_includes_instructions_and_pr_meta(self):
        prompt = build_self_review_prompt(
            pr_number=42,
            pr_title="Refactor auth",
            pr_body="body text",
            base_branch="main",
            head_sha="abc1234",
            diff_text="diff content",
            changed_files=["src/auth.py"],
            output_path="/tmp/_self_review.xml",
            language="en",
        )
        assert "<instructions>" in prompt
        assert "42" in prompt
        assert "Refactor auth" in prompt
        assert "abc1234" in prompt
        assert "src/auth.py" in prompt
        assert "/tmp/_self_review.xml" in prompt
        assert "suggested_fix" in prompt

    def test_truncates_oversize_diff(self):
        big_diff = "diff --git a/x b/x\n" + ("x" * (DIFF_TRUNCATE_LIMIT + 1000))
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_text=big_diff,
            changed_files=[],
            output_path="/tmp/r.xml",
        )
        assert "<truncated>true</truncated>" in prompt


class TestBuildFixPrompt:
    def test_includes_fix_instructions_and_inline_xml(self):
        prompt = build_fix_prompt(
            pr_number=42,
            pr_title="Refactor auth",
            base_branch="main",
            self_review_path="/tmp/_self_review.xml",
            self_review_xml=_VALID_XML,
            language="en",
        )
        assert "/tmp/_self_review.xml" in prompt
        assert "AUTHORITATIVE" in prompt
        assert "<self_review_inline>" in prompt
        assert "SECONDS_PER_DAY" in prompt

    def test_ja_instructions(self):
        i18n.set_language("ja")
        prompt = build_fix_prompt(
            pr_number=42,
            pr_title="t",
            base_branch="main",
            self_review_path="/tmp/r.xml",
            self_review_xml="<self_review/>",
            language="ja",
        )
        assert "再評価" in prompt


class TestParseSelfReviewXml:
    def test_happy_path(self):
        result = parse_self_review_xml(_VALID_XML)
        assert result.head_sha == "abcdef1234567890"
        assert result.reviewed_at == "2026-05-12T14:30:00+09:00"
        assert len(result.findings) == 2
        assert result.findings[0].finding_id == "f1"
        assert result.findings[0].severity == "major"
        assert result.findings[0].line == 42
        assert result.findings[1].line is None

    def test_empty_findings_allowed(self):
        xml = (
            '<self_review version="1" head_sha="abc1234" reviewed_at="2026-05-12">'
            "<summary>clean</summary><findings/></self_review>"
        )
        result = parse_self_review_xml(xml)
        assert result.findings == []

    def test_missing_suggested_fix_raises(self):
        xml = (
            '<self_review version="1" head_sha="abc" reviewed_at="2026-05-12">'
            "<summary>s</summary><findings>"
            '<finding id="f1" severity="major" path="src/x.py">'
            "<title>t</title><body>b</body></finding>"
            "</findings></self_review>"
        )
        with pytest.raises(ValueError, match="suggested_fix"):
            parse_self_review_xml(xml)

    def test_invalid_severity_raises(self):
        xml = (
            '<self_review version="1" head_sha="abc" reviewed_at="2026-05-12">'
            "<summary>s</summary><findings>"
            '<finding id="f1" severity="blocker" path="src/x.py">'
            "<title>t</title><body>b</body><suggested_fix>f</suggested_fix></finding>"
            "</findings></self_review>"
        )
        with pytest.raises(ValueError, match="severity"):
            parse_self_review_xml(xml)

    def test_malformed_xml_raises(self):
        with pytest.raises(ValueError):
            parse_self_review_xml("<self_review")

    def test_missing_head_sha_raises(self):
        xml = (
            '<self_review version="1" reviewed_at="2026-05-12">'
            "<summary>s</summary><findings/></self_review>"
        )
        with pytest.raises(ValueError, match="head_sha"):
            parse_self_review_xml(xml)


class TestSeverityBreakdown:
    def test_counts_all_severities(self):
        findings = [
            SelfReviewFinding(
                finding_id=f"f{i}",
                severity=sev,
                path="x",
                line=None,
                title="t",
                body="b",
                suggested_fix="f",
            )
            for i, sev in enumerate(["critical", "major", "major", "minor", "nitpick"])
        ]
        breakdown = severity_breakdown(findings)
        assert breakdown == {"critical": 1, "major": 2, "minor": 1, "nitpick": 1}

    def test_empty(self):
        assert severity_breakdown([]) == {
            "critical": 0,
            "major": 0,
            "minor": 0,
            "nitpick": 0,
        }


class TestTruncateDiffForReview:
    def test_no_truncation_under_limit(self):
        text = "small"
        out, truncated = truncate_diff_for_review(text, max_chars=100)
        assert out == text
        assert truncated is False

    def test_truncates_at_file_boundary(self):
        text = "\n".join(
            [
                "diff --git a/a b/a",
                "@@ ...",
                "x" * 5000,
                "diff --git a/b b/b",
                "@@ ...",
                "y" * 5000,
            ]
        )
        out, truncated = truncate_diff_for_review(text, max_chars=6000)
        assert truncated is True
        assert "y" * 100 not in out
