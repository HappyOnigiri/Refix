"""Unit tests for result_report module."""

from __future__ import annotations

import pytest

import i18n
import result_report


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language("en")


class TestFormatPhaseResultBlock:
    def test_self_review_format_en(self):
        block = result_report.format_phase_result_block(
            phase_label="self-review",
            stdout_text="found 2 findings",
            timestamp="2026-05-12 14:30:00 JST",
        )
        assert "#### Self-review" in block
        assert "**Executed at:** 2026-05-12 14:30:00 JST" in block
        assert "found 2 findings" in block

    def test_fix_format_en(self):
        block = result_report.format_phase_result_block(
            phase_label="fix",
            stdout_text="applied",
            timestamp="2026-05-12 14:30:00 JST",
        )
        assert "#### Fix" in block

    def test_self_review_format_ja(self):
        i18n.set_language("ja")
        block = result_report.format_phase_result_block(
            phase_label="self-review",
            stdout_text="ok",
            timestamp="2026-05-12 14:30:00 JST",
        )
        assert "#### セルフレビュー" in block
        assert "**実行日時:** 2026-05-12 14:30:00 JST" in block

    def test_fix_format_ja(self):
        i18n.set_language("ja")
        block = result_report.format_phase_result_block(
            phase_label="fix",
            stdout_text="ok",
            timestamp="2026-05-12 14:30:00 JST",
        )
        assert "#### 修正" in block

    def test_merge_conflict_resolution_label(self):
        block = result_report.format_phase_result_block(
            phase_label="merge-conflict-resolution",
            stdout_text="Resolved",
            timestamp="2026-05-12 14:30:00 JST",
        )
        assert "#### Conflict Resolution" in block


class TestMergeResultLogBody:
    def test_prepends_new_blocks_before_existing(self):
        existing = "old content"
        new_blocks = ["block A", "block B"]
        result = result_report.merge_result_log_body(existing, new_blocks)
        assert result == "block A\n\nblock B\n\nold content"

    def test_with_no_existing(self):
        result = result_report.merge_result_log_body("", ["block A"])
        assert result == "block A"

    def test_with_no_new_blocks(self):
        result = result_report.merge_result_log_body("existing", [])
        assert result == "existing"

    def test_empty_blocks_are_skipped(self):
        result = result_report.merge_result_log_body(
            "existing", ["", "  ", "real block"]
        )
        assert result == "real block\n\nexisting"

    def test_both_empty(self):
        result = result_report.merge_result_log_body("", [])
        assert result == ""


class TestBuildPhaseResultEntry:
    def test_generates_block_with_timestamp(self, mocker):
        mocker.patch(
            "result_report.current_timestamp",
            return_value="2026-05-12 14:30:00 JST",
        )
        entry = result_report.build_phase_result_entry(
            phase_label="self-review",
            stdout_text="output text",
            timezone_name="JST",
        )
        assert "2026-05-12 14:30:00 JST" in entry
        assert "#### Self-review" in entry
        assert "output text" in entry
