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
3. Identify only issues that are CLEARLY and OBJECTIVELY worth fixing. If you are unsure whether a finding is valid, OMIT it. Once you write a finding, the fix session will treat it as authoritative and apply it without re-judging.
3a. DO NOT flag any of the following, even if you would "prefer" them:
   - Naming or style preferences when the existing name is already clear and unambiguous.
   - Reorganization, extraction, or renaming "for readability" with no concrete defect.
   - Alternative-but-equivalent implementations (different idioms, loop vs. comprehension, etc.).
   - Performance micro-optimizations without a demonstrable hot path or measured impact.
   - Missing comments or docstrings, unless their absence creates a real correctness risk.
   - Speculative defensive code for conditions that cannot actually occur.
   - Test additions for trivial wrappers, or coverage improvements without a concrete bug.
   Only flag what is OBJECTIVELY wrong: bug, correctness defect, security issue, API contract violation, clear regression, or behavior that deviates from the PR's stated intent.
3b. If a <previously_applied_fixes> block is present below, those commits represent fixes already applied to this PR in earlier Refix runs. DO NOT re-raise the same concern (or any near-equivalent rephrasing) that has already been addressed by those commits. If the diff still shows residual signs of an old concern, treat it as resolved unless there is a NEW, distinct defect.
4. Each finding MUST contain three elements:
   - <title>: short headline
   - <body>: WHY this is a problem (current behavior, risk, impact)
   - <fix_approach>: WHAT direction to take to fix it. Describe the GOAL and APPROACH, not a literal patch. Examples: "Rename parameter `numbers_list` to `numbers` for consistency", "Replace the magic number 86400 with a named constant SECONDS_PER_DAY", "Change the off-by-one denominator from `len(numbers) - 1` to `len(numbers)`". You MAY include code snippets as illustration, but do NOT enumerate every caller, test, or comment that needs updating: the fix session is responsible for discovering and updating the full impact surface (callers, tests, docstrings, comments, docs) to satisfy your approach.
5. Allowed severities: critical, major, minor, nitpick. No other values.
6. Write the result to the file path provided in <output_path>. Use the Write tool. Format MUST be a single XML document with this exact shape (no markdown fences, no extra text):

<self_review version="1" head_sha="{head_sha}" reviewed_at="ISO8601">
  <summary>1-3 sentence overview describing finding count and themes.</summary>
  <findings>
    <finding id="f1" severity="major" path="src/foo.py" line="42">
      <title>Short headline</title>
      <body>Why this is a problem.</body>
      <fix_approach>Goal and approach for the fix (not a literal patch).</fix_approach>
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
3. 「**客観的に**修正する価値のある問題」のみ指摘すること。妥当性に疑問がある場合は出さないこと。一度書いた finding は権威ある指示として扱われ、後続の修正セッションは再判断せずに適用します。
3a. 以下の類の指摘は、たとえ「自分の好み」と一致していても**出してはならない**:
   - 既存の名前が明確で誤解の余地がない場合の命名・スタイル変更の好み
   - 具体的な欠陥がない「可読性向上」のためのリファクタ・抽出・リネーム
   - 等価な代替実装（書き方の好み、ループ vs comprehension など）
   - 計測可能なホットパスや実測根拠のない性能マイクロ最適化
   - 正当性リスクが実在しない範囲での コメント・docstring 追加要求
   - 実際には到達不能な条件への防御的コード追加
   - 些末なラッパーへのテスト追加・具体的バグの根拠を伴わないカバレッジ改善
   指摘してよいのは**客観的に誤っているもの**のみ: バグ・正当性欠陥・セキュリティ問題・API 契約違反・明らかな regression・PR の宣言された意図からの逸脱。
3b. 下に `<previously_applied_fixes>` ブロックが存在する場合、それらは過去の Refix 実行でこの PR に既に適用済みの修正コミットです。それらが既に対応した懸念（または近似的な言い換え）を**再度指摘してはならない**。diff に古い懸念の残痕が見えても、新規かつ別個の欠陥でない限り解決済みとして扱うこと。
4. 各 finding には以下 3 要素を必ず含めること:
   - <title>: 短い見出し
   - <body>: なぜ問題なのか（現在の挙動・リスク・影響）
   - <fix_approach>: どの方向で修正するか。**ゴールと方針**を書くこと。完全なパッチを書く必要はない。例: 「引数 `numbers_list` を一貫性のため `numbers` にリネームする」「マジックナンバー 86400 を名前付き定数 SECONDS_PER_DAY に置き換える」「off-by-one の分母 `len(numbers) - 1` を `len(numbers)` に修正する」。例示としてコード片を含めても良いが、影響を受ける callers・テスト・コメント等を**全て列挙する必要はない**。後続の修正セッションが影響範囲（callers / tests / docstring / comment / docs）を能動的に発見・更新する責任を持つ。
