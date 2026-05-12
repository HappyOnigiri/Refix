"""LLM prompt string translations for Refix.

Keys:
    self_review.instructions
    fix.instructions
    conflict_resolution.instructions
"""

from i18n import register

_PROMPTS: dict[str, dict[str, str]] = {
    "self_review.instructions": {
        "en": """\
You are performing a self-review of a pull request. Your sole task in THIS session is to produce a structured review XML file.

Strict rules:
1. DO NOT modify any source files. DO NOT run git commit. DO NOT push.
2. Read the <pr_meta>, <changed_files>, and <diff> blocks below. Use the Read tool to inspect any file in the working tree as needed.
3. Identify only issues that are clearly worth fixing. If you are unsure whether a finding is valid, OMIT it. Once you write a finding, it will be treated as authoritative and the fix session will apply it without re-judging.
4. Each finding MUST contain three elements:
   - <title>: short headline
   - <body>: WHY this is a problem (current behavior, risk, impact)
   - <suggested_fix>: HOW to fix it. Be concrete: name files/lines, show code snippets or pseudocode, name new constants/functions. The fix session will follow this verbatim and is forbidden from re-evaluating it.
5. Allowed severities: critical, major, minor, nitpick. No other values.
6. Write the result to the file path provided in <output_path>. Use the Write tool. Format MUST be a single XML document with this exact shape (no markdown fences, no extra text):

<self_review version="1" head_sha="{head_sha}" reviewed_at="ISO8601">
  <summary>1-3 sentence overview describing finding count and themes.</summary>
  <findings>
    <finding id="f1" severity="major" path="src/foo.py" line="42">
      <title>Short headline</title>
      <body>Why this is a problem.</body>
      <suggested_fix>Concrete change: which lines, what code to add/remove.</suggested_fix>
    </finding>
  </findings>
</self_review>

7. If the diff is clean, still write a valid <self_review> file with an empty <findings/> element. Do not skip writing the file.
8. Output nothing to stdout other than incidental tool output. The review file is the deliverable.
""",
        "ja": """\
あなたは pull request のセルフレビューを実施します。このセッションでの唯一のタスクは、構造化されたレビュー XML ファイルを生成することです。

厳守事項:
1. ソースファイルを一切変更しないこと。git commit / push もしないこと。
2. 以下の <pr_meta> / <changed_files> / <diff> ブロックを読み、必要に応じて Read ツールで作業ツリー内のファイルを確認すること。
3. 「明らかに修正する価値のある問題」のみ指摘すること。妥当性に疑問がある場合は出さないこと。一度書いた finding は権威ある指示として扱われ、後続の修正セッションは再判断せずに適用します。
4. 各 finding には以下 3 要素を必ず含めること:
   - <title>: 短い見出し
   - <body>: なぜ問題なのか（現在の挙動・リスク・影響）
   - <suggested_fix>: どう修正するか。具体的に: ファイル名・行番号、コード片や擬似コード、新規定数・関数名などを明示。後続の修正セッションはこの内容をそのまま適用し、再評価することは禁止されている。
5. 使用可能な severity は critical / major / minor / nitpick のみ。
6. 結果は <output_path> で指定されたファイルパスに Write ツールで書き出すこと。形式は以下の単一 XML ドキュメントに厳密に従うこと（マークダウンフェンスや余計なテキストは禁止）:

<self_review version="1" head_sha="{head_sha}" reviewed_at="ISO8601">
  <summary>件数・傾向を 1〜3 文で記述</summary>
  <findings>
    <finding id="f1" severity="major" path="src/foo.py" line="42">
      <title>短い見出し</title>
      <body>なぜ問題なのか</body>
      <suggested_fix>具体的な修正方法（どの行をどう変えるか）</suggested_fix>
    </finding>
  </findings>
</self_review>

7. 指摘がない場合も <findings/> を空にした有効な <self_review> ファイルを必ず書き出すこと。
8. stdout には付随的なツール出力以外を出さないこと。成果物は XML ファイル。
""",
    },
    "fix.instructions": {
        "en": """\
You are executing the fix phase. A previous self-review session produced an XML file at <self_review_path> listing the findings to apply.

Strict rules:
1. Read the XML file with the Read tool. The findings listed there are AUTHORITATIVE. Do NOT re-evaluate whether each finding is valid, do NOT skip findings you disagree with, do NOT add new findings beyond what the XML lists. The review session already made the judgment call.
2. For each finding, implement the change described in <suggested_fix> with the minimum diff. Use the file path and line number hints as starting points; verify the surrounding code with Read first.
3. Treat nitpick severity the same as any other: apply it.
4. Aim for one commit per finding. Use a short commit subject derived from <title>.
5. If a finding's <suggested_fix> is genuinely ambiguous (e.g. references a file that does not exist or an undefined symbol), stop work, do not commit, and print a clear stdout message: "FIX-ABORT: <finding_id> reason: ...". This is the only allowed escape hatch.
6. Do NOT git push; the runner will push after this session completes.
7. The full review XML is inlined below as a fallback in case Read tool access fails.
""",
        "ja": """\
あなたは修正フェーズを実行します。先行する self-review セッションが <self_review_path> に XML ファイルを生成しており、ここに適用すべき finding が列挙されています。

厳守事項:
1. Read ツールで XML ファイルを読み込むこと。そこに列挙された finding は確定情報です。各 finding の妥当性を再評価しないこと、同意できない finding をスキップしないこと、列挙されていない新規 finding を追加しないこと。妥当性の判断は review セッションで完了済みです。
2. 各 finding について、<suggested_fix> に記述された変更を最小差分で実装すること。ファイルパスと行番号はあくまでヒントなので、Read で周辺コードを確認してから編集すること。
3. severity が nitpick であっても他と同様に適用すること。
4. 1 finding につき 1 コミットを目安にすること。コミットサブジェクトは <title> から短く作成すること。
5. <suggested_fix> が本当に曖昧（例: 存在しないファイル参照、未定義シンボル参照）の場合のみ作業を中止し、commit はせず stdout に明示的に報告すること: "FIX-ABORT: <finding_id> reason: ..."。これが唯一許可される中断条件。
6. git push はしないこと。runner がこのセッション完了後に push する。
7. Read ツールが失敗した場合のフォールバックとして、レビュー XML 全文を以下にインライン展開している。
""",
    },
    "conflict_resolution.instructions": {
        "en": """\
The following is a conflict resolution task after running git merge origin/{base_branch}.
- Objective: Correctly resolve conflicts that arose when incorporating the base branch
- Requirements:
  1. Completely remove `<<<<<<<`, `=======`, and `>>>>>>>` conflict markers
  2. Resolve with minimal changes that do not break existing behavior
  3. Only git commit if changes were made
  4. Do not commit if no changes are needed
- Refer to the <pr_meta> block for the target PR information""",
        "ja": """\
以下は git merge origin/{base_branch} 実行後に発生したコンフリクト解消タスクです。
- 目的: ベースブランチ取り込み時のコンフリクトを正しく解消する
- 必須条件:
  1. `<<<<<<<`, `=======`, `>>>>>>>` の競合マーカーを完全に除去する
  2. 既存仕様を壊さない最小変更で解消する
  3. 変更した場合のみ git commit する
  4. 変更不要なら commit はしない
- 対象PRの情報は <pr_meta> ブロックを参照すること""",
    },
}

register(_PROMPTS)
