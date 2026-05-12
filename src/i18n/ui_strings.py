"""GitHub UI string translations for Refix.

Keys:
    state_comment.description
    state_comment.result_log_summary
    state_comment.self_review_log_summary
    state_comment.no_findings
    state_comment.findings_breakdown
    state_comment.review_details_summary
    state_comment.truncation_notice
    result_report.phase_title.self-review
    result_report.phase_title.fix
    result_report.phase_title.merge-conflict-resolution
    result_report.executed_at
"""

from i18n import register

_UI_STRINGS: dict[str, dict[str, str]] = {
    "state_comment.description": {
        "en": (
            "<!-- This comment is used by Refix to record processing state. "
            "Do not manually edit or delete it. -->"
        ),
        "ja": (
            "<!-- このコメントは Refix が処理状態を記録するためのものです。"
            "手動で編集・削除しないでください。 -->"
        ),
    },
    "state_comment.result_log_summary": {
        "en": "Execution Log",
        "ja": "実行ログ",
    },
    "state_comment.self_review_log_summary": {
        "en": "Self-Review Log",
        "ja": "セルフレビュー履歴",
    },
    "state_comment.no_findings": {
        "en": "No issues found.",
        "ja": "指摘事項はありませんでした。",
    },
    "state_comment.findings_breakdown": {
        "en": "Findings: {total} (critical: {critical}, major: {major}, minor: {minor}, nitpick: {nitpick})",
        "ja": "指摘件数: {total} 件 (critical: {critical}, major: {major}, minor: {minor}, nitpick: {nitpick})",
    },
    "state_comment.review_details_summary": {
        "en": "Review details",
        "ja": "レビュー詳細",
    },
    "state_comment.truncation_notice": {
        "en": "\n\n*Older execution logs have been omitted due to length limits.*",
        "ja": "\n\n*古い実行ログは長さ制限のため省略されています。*",
    },
    "result_report.phase_title.self-review": {
        "en": "Self-review",
        "ja": "セルフレビュー",
    },
    "result_report.phase_title.fix": {
        "en": "Fix",
        "ja": "修正",
    },
    "result_report.phase_title.merge-conflict-resolution": {
        "en": "Conflict Resolution",
        "ja": "コンフリクト解消",
    },
    "result_report.executed_at": {
        "en": "**Executed at:** {timestamp}",
        "ja": "**実行日時:** {timestamp}",
    },
}

register(_UI_STRINGS)
