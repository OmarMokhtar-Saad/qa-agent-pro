"""Moved from handlers (refactor P2): _common."""

from __future__ import annotations

from handlers import _agents
from tools import settings_port


def test_cases_only() -> bool:
    """True when only the test-case tools should be exposed: the distribution
    build (optional modules absent) or QA_DIST_MODE=true."""
    return settings_port.get().qa_dist_mode or not _agents.FULL_EDITION


def mobile_capture() -> bool:
    """Always True -- device screen capture is ON, unconditionally.

    QA_MOBILE_CAPTURE was DELETED on 2026-08-13 and hardcoded to the `true` the
    dist .env.example already shipped. A named seam so the capture-disabled
    branches stay executable by their tests (they are unreachable in
    production), and so a test suite never shells out to `adb` by default.
    NOT settings-derived.
    """
    return True


def rag_enabled() -> bool:
    """Always True -- RAG corpus grounding is ON, unconditionally.

    QA_RAG_ENABLED was DELETED on 2026-08-13 and hardcoded to the `true` the
    dist .env.example already shipped. An EMPTY corpus was always a silent
    no-op, which is what made the per-install switch redundant. NOT
    settings-derived.
    """
    return True
