# Refix

[English version](README.md)

PR の差分を Claude にセルフレビューさせ、別セッションで自動修正まで行う GitHub Action です。

![Refix](.github/assets/refix.jpg)

## 主な機能

- **Claude セルフレビュー**: PR の差分に対して Opus セッション（設定可）を走らせ、修正方針付きの XML レビューを生成
- **自動修正セッション**: 別の Sonnet セッション（設定可）が finding を順に commit として適用
- **状態コメント**: 実行ごとに「Self-Review Log」エントリ（head SHA / 件数 / 生 XML / コミット SHA）を追記し、トレース可能
- **冪等性**: 直近にレビューした HEAD SHA を記録し、変更がなければスキップ
- **ベースブランチ追従**: base への追従と Claude によるコンフリクト解消
- **修正完了後の自動マージ**（オプション）
- **PR ラベル**: running / done / merged / auto-merge-requested

## モデル設定

PR ごとに 2 つの Claude セッションを実行します。

| 設定キー         | デフォルト | 用途                                |
| ---------------- | ---------- | ----------------------------------- |
| `models.review`  | `opus`     | `_self_review.xml` を生成する側     |
| `models.fix`     | `sonnet`   | finding をコミットとして適用する側  |

`.refix.yaml` またはバッチ設定で個別に上書き可能です。

### Severity しきい値

主観的・スタイル的な指摘を Claude が無限に出し続けるループを避けるため、
fix フェーズ実行前に指定 severity 未満の finding を一括除外できます:

```yaml
review_min_severity: "minor"   # または "major" / "critical"
```

許容値（重要度の降順）: `critical`, `major`, `minor`, `nitpick`。
デフォルトは `nitpick`（すべての finding を適用）。

加えて self-review プロンプトには「主観的・スタイル的な指摘を出すな」という
具体的な禁則例が含まれており、さらに同一 PR でこれまでに適用済みの修正コミット
一覧を `<previously_applied_fixes>` として渡しているため、既に対応済みの懸念を
再度指摘することを抑制します。

## セットアップ

### 1. ワークフローの追加

リポジトリのルートで以下を実行します:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/init.ja.sh)
```

`.github/workflows/run-refix.yml` が生成されます。PR イベント・定期実行・手動 dispatch をトリガーに動作します。

### 2. シークレットの登録

リポジトリの **Settings > Secrets and variables > Actions** に以下を追加します:

- **`GH_TOKEN`** — Classic Personal Access Token
  - GitHub Settings > Developer settings > Personal access tokens > Tokens (classic) で作成
  - 必要なスコープ: `repo`、`workflow`
- **`CLAUDE_CODE_OAUTH_TOKEN`** — Claude Code の OAuth トークン
  - `claude setup-token` コマンドで発行

## 設定（任意）

リポジトリルートに `.refix.yaml` を配置するか、GitHub Actions Variable に `REFIX_CONFIG_YAML`
として設定することで、Refix の動作をカスタマイズできます。

利用可能なオプションは [`.refix.sample.yaml`](.refix.sample.yaml) を参照してください。

## v1.x からの移行

Refix v2.0.0 では CodeRabbit 連携を撤廃しました。以下の設定キーはエラーになるため `.refix.yaml` から削除してください。

- `coderabbit_auto_resume`, `coderabbit_auto_resume_triggers`,
  `coderabbit_auto_resume_max_per_run`, `coderabbit_auto_resume_stale_minutes`
- `coderabbit_require_review`, `coderabbit_block_while_processing`,
  `coderabbit_ignore_nitpick`
- `triggers`, `ci_log_max_lines`, `write_result_to_comment`
- `models.summarize` (代わりに `models.review` を指定)

既存 PR に残っている `refix: ci-pending` ラベルは v2.0.0 では参照されないので、必要に応じて手動で削除してください。

## Contributing

コントリビュート歓迎です。

- バグ報告、要望、質問は Issue を作成してください。
- 修正、改善、ドキュメント更新は Pull Request を歓迎します。
- 追加した Issue / PR テンプレートを使うと、内容を整理しやすくなります。

## ライセンス

このプロジェクトは MIT License で提供されます。詳細は [LICENSE](LICENSE) を参照してください。
