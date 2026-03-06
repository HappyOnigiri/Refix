.PHONY: run dry-run help

help:
	@echo "Auto Review Fixer - Makefile targets:"
	@echo "  make run      - Run auto review fixer with repos from repos.txt"
	@echo "  make dry-run  - Show what would be executed without actually running"
	@echo "  make help     - Show this help message"

run:
	cd src && python auto_fixer.py

dry-run:
	cd src && python auto_fixer.py --dry-run

.DEFAULT_GOAL := help
