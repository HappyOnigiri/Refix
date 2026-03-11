"""Pytest configuration and fixtures for auto-review-fixer tests."""

import sys
from pathlib import Path

# Add src to path so tests can import auto_fixer, state_manager, summarizer, etc.
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))
