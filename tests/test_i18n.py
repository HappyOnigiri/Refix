"""Unit tests for the i18n module."""

import pytest

import i18n


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language("en")


class TestSetLanguage:
    def test_set_to_en(self):
        i18n.set_language("en")
        assert i18n.get_language() == "en"

    def test_set_to_ja(self):
        i18n.set_language("ja")
        assert i18n.get_language() == "ja"

    def test_unsupported_language_raises(self):
        with pytest.raises(ValueError, match="Unsupported language"):
            i18n.set_language("fr")


class TestTranslate:
    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            i18n.t("nonexistent.key")

    def test_format_substitution(self):
        i18n.set_language("en")
        text = i18n.t("result_report.executed_at", timestamp="2026-05-12")
        assert "2026-05-12" in text


class TestAllKeysHaveBothLanguages:
    def test_all_keys_have_en_and_ja(self):
        missing = []
        for key, translations in i18n._registry.items():
            if "en" not in translations:
                missing.append(f"{key}: missing 'en'")
            if "ja" not in translations:
                missing.append(f"{key}: missing 'ja'")
        assert not missing, "Missing translations:\n" + "\n".join(missing)


class TestSelfReviewPromptKeys:
    def test_self_review_instructions_en(self):
        i18n.set_language("en")
        text = i18n.t("self_review.instructions")
        assert "self-review" in text.lower()
        assert "suggested_fix" in text
        assert "DO NOT modify" in text or "DO NOT run git commit" in text

    def test_self_review_instructions_ja(self):
        i18n.set_language("ja")
        text = i18n.t("self_review.instructions")
        assert "セルフレビュー" in text
        assert "suggested_fix" in text

    def test_fix_instructions_en(self):
        i18n.set_language("en")
        text = i18n.t("fix.instructions")
        assert "AUTHORITATIVE" in text

    def test_fix_instructions_ja(self):
        i18n.set_language("ja")
        text = i18n.t("fix.instructions")
        assert "再評価" in text


class TestUIStringKeys:
    def test_self_review_log_summary_en(self):
        i18n.set_language("en")
        assert i18n.t("state_comment.self_review_log_summary") == "Self-Review Log"

    def test_self_review_log_summary_ja(self):
        i18n.set_language("ja")
        assert i18n.t("state_comment.self_review_log_summary") == "セルフレビュー履歴"

    def test_no_findings_en(self):
        i18n.set_language("en")
        assert "No issues" in i18n.t("state_comment.no_findings")

    def test_no_findings_ja(self):
        i18n.set_language("ja")
        assert "指摘事項" in i18n.t("state_comment.no_findings")

    def test_findings_breakdown_format(self):
        i18n.set_language("en")
        text = i18n.t(
            "state_comment.findings_breakdown",
            total=3,
            critical=1,
            major=1,
            minor=0,
            nitpick=1,
        )
        assert "Findings: 3" in text
        assert "critical: 1" in text

    def test_phase_titles_en(self):
        i18n.set_language("en")
        assert i18n.t("result_report.phase_title.self-review") == "Self-review"
        assert i18n.t("result_report.phase_title.fix") == "Fix"
        assert (
            i18n.t("result_report.phase_title.merge-conflict-resolution")
            == "Conflict Resolution"
        )

    def test_phase_titles_ja(self):
        i18n.set_language("ja")
        assert i18n.t("result_report.phase_title.self-review") == "セルフレビュー"
        assert i18n.t("result_report.phase_title.fix") == "修正"

    def test_old_phase_titles_are_removed(self):
        with pytest.raises(KeyError):
            i18n.t("result_report.phase_title.ci-fix")
        with pytest.raises(KeyError):
            i18n.t("result_report.phase_title.review-fix")
