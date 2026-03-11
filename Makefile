.PHONY: run run-silent dry-run run-summarize-only setup test ci repomix install-hooks help help-en

# Use venv Python when available (for make test/ci without activating)
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
REPOMIX_VERSION ?= 1.12.0
.DEFAULT_GOAL := run

help:
	@cd src && python auto_fixer.py --list-commands

help-en:
	@cd src && python auto_fixer.py --list-commands-en

setup:
	pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.sample .env && echo ".env created from .env.sample"; \
	else \
		echo ".env already exists, skipping."; \
	fi

run:
	cd src && python auto_fixer.py

run-silent:
	cd src && python auto_fixer.py --silent

dry-run:
	cd src && python auto_fixer.py --dry-run

run-summarize-only:
	cd src && python auto_fixer.py --summarize-only

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

ci: test

repomix:
	@mkdir -p tmp/repomix
	npx --yes repomix@$(REPOMIX_VERSION) -o tmp/repomix/repomix-output.xml --quiet

install-hooks:
	@HOOKS_DIR="$$(git config --path --get core.hooksPath 2>/dev/null || true)"; \
	if [ -z "$$HOOKS_DIR" ]; then HOOKS_DIR="$$(git rev-parse --git-path hooks)"; fi; \
	mkdir -p "$$HOOKS_DIR" && \
	install -m 755 scripts/githooks/pre-commit "$$HOOKS_DIR/pre-commit" && \
	echo "Git hooks installed."
