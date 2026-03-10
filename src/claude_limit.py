"""Helpers for detecting Claude usage-limit failures."""


class ClaudeUsageLimitError(RuntimeError):
    """Raised when Claude reports account usage limit exhaustion."""


_USAGE_LIMIT_MARKERS = (
    "you've hit your limit",
    "claude usage limit reached",
    "claude code usage limit reached",
    "usage limit reached",
)


def is_claude_usage_limit_error(*texts: str) -> bool:
    """Return True when command output indicates Claude usage limit."""
    combined = "\n".join(text for text in texts if text).lower()
    if not combined.strip():
        return False
    return any(marker in combined for marker in _USAGE_LIMIT_MARKERS)
