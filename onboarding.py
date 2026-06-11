# onboarding.py
"""First-run onboarding: detect a fresh deploy, track onboarding state, and hold
the kickoff prompt + completion-marker handling.

Pure and testable: this module imports no Telegram or agent code. bot.py wires
these helpers into the chat. Paths are read from `config` inside each function so
tests can monkeypatch config.BASE_DIR / MEMORY_DIR / EXPERIENCES_DIR.
"""
import json

import config

# A profile/goals file with fewer than this many stripped chars is treated as a
# placeholder stub, not real content.
_FRESH_CONTENT_THRESHOLD = 80


def _meaningful(path) -> bool:
    """True if a memory markdown file exists and has real (non-stub) content."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return False
    return len(text.strip()) >= _FRESH_CONTENT_THRESHOLD


def is_fresh() -> bool:
    """True when memory is essentially empty — eligible for auto-onboarding.

    Conservative AND: profile.md AND goals.md are missing/stub AND there are no
    real experience files. A partially set-up deploy does not auto-trigger.
    """
    if _meaningful(config.MEMORY_DIR / "profile.md"):
        return False
    if _meaningful(config.MEMORY_DIR / "goals.md"):
        return False
    if any(config.EXPERIENCES_DIR.glob("*.md")):
        return False
    return True
