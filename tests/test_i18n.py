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
        text = i18n.t(
            "state_comment.findings_label",
            total=1,
            critical=0,
            major=1,
            minor=0,
            nitpick=0,
        )
        assert "1" in text


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
        assert "fix_approach" in text
        assert "DO NOT modify" in text or "DO NOT run git commit" in text

    def test_self_review_instructions_ja(self):
        i18n.set_language("ja")
        text = i18n.t("self_review.instructions")
        assert "セルフレビュー" in text
        assert "fix_approach" in text

    def test_fix_instructions_en(self):
        i18n.set_language("en")
        text = i18n.t("fix.instructions")
        assert "AUTHORITATIVE" in text

    def test_fix_instructions_ja(self):
        i18n.set_language("ja")
        text = i18n.t("fix.instructions")
        assert "再評価" in text


class TestUIStringKeys:
    def test_refix_log_summary_en(self):
        i18n.set_language("en")
        assert i18n.t("state_comment.refix_log_summary") == "Refix Log"

    def test_refix_log_summary_ja(self):
        i18n.set_language("ja")
        assert i18n.t("state_comment.refix_log_summary") == "Refix ログ"

    def test_no_findings_en(self):
        i18n.set_language("en")
        assert "No issues" in i18n.t("state_comment.no_findings")

    def test_no_findings_ja(self):
        i18n.set_language("ja")
        assert "指摘事項" in i18n.t("state_comment.no_findings")

    def test_findings_label_format(self):
        i18n.set_language("en")
        text = i18n.t(
            "state_comment.findings_label",
            total=3,
            critical=1,
            major=1,
            minor=0,
            nitpick=1,
        )
        assert "**Findings:**" in text
        assert "3" in text
        assert "critical: 1" in text

    def test_applied_commits_label(self):
        i18n.set_language("en")
        assert "Applied commits" in i18n.t("state_comment.applied_commits_label")

    def test_fix_failed_notice_en(self):
        i18n.set_language("en")
        assert "Fix failed" in i18n.t("state_comment.fix_failed_notice")

    def test_fix_failed_notice_ja(self):
        i18n.set_language("ja")
        assert "修正に失敗" in i18n.t("state_comment.fix_failed_notice")

    def test_old_keys_are_removed(self):
        for key in (
            "result_report.phase_title.self-review",
            "result_report.phase_title.fix",
            "result_report.executed_at",
            "state_comment.self_review_log_summary",
            "state_comment.findings_breakdown",
            "state_comment.review_details_summary",
            "state_comment.result_log_summary",
            "state_comment.truncation_notice",
            "state_comment.suggested_fix_label",
        ):
            with pytest.raises(KeyError):
                i18n.t(key)

    def test_fix_approach_label(self):
        i18n.set_language("en")
        assert i18n.t("state_comment.fix_approach_label") == "**Approach:**"
        i18n.set_language("ja")
        assert i18n.t("state_comment.fix_approach_label") == "**修正方針:**"

    def test_fix_instructions_mention_comprehensive(self):
        i18n.set_language("en")
        text = i18n.t("fix.instructions")
        assert "COMPREHENSIVELY" in text
        assert "callers" in text.lower()
        i18n.set_language("ja")
        text_ja = i18n.t("fix.instructions")
        assert "影響範囲" in text_ja
        assert "callers" in text_ja
