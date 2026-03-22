"""実行結果ブロックのフォーマットと結合を行うモジュール。"""

from i18n import t
from state_manager import current_timestamp


def format_phase_result_block(
    phase_label: str,
    stdout_text: str,
    timestamp: str,
    comment_urls: list[str] | None = None,
) -> str:
    """1フェーズの実行結果ブロックを生成する。"""
    phase_title_key = f"result_report.phase_title.{phase_label}"
    try:
        phase_title = t(phase_title_key)
    except KeyError:
        phase_title = phase_label
    stripped_stdout = stdout_text.strip()
    fence = "```"
    while fence in stripped_stdout:
        fence += "`"
    lines = [
        f"#### {phase_title}",
        "",
        t("result_report.executed_at", timestamp=timestamp),
    ]
    if comment_urls:
        url_links = ", ".join(
            f"[link{i + 1}]({url})" for i, url in enumerate(comment_urls)
        )
        lines.append(t("result_report.target_comments", url_links=url_links))
    lines.extend(
        [
            "",
            fence,
            stripped_stdout,
            fence,
        ]
    )
    return "\n".join(lines)


def merge_result_log_body(
    existing_body: str,
    new_blocks: list[str],
) -> str:
    """新しいブロックを既存の本文の前にマージする。"""
    parts = [block.strip() for block in new_blocks if block.strip()]
    existing = (existing_body or "").strip()
    if existing:
        parts.append(existing)
    return "\n\n".join(parts)


def build_phase_result_entry(
    phase_label: str,
    stdout_text: str,
    timezone_name: str,
    comment_urls: list[str] | None = None,
) -> str:
    """タイムスタンプを生成し format_phase_result_block を呼ぶ。"""
    timestamp = current_timestamp(timezone_name)
    return format_phase_result_block(
        phase_label=phase_label,
        stdout_text=stdout_text,
        timestamp=timestamp,
        comment_urls=comment_urls,
    )