5. 使用可能な severity は critical / major / minor / nitpick のみ。
6. 結果は <output_path> で指定されたファイルパスに Write ツールで書き出すこと。形式は以下の単一 XML ドキュメントに厳密に従うこと（マークダウンフェンスや余計なテキストは禁止）:

<self_review version="1" head_sha="{head_sha}" reviewed_at="ISO8601">
  <summary>件数・傾向を 1〜3 文で記述</summary>
  <findings>
    <finding id="f1" severity="major" path="src/foo.py" line="42">
      <title>短い見出し</title>
      <body>なぜ問題なのか</body>
      <fix_approach>修正のゴールと方針（完全なパッチではなく方向性）</fix_approach>
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
1. Read the XML file with the Read tool. The findings listed there are AUTHORITATIVE in terms of WHAT to fix and WHICH DIRECTION to take. Do NOT re-evaluate whether each finding is a valid concern. Do NOT skip a finding merely because you disagree with the judgment. Do NOT add new findings beyond what the XML lists.
2. For each finding, implement the <fix_approach> COMPREHENSIVELY. Discover and update the full impact surface needed to satisfy the approach:
   - The primary change at the indicated path/line
   - All callers, references, and usage sites (use Grep / Read tools to locate them across the repo)
   - Related tests that exercise the changed behavior
   - Related docstrings, comments, and inline documentation that would become stale or misleading
   - Related user-facing documentation (README etc.) if directly affected
   The <fix_approach> describes the goal; you are responsible for figuring out every change required to reach that goal coherently.
3. DO NOT perform out-of-scope work: no opportunistic refactors, style cleanups, optimizations, dependency bumps, or unrelated improvements. Stay strictly within what each finding's approach demands.
4. Skip-allowed conditions (only these): you MAY skip a finding if (a) a precondition stated or implied by the finding is no longer true (e.g. the referenced code has already been changed or removed by an earlier finding's fix), or (b) the approach is genuinely infeasible to implement (e.g. references a symbol or file that does not exist and cannot be located, or the approach is internally contradictory). In those cases, do not commit anything for that finding and print: "FIX-SKIP: <finding_id> reason: ...". You must not skip for any other reason.
5. Treat nitpick severity the same as any other: apply it.
6. Commit granularity: one commit per finding when practical. If a single finding requires changes spread across many files (which is normal under comprehensive fixing), keep them in one commit. Use a short commit subject derived from <title>.
7. DO NOT git push; the runner will push after this session completes.
8. The full review XML is inlined below as a fallback in case Read tool access fails.
""",
        "ja": """\
あなたは修正フェーズを実行します。先行する self-review セッションが <self_review_path> に XML ファイルを生成しており、ここに適用すべき finding が列挙されています。

厳守事項:
1. Read ツールで XML ファイルを読み込むこと。そこに列挙された finding は「何を修正するか」「どの方向で修正するか」について確定情報です。各 finding が妥当な指摘かを再評価しないこと、同意できないという理由でスキップしないこと、列挙されていない新規 finding を追加しないこと。
2. 各 finding について、<fix_approach> を**影響範囲を含めて包括的に**実装すること。方針を満たすために必要な変更を全て発見し適用すること:
   - 指定された path/line の主たる変更
   - 全ての callers・参照箇所・使用箇所（Grep / Read ツールでリポジトリ全体から探すこと）
   - 変更により影響を受ける関連テスト
   - stale になる / 誤解を招く関連 docstring・コメント・インラインドキュメント
   - 直接影響を受けるユーザー向けドキュメント（README 等）
   <fix_approach> はゴールを示すもの。そのゴールに整合的に到達するために必要な全変更を発見し実装する責任があなたにある。
3. 範囲外の作業は禁止: ついでのリファクタリング、スタイル整理、最適化、依存更新、無関係な改善は一切行わないこと。各 finding の方針が要求する範囲に厳密に留まること。
4. スキップを許可する条件（以下のみ）: (a) finding が前提とする状態が既に成立していない場合（例: 先行する別 finding の修正で対象コードが既に変更・削除済み）、または (b) 方針が実装的に実行不能な場合（例: 存在せず特定不能なシンボル・ファイルを参照している、方針が内部矛盾している）。これらに該当する場合のみコミットせず stdout に明示すること: "FIX-SKIP: <finding_id> reason: ..."。それ以外の理由でスキップしてはならない。
5. severity が nitpick であっても他と同様に適用すること。
6. コミット粒度: 可能なら 1 finding につき 1 コミット。包括的な修正で 1 finding が多数のファイルに渡る場合（通常起こりうる）は 1 コミットにまとめてよい。コミットサブジェクトは <title> から短く作成すること。
7. git push はしないこと。runner がこのセッション完了後に push する。
8. Read ツールが失敗した場合のフォールバックとして、レビュー XML 全文を以下にインライン展開している。
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
