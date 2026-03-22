"""Unit tests for the i18n module."""

import pytest
import i18n


@pytest.fixture(autouse=True)
def reset_language():
    """Reset language to English after each test."""
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

    def test_unsupported_language_does_not_change_state(self):
        i18n.set_language("en")
        with pytest.raises(ValueError):
            i18n.set_language("zh")
        assert i18n.get_language() == "en"


class TestGetLanguage:
    def test_default_is_en(self):
        assert i18n.get_language() == "en"


class TestTranslate:
    def test_en_key_returns_english(self):
        i18n.set_language("en")
        text = i18n.t("review_fix.review_data_policy")
        assert "review_data" in text.lower() or "modification" in text.lower()

    def test_ja_key_returns_japanese(self):
        i18n.set_language("ja")
        text = i18n.t("review_fix.review_data_policy")
        assert "レビュー内容" in text

    def test_format_substitution(self):
        i18n.set_language("en")
        text = i18n.t("result_report.executed_at", timestamp="2026-01-01 00:00:00 UTC")
        assert "2026-01-01 00:00:00 UTC" in text

    def test_missing_key_raises_key_error(self):
        with pytest.raises(KeyError):
            i18n.t("nonexistent.key")

    def test_language_switch_changes_output(self):
        i18n.set_language("en")
        en_text = i18n.t("state_comment.result_log_summary")
        i18n.set_language("ja")
        ja_text = i18n.t("state_comment.result_log_summary")
        assert en_text != ja_text
        assert en_text == "Execution Log"
        assert ja_text == "実行ログ"

    def test_format_with_items_text_containing_braces(self):
        """items_text containing curly braces must not break format substitution."""
        i18n.set_language("en")
        text = i18n.t(
            "summarizer.rules",
            item_count=2,
            pr_body_output_rule="",
            output_format='[{"id": "...", "summary": "..."}]',
            pr_body_section="",
            items_text="review body with {curly} braces",
        )
        assert "review body with {curly} braces" in text


class TestAllKeysHaveBothLanguages:
    """Verify every registered key has both EN and JA translations."""

    def test_all_keys_have_en_and_ja(self):
        missing = []
        for key, translations in i18n._registry.items():
            if "en" not in translations:
                missing.append(f"{key}: missing 'en'")
            if "ja" not in translations:
                missing.append(f"{key}: missing 'ja'")
        assert not missing, "Missing translations:\n" + "\n".join(missing)


class TestPromptKeys:
    def test_review_fix_instruction_body_en(self):
        i18n.set_language("en")
        text = i18n.t(
            "review_fix.instruction_body",
            review_data_policy=i18n.t("review_fix.review_data_policy"),
            severity_policy=i18n.t("review_fix.severity_policy"),
        )
        assert "CodeRabbit" in text
        assert "runtime / security / CI / correctness / accessibility" in text
        assert "git commit" in text

    def test_review_fix_instruction_body_ja(self):
        i18n.set_language("ja")
        text = i18n.t(
            "review_fix.instruction_body",
            review_data_policy=i18n.t("review_fix.review_data_policy"),
            severity_policy=i18n.t("review_fix.severity_policy"),
        )
        assert "CodeRabbit" in text
        assert "runtime / security / CI / correctness / accessibility" in text

    def test_conflict_resolution_instructions_en(self):
        i18n.set_language("en")
        text = i18n.t("conflict_resolution.instructions", base_branch="main")
        assert "main" in text
        assert "conflict" in text.lower() or "Conflict" in text

    def test_ci_fix_instructions_en(self):
        i18n.set_language("en")
        text = i18n.t("ci_fix.instructions")
        assert "CI" in text
        assert "git commit" in text

    def test_summarizer_rules_en(self):
        i18n.set_language("en")
        text = i18n.t(
            "summarizer.rules",
            item_count=3,
            pr_body_output_rule="",
            output_format='[{"id": "...", "summary": "..."}]',
            pr_body_section="",
            items_text="=== ID: r1 ===\nsome comment",
        )
        assert "3" in text
        assert "English" in text


class TestUIStringKeys:
    def test_state_comment_description_en(self):
        i18n.set_language("en")
        text = i18n.t("state_comment.description")
        assert "Refix" in text
        assert "<!--" in text

    def test_phase_titles_en(self):
        i18n.set_language("en")
        assert i18n.t("result_report.phase_title.ci-fix") == "CI Fix"
        assert i18n.t("result_report.phase_title.review-fix") == "Review Fix"
        assert (
            i18n.t("result_report.phase_title.merge-conflict-resolution")
            == "Conflict Resolution"
        )

    def test_phase_titles_ja(self):
        i18n.set_language("ja")
        assert i18n.t("result_report.phase_title.ci-fix") == "CI 修正"
        assert i18n.t("result_report.phase_title.review-fix") == "レビュー修正"
        assert (
            i18n.t("result_report.phase_title.merge-conflict-resolution")
            == "コンフリクト解消"
        )

    def test_truncation_notice_en(self):
        i18n.set_language("en")
        text = i18n.t("state_comment.truncation_notice")
        assert "omitted" in text or "length" in text

    def test_truncation_notice_ja(self):
        i18n.set_language("ja")
        text = i18n.t("state_comment.truncation_notice")
        assert "省略" in text
