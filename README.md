# Refix

[Japanese version](README.ja.md)

A GitHub Action that lets Claude review each pull request and then auto-fix the
issues it finds, all in a single workflow run.

![Refix](.github/assets/refix.jpg)

## Features

- **Claude self-review**: runs an Opus session (configurable) against the PR
  diff and produces a structured XML review with concrete fix suggestions
- **Automatic fix session**: a separate Sonnet session (configurable) applies
  every finding as a commit, following the review verbatim
- **State comment**: each run appends a `Self-Review Log` entry with the head
  SHA, finding counts, and the raw review XML for full traceability
- **Idempotent**: the workflow records the reviewed HEAD and skips re-running
  if nothing has changed
- **Base branch maintenance**: keeps PR branches up to date with the base
  branch and asks Claude to resolve merge/rebase conflicts when they occur
- **Optional auto-merge** once the PR reaches the `refix: done` state
- **PR labels** for visual status (running, done, merged, auto-merge-requested)

## Models

Two Claude sessions run per PR:

| Setting          | Default   | Purpose                                    |
| ---------------- | --------- | ------------------------------------------ |
| `models.review`  | `opus`    | Generates `_self_review.xml` for the diff. |
| `models.fix`     | `sonnet`  | Applies the findings as commits.           |

Override either in `.refix.yaml` or in your batch config.

## Setup

### 1. Add the workflow

Run the following command in your repository root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/init.sh)
```

This creates `.github/workflows/run-refix.yml`, which triggers automatically on
PR events, scheduled runs, and manual dispatch.

### 2. Register secrets

Go to your repository's **Settings > Secrets and variables > Actions** and add:

- **`GH_TOKEN`** - Classic Personal Access Token
  - Create at: GitHub Settings > Developer settings > Personal access tokens >
    Tokens (classic)
  - Required scopes: `repo`, `workflow`
- **`CLAUDE_CODE_OAUTH_TOKEN`** - Claude Code OAuth token
  - Generate with the `claude setup-token` command

## Configuration (optional)

You can customize Refix behavior by placing a `.refix.yaml` file at your
repository root, or by setting `REFIX_CONFIG_YAML` as a GitHub Actions Variable.

See [`.refix.sample.yaml`](.refix.sample.yaml) for all available options.

## Migrating from v1.x

Refix v2.0.0 removes the CodeRabbit integration. The following configuration
keys are no longer accepted and must be removed from your `.refix.yaml`:

- `coderabbit_auto_resume`, `coderabbit_auto_resume_triggers`,
  `coderabbit_auto_resume_max_per_run`, `coderabbit_auto_resume_stale_minutes`
- `coderabbit_require_review`, `coderabbit_block_while_processing`,
  `coderabbit_ignore_nitpick`
- `triggers`, `ci_log_max_lines`
- `models.summarize` (replaced by `models.review`)

Any existing `refix: ci-pending` labels can be removed manually; v2.0.0 no
longer reads them.

## Contributing

Contributions are welcome.

- Open an issue for bugs, ideas, or questions.
- Submit a pull request for fixes, improvements, or documentation updates.
- Use the provided issue and pull request templates to keep reports actionable.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
