"""Claude へのプロンプト生成・レビュー XML パースを行うモジュール。"""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET

from i18n import t
from type_defs import LoggedCommit, SelfReviewFinding, SelfReviewResult

_ALLOWED_SEVERITIES = {"critical", "major", "minor", "nitpick"}
# critical > major > minor > nitpick の順。filter は rank が threshold 以上のみ残す。
_SEVERITY_RANK: dict[str, int] = {
    "nitpick": 0,
    "minor": 1,
    "major": 2,
    "critical": 3,
}


def filter_findings_by_severity(
    result: SelfReviewResult, min_severity: str
) -> SelfReviewResult:
    """min_severity 未満の finding を除外した SelfReviewResult を返す。

    min_severity が "nitpick"（デフォルト）の場合は no-op（全件通過）。
    """
    normalized = (min_severity or "nitpick").strip().lower()
    if normalized not in _SEVERITY_RANK:
        raise ValueError(
            f"min_severity must be one of {sorted(_SEVERITY_RANK)}; got {min_severity!r}"
        )
    threshold = _SEVERITY_RANK[normalized]
    if threshold == 0:
        return result
    filtered = [
        f for f in result.findings if _SEVERITY_RANK.get(f.severity, 0) >= threshold
    ]
    if len(filtered) == len(result.findings):
        return result
    return dataclasses.replace(result, findings=filtered)


def _xml_escape(text: str) -> str:
    """XML コンテンツ用のテキストエスケープ。プロンプトインジェクション防止。"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_escape_attr(text: str) -> str:
    """XML 属性値用のテキストエスケープ。"""
    return _xml_escape(text).replace('"', "&quot;").replace("'", "&apos;")


def build_self_review_prompt(
    *,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    base_branch: str,
    head_sha: str,
    diff_range: str,
    review_files: list[str],
    output_path: str,
    language: str = "en",
    previously_applied_fixes: list[LoggedCommit] | None = None,
) -> str:
    """セルフレビュー用のプロンプトを生成する。

    diff・変更ファイル一覧は Refix 側で事前計算し <review_scope> 要素に注入する。
    """
    instructions = t("self_review.instructions", diff_range=diff_range)
    description_elem = (
        f"\n  <pr_description>{_xml_escape(pr_body)}</pr_description>"
        if pr_body
        else ""
    )
    files_lines = "\n".join(f"    <file>{_xml_escape(p)}</file>" for p in review_files)
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
        (
            "<review_scope>\n"
            f"  <diff_range>{_xml_escape(diff_range)}</diff_range>\n"
            "  <files>\n"
            f"{files_lines}\n"
            "  </files>\n"
            "</review_scope>"
        ),
    ]
    if previously_applied_fixes:
        commit_lines = "\n".join(
            f'  <commit sha="{_xml_escape_attr(c.sha)}">{_xml_escape(c.message)}</commit>'
            for c in previously_applied_fixes
        )
        parts.append(
            f"<previously_applied_fixes>\n{commit_lines}\n</previously_applied_fixes>"
        )
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
        fix_approach_elem = finding_elem.find("fix_approach")
        if title_elem is None or not (title_elem.text or "").strip():
            raise ValueError(f"finding {finding_id} is missing <title>")
        if body_elem is None or not (body_elem.text or "").strip():
            raise ValueError(f"finding {finding_id} is missing <body>")
        if fix_approach_elem is None or not (fix_approach_elem.text or "").strip():
            raise ValueError(
                f"finding {finding_id} is missing <fix_approach>; "
                "every finding must include a fix approach."
            )

        findings.append(
            SelfReviewFinding(
                finding_id=finding_id,
                severity=severity,
                path=path,
                line=line,
                title=(title_elem.text or "").strip(),
                body=(body_elem.text or "").strip(),
                fix_approach=(fix_approach_elem.text or "").strip(),
            )
        )

    return SelfReviewResult(
        head_sha=head_sha,
        reviewed_at=reviewed_at,
        summary=summary,
        findings=findings,
        raw_xml=xml_text.strip(),
    )


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
