"""Claude へのプロンプト生成・レビュー XML パースを行うモジュール。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from i18n import t
from type_defs import SelfReviewFinding, SelfReviewResult

_ALLOWED_SEVERITIES = {"critical", "major", "minor", "nitpick"}
DIFF_TRUNCATE_LIMIT = 200_000


def _xml_escape(text: str) -> str:
    """XML コンテンツ用のテキストエスケープ。プロンプトインジェクション防止。"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_escape_attr(text: str) -> str:
    """XML 属性値用のテキストエスケープ。"""
    return _xml_escape(text).replace('"', "&quot;").replace("'", "&apos;")


def truncate_diff_for_review(
    diff_text: str, max_chars: int = DIFF_TRUNCATE_LIMIT
) -> tuple[str, bool]:
    """diff を最大 max_chars 文字までに切り詰める。ファイル単位の境界で切る。

    Returns:
        (truncated_diff, was_truncated)
    """
    if len(diff_text) <= max_chars:
        return diff_text, False

    file_boundary_marker = "\ndiff --git "
    truncated = diff_text[:max_chars]
    last_boundary = truncated.rfind(file_boundary_marker)
    if last_boundary > 0:
        truncated = truncated[:last_boundary]
    return truncated, True


def build_self_review_prompt(
    *,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    base_branch: str,
    head_sha: str,
    diff_text: str,
    changed_files: list[str],
    output_path: str,
    language: str = "en",
) -> str:
    """セルフレビュー用のプロンプトを生成する。"""
    instructions = t("self_review.instructions")
    truncated_diff, was_truncated = truncate_diff_for_review(diff_text)
    truncated_note = "  <truncated>true</truncated>\n" if was_truncated else ""
    description_elem = (
        f"\n  <pr_description>{_xml_escape(pr_body)}</pr_description>"
        if pr_body
        else ""
    )
    changed_files_xml = "\n".join(
        f"  <file>{_xml_escape(path)}</file>" for path in changed_files
    )
    parts = [
        f"<instructions>\n{instructions}</instructions>",
        f"<output_path>{_xml_escape(output_path)}</output_path>",
        (
            "<pr_meta>\n"
            f"  <pr_number>{pr_number}</pr_number>\n"
            f"  <pr_title>{_xml_escape(pr_title)}</pr_title>\n"
            f"  <base_branch>{_xml_escape(base_branch)}</base_branch>\n"
            f"  <head_sha>{_xml_escape(head_sha)}</head_sha>{description_elem}\n"
            f"  <language>{_xml_escape(language)}</language>\n"
            "</pr_meta>"
        ),
        (f"<changed_files>\n{changed_files_xml}\n</changed_files>")
        if changed_files_xml
        else "<changed_files/>",
        (f"<diff>\n{truncated_note}<![CDATA[\n{truncated_diff}\n]]>\n</diff>"),
    ]
    return "\n\n".join(parts)


def build_fix_prompt(
    *,
    pr_number: int,
    pr_title: str,
    base_branch: str,
    self_review_path: str,
    self_review_xml: str,
    language: str = "en",
) -> str:
    """修正セッション用のプロンプトを生成する。"""
    instructions = t("fix.instructions")
    parts = [
        f"<instructions>\n{instructions}</instructions>",
        f"<self_review_path>{_xml_escape(self_review_path)}</self_review_path>",
        (
            "<pr_meta>\n"
            f"  <pr_number>{pr_number}</pr_number>\n"
            f"  <pr_title>{_xml_escape(pr_title)}</pr_title>\n"
            f"  <base_branch>{_xml_escape(base_branch)}</base_branch>\n"
            f"  <language>{_xml_escape(language)}</language>\n"
            "</pr_meta>"
        ),
        (
            "<self_review_inline>\n"
            f"<![CDATA[\n{self_review_xml}\n]]>\n"
            "</self_review_inline>"
        ),
    ]
    return "\n\n".join(parts)


