"""Unit tests for prompt_builder."""

from __future__ import annotations

import pytest

import i18n
from prompt_builder import (
    build_fix_prompt,
    build_self_review_prompt,
    filter_findings_by_severity,
    parse_self_review_xml,
)
from type_defs import LoggedCommit, SelfReviewFinding, SelfReviewResult


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
      <fix_approach>Make foo() defensive against None inputs and align callers accordingly.</fix_approach>
    </finding>
    <finding id="f2" severity="minor" path="src/bar.py">
      <title>Magic number</title>
      <body>86400 should be a constant.</body>
      <fix_approach>Introduce a named constant SECONDS_PER_DAY and replace all literal usages.</fix_approach>
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
            diff_range="origin/main...HEAD",
            review_files=["src/auth.py"],
            output_path="/tmp/_self_review.xml",
            language="en",
        )
        assert "<instructions>" in prompt
        assert "42" in prompt
        assert "Refactor auth" in prompt
        assert "abc1234" in prompt
        assert "/tmp/_self_review.xml" in prompt
        assert "fix_approach" in prompt

    def test_does_not_inline_diff_or_changed_files(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_range="origin/main...HEAD",
            review_files=["src/x.py"],
            output_path="/tmp/r.xml",
        )
        assert "<diff>" not in prompt
        assert "<changed_files>" not in prompt

    def test_instructions_reference_diff_range(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="develop",
            head_sha="abc",
            diff_range="origin/develop...HEAD",
            review_files=["src/x.py"],
            output_path="/tmp/r.xml",
            language="en",
        )
        # <review_scope> 要素に diff_range が展開されていること
        assert "origin/develop...HEAD" in prompt
        # XML テンプレート内の {head_sha} はリテラルとして残る（format で消費されない）
        assert 'head_sha="{head_sha}"' in prompt

    def test_review_scope_element_present(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_range="origin/main...HEAD",
            review_files=["src/a.py", "src/b.py"],
            output_path="/tmp/r.xml",
        )
        assert "<review_scope>" in prompt
        assert "<diff_range>origin/main...HEAD</diff_range>" in prompt
        assert "<file>src/a.py</file>" in prompt
        assert "<file>src/b.py</file>" in prompt

    def test_incremental_diff_range(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_range="abc1234..HEAD",
            review_files=["src/x.py"],
            output_path="/tmp/r.xml",
        )
        assert "<diff_range>abc1234..HEAD</diff_range>" in prompt

    def test_xml_escape_in_review_files(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_range="origin/main...HEAD",
            review_files=["src/<weird>&file.py"],
            output_path="/tmp/r.xml",
        )
        assert "<file>src/&lt;weird&gt;&amp;file.py</file>" in prompt


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
        # Comprehensive scope instructions are present
        assert "Grep" in prompt or "grep" in prompt

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

    def test_missing_fix_approach_raises(self):
        xml = (
            '<self_review version="1" head_sha="abc" reviewed_at="2026-05-12">'
            "<summary>s</summary><findings>"
            '<finding id="f1" severity="major" path="src/x.py">'
            "<title>t</title><body>b</body></finding>"
            "</findings></self_review>"
        )
        with pytest.raises(ValueError, match="fix_approach"):
            parse_self_review_xml(xml)

    def test_invalid_severity_raises(self):
        xml = (
            '<self_review version="1" head_sha="abc" reviewed_at="2026-05-12">'
            "<summary>s</summary><findings>"
            '<finding id="f1" severity="blocker" path="src/x.py">'
            "<title>t</title><body>b</body><fix_approach>f</fix_approach></finding>"
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


class TestPreviouslyAppliedFixes:
    def test_block_omitted_when_empty(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_range="origin/main...HEAD",
            review_files=["src/x.py"],
            output_path="/tmp/r.xml",
            previously_applied_fixes=[],
        )
        # ブロックそのもの（行頭の独立タグ）が無いことを確認。
        # 指示文中の文字列としては言及されているのでブロック区切りで検査する。
        assert "\n<previously_applied_fixes>" not in prompt

    def test_block_renders_commits(self):
        prompt = build_self_review_prompt(
            pr_number=1,
            pr_title="t",
            pr_body="",
            base_branch="main",
            head_sha="abc",
            diff_range="origin/main...HEAD",
            review_files=["src/x.py"],
            output_path="/tmp/r.xml",
            previously_applied_fixes=[
                LoggedCommit(sha="deadbeef", message="fix: rename foo"),
                LoggedCommit(sha="cafebabe", message="fix: add null guard"),
            ],
        )
        assert "\n<previously_applied_fixes>" in prompt
        assert 'sha="deadbeef"' in prompt
        assert "fix: rename foo" in prompt
        assert "fix: add null guard" in prompt


class TestFilterFindingsBySeverity:
    def _make_result(self, severities: list[str]) -> SelfReviewResult:
        findings = [
            SelfReviewFinding(
                finding_id=f"f{i}",
                severity=sev,
                path="src/x.py",
                line=None,
                title="t",
                body="b",
                fix_approach="a",
            )
            for i, sev in enumerate(severities)
        ]
        return SelfReviewResult(
            head_sha="abc",
            reviewed_at="2026-05-12",
            summary="",
            findings=findings,
            raw_xml="<xml/>",
        )

    def test_nitpick_threshold_is_noop(self):
        result = self._make_result(["critical", "major", "minor", "nitpick"])
        out = filter_findings_by_severity(result, "nitpick")
        assert len(out.findings) == 4
        assert out is result  # 同一オブジェクトを返すこと（no-op 保証）

    def test_minor_threshold_drops_nitpick(self):
        result = self._make_result(["major", "minor", "nitpick"])
        out = filter_findings_by_severity(result, "minor")
        assert [f.severity for f in out.findings] == ["major", "minor"]

    def test_major_threshold_keeps_critical_and_major(self):
        result = self._make_result(["critical", "major", "minor", "nitpick"])
        out = filter_findings_by_severity(result, "major")
        assert [f.severity for f in out.findings] == ["critical", "major"]

    def test_critical_threshold_keeps_only_critical(self):
        result = self._make_result(["critical", "major", "minor"])
        out = filter_findings_by_severity(result, "critical")
        assert [f.severity for f in out.findings] == ["critical"]

    def test_invalid_threshold_raises(self):
        result = self._make_result(["major"])
        with pytest.raises(ValueError, match="min_severity"):
            filter_findings_by_severity(result, "blocker")

    def test_case_insensitive_threshold(self):
        result = self._make_result(["minor", "nitpick"])
        out = filter_findings_by_severity(result, "MINOR")
        assert [f.severity for f in out.findings] == ["minor"]
