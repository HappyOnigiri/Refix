.PHONY: run run-silent dry-run reset setup test ci lint repomix repomix-full repomix-task repomix-core prep-repomix help help-en

# venv の Python が利用可能な場合はそれを使用する（activate なしで make test/ci を実行するため）
PYTHON := $(if $(wildcard .venv/bin/python),$(abspath .venv/bin/python),$(shell command -v python3 || command -v python))
REPOMIX_VERSION ?= 1.12.0
.DEFAULT_GOAL := run

help:
	@echo "Refix - Makefile targets:"
	@echo ""
	@echo "  make run"
	@echo "    PR の差分を Claude でセルフレビューし、修正セッションで適用・push して PR の状態管理コメントに記録。"
	@echo "    デバッグレベルのログ（プロンプト全文）を表示"
	@echo ""
	@echo "  make run-silent"
	@echo "    本番実行と同じだが、ログを最小限に抑える"
	@echo ""
	@echo "  make dry-run"
	@echo "    Claude を呼ばず、実行コマンドを表示"
	@echo ""
	@echo "  make setup"
	@echo "    依存パッケージをインストールし、.env および .refix-batch.yaml テンプレートを作成"

help-en:
	@echo "Refix - Makefile targets:"
	@echo ""
	@echo "  make run"
	@echo "    Run Claude self-review on PR diffs and apply findings in a fix session, then push and record results in a PR state comment."
	@echo "    Shows debug-level logs (full prompts)."
	@echo ""
	@echo "  make run-silent"
	@echo "    Same as run, but minimize log output."
	@echo ""
	@echo "  make dry-run"
	@echo "    Show commands without calling Claude."
	@echo ""
	@echo "  make setup"
	@echo "    Install dependencies and create .env and .refix-batch.yaml templates."

setup:
	$(PYTHON) -m pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.sample .env && echo ".env created from .env.sample"; \
	else \
		echo ".env already exists, skipping."; \
	fi
	@if [ ! -f .refix-batch.yaml ]; then \
		cp .refix-batch.sample.yaml .refix-batch.yaml && echo ".refix-batch.yaml created from sample"; \
	else \
		echo ".refix-batch.yaml already exists, skipping."; \
	fi

run:
	cd src && python auto_fixer.py

run-silent:
	cd src && python auto_fixer.py --silent

dry-run:
	cd src && python auto_fixer.py --dry-run

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q --ignore=works

ci:
	$(PYTHON) scripts/ci.py

lint:
	$(PYTHON) -m ruff format src tests scripts
	$(PYTHON) -m ruff check src tests scripts --fix
	$(PYTHON) scripts/fix_newlines.py

# --- Repomix ---
# コードベースを AI フレンドリーな単一ファイルにまとめます。
# 用途に合わせて 3 つのバリアントを提供します：
#   - full: 全ファイル（.gitignore 以外すべて）
#   - task: AI エージェントへの機能改修相談に最適化（src/ + tests/ + .github/workflows/ + 基本定義ファイル）
#   - core: ロジック + Actions 構成のみ（src/ + .github/workflows/）
repomix: repomix-full repomix-task repomix-core
	@echo "Repomix files generated in tmp/repomix/"

repomix-full: prep-repomix
	npx --yes repomix@$(REPOMIX_VERSION) --config .repomix/full.config.json --quiet

repomix-task: prep-repomix
	npx --yes repomix@$(REPOMIX_VERSION) --config .repomix/task.config.json --quiet

repomix-core: prep-repomix
	npx --yes repomix@$(REPOMIX_VERSION) --config .repomix/core.config.json --quiet

prep-repomix:
	@mkdir -p tmp/repomix
