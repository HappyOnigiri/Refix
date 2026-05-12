"""GitHub UI string translations for Refix.

Keys:
    state_comment.description
    state_comment.refix_log_summary
    state_comment.no_findings
    state_comment.findings_label
    state_comment.fix_approach_label
    state_comment.applied_commits_label
    state_comment.fix_failed_notice
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
    "state_comment.refix_log_summary": {
        "en": "Refix Log",
        "ja": "Refix ログ",
    },
    "state_comment.no_findings": {
        "en": "**No issues found.**",
        "ja": "**指摘事項はありませんでした。**",
    },
    "state_comment.findings_label": {
        "en": "**Findings:** {total} (critical: {critical}, major: {major}, minor: {minor}, nitpick: {nitpick})",
        "ja": "**指摘件数:** {total} (critical: {critical}, major: {major}, minor: {minor}, nitpick: {nitpick})",
    },
    "state_comment.fix_approach_label": {
        "en": "**Approach:**",
        "ja": "**修正方針:**",
    },
    "state_comment.applied_commits_label": {
        "en": "**Applied commits:**",
        "ja": "**適用コミット:**",
    },
    "state_comment.fix_failed_notice": {
        "en": "⚠️ **Fix failed.** No commits applied. Will retry on next run.",
        "ja": "⚠️ **修正に失敗しました。** コミットは適用されていません。次回再実行されます。",
    },
}

register(_UI_STRINGS)