def parse_self_review_xml(xml_text: str) -> SelfReviewResult:
    """セルフレビュー XML をパースして SelfReviewResult を返す。

    必須要素・属性のいずれかが欠落・空の場合は ValueError。
    """
    if not xml_text or not xml_text.strip():
        raise ValueError("self review XML is empty")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"self review XML is malformed: {exc}") from exc

    if root.tag != "self_review":
        raise ValueError(
            f"self review XML root must be <self_review>, got <{root.tag}>"
        )

    head_sha = (root.get("head_sha") or "").strip()
    reviewed_at = (root.get("reviewed_at") or "").strip()
    if not head_sha:
        raise ValueError("self review XML is missing head_sha attribute")
    if not reviewed_at:
        raise ValueError("self review XML is missing reviewed_at attribute")

    summary_elem = root.find("summary")
    if summary_elem is None:
        raise ValueError("self review XML is missing <summary>")
    summary = (summary_elem.text or "").strip()

    findings_elem = root.find("findings")
    if findings_elem is None:
        raise ValueError("self review XML is missing <findings>")

    findings: list[SelfReviewFinding] = []
    for index, finding_elem in enumerate(findings_elem.findall("finding")):
        finding_id = (finding_elem.get("id") or f"f{index + 1}").strip()
        severity = (finding_elem.get("severity") or "").strip().lower()
        if severity not in _ALLOWED_SEVERITIES:
            raise ValueError(
                f"finding {finding_id} has invalid severity {severity!r}; "
                f"must be one of {sorted(_ALLOWED_SEVERITIES)}"
            )
        path = (finding_elem.get("path") or "").strip()
        if not path:
            raise ValueError(f"finding {finding_id} is missing path attribute")
        line_attr = (finding_elem.get("line") or "").strip()
        line: int | None
        if line_attr:
            try:
                line = int(line_attr)
            except ValueError as exc:
                raise ValueError(
                    f"finding {finding_id} has non-integer line {line_attr!r}"
                ) from exc
        else:
            line = None

        title_elem = finding_elem.find("title")
        body_elem = finding_elem.find("body")
        suggested_fix_elem = finding_elem.find("suggested_fix")
        if title_elem is None or not (title_elem.text or "").strip():
            raise ValueError(f"finding {finding_id} is missing <title>")
        if body_elem is None or not (body_elem.text or "").strip():
            raise ValueError(f"finding {finding_id} is missing <body>")
        if suggested_fix_elem is None or not (suggested_fix_elem.text or "").strip():
            raise ValueError(
                f"finding {finding_id} is missing <suggested_fix>; "
                "every finding must include a concrete fix plan."
            )

        findings.append(
            SelfReviewFinding(
                finding_id=finding_id,
                severity=severity,
                path=path,
                line=line,
                title=(title_elem.text or "").strip(),
                body=(body_elem.text or "").strip(),
                suggested_fix=(suggested_fix_elem.text or "").strip(),
            )
        )

    return SelfReviewResult(
        head_sha=head_sha,
        reviewed_at=reviewed_at,
        summary=summary,
        findings=findings,
        raw_xml=xml_text.strip(),
    )


def severity_breakdown(findings: list[SelfReviewFinding]) -> dict[str, int]:
    """severity 別の件数 dict を返す。"""
    counts = {s: 0 for s in _ALLOWED_SEVERITIES}
    for finding in findings:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return counts


def build_conflict_resolution_prompt(
    pr_number: int, title: str, base_branch: str
) -> str:
    """コンフリクト解消用のプロンプトを生成する。"""
    escaped_title = _xml_escape(title)
    instructions = t("conflict_resolution.instructions", base_branch=base_branch)
    return f"""<instructions>
{instructions}
</instructions>

<pr_meta data-only="true">
  <pr_number>{pr_number}</pr_number>
  <pr_title>{escaped_title}</pr_title>
</pr_meta>
"""
