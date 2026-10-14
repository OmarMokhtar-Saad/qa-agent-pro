"""The per-task armed-value net: arm, learn and forget what must be masked."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable

from tools.mobile_shared.scrub_keys import (
    FIXTURE_SECRETS,
    MARKED_HEADERS,
    MIN_SENSITIVE_LEN,
)

# Populated per render by learn_sensitive / arm, reset by forget_sensitive so one page's
# values can never leak into the next page's mask decisions. A ContextVar, not a plain
# module-global set (T5.2): redaction now also runs at WRITE time
# (tools/mobile_capture/flows.py), concurrently with render, and asyncio gives no LIFO
# unwind guarantee across overlapping arming windows -- a save/restore context manager
# over a shared mutable set would let one task's `forget_sensitive` silently un-arm
# another task's still-in-flight values. Each asyncio task gets its OWN value here,
# and `arm` always SETS a new frozenset rather than mutating one in place, because a
# child task inherits the same object reference: an in-place mutation would leak
# straight back across the boundary this ContextVar exists to draw.
_SENSITIVE_VALUES: ContextVar[frozenset[str]] = ContextVar(
    "mobile_evidence_scrub_sensitive_values", default=frozenset()
)


def _credential_values(headers: dict) -> set[str]:
    found: set[str] = set()
    for key, value in headers.items():
        if str(key).lower() in MARKED_HEADERS and str(key).lower() != "accept-language":
            text = str(value).strip()
            if len(text) >= MIN_SENSITIVE_LEN and text.lower() not in FIXTURE_SECRETS:
                found.add(text)
    return found


def _collect_credentials(data: object) -> set[str]:
    stack = [data]
    seen = 0
    collected: set[str] = set()
    while stack and seen < 200000:
        node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            headers = node.get("requestHeaders")
            if isinstance(headers, dict):
                collected |= _credential_values(headers)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return collected


def learn_sensitive(data: object) -> int:
    """Collect the identifying values this render must never print, from the run itself.

    Called ONCE before rendering, not lazily during it: a body may render before any header
    does, and a net armed halfway through the page is not a net. Shape-independent on
    purpose: it walks whatever it is given looking for ``requestHeaders``.
    """
    collected = _collect_credentials(data)
    if collected:
        # A NEW frozenset, never a mutation of the one already held -- see the
        # module docstring above _SENSITIVE_VALUES.
        _SENSITIVE_VALUES.set(_SENSITIVE_VALUES.get() | collected)
    return len(_SENSITIVE_VALUES.get())


def _typed_additions(values: Iterable[object]) -> set[str]:
    return {
        str(value or "").strip()
        for value in (values or ())
        if len(str(value or "").strip()) >= MIN_SENSITIVE_LEN
    }


# Sets a NEW frozenset onto this task's :class:`~contextvars.ContextVar` rather than
# mutating one in place -- a child task inherits the same object reference, so an
# in-place ``|=`` would leak straight back across the boundary the ContextVar exists to
# draw (see the module docstring above ``_SENSITIVE_VALUES``).
def arm(values: Iterable[object]) -> int:
    """Add the tester's typed values to the net before a capture is written.

    The lane types credentials onto the device from ``Context.tester_inputs``, which is never
    persisted -- so the moment a logcat slice is taken is the ONLY moment the typed value is
    known and can be masked. A value shorter than MIN_SENSITIVE_LEN is not armed, for the
    reason :func:`mask_value` gives. Returns the size of the armed set.
    """
    additions = _typed_additions(values)
    if additions:
        _SENSITIVE_VALUES.set(_SENSITIVE_VALUES.get() | additions)
    return len(_SENSITIVE_VALUES.get())


def forget_sensitive() -> None:
    """Drop what the last render or capture learned, for THIS task only."""
    _SENSITIVE_VALUES.set(frozenset())


def armed() -> int:
    """How many values the net currently holds (for tests and disclosure)."""
    return len(_SENSITIVE_VALUES.get())


@contextmanager
def armed_scope(values: Iterable[object]):
    """Arm *values* for the duration of the block, in THIS task only.

    A thin ergonomic wrapper over :func:`arm` / a task-local reset -- used by
    ``tools/mobile_capture/flows.py`` to redact one case's flows at write time. Restores
    to whatever THIS task held before the scope opened (not to empty), so a nested or
    sibling scope in the same task is never clobbered. Safe under non-LIFO unwind ACROSS
    TASKS by construction: each ``asyncio`` task owns its own value in this ContextVar
    (a copy taken at task-creation time), so opening or closing a scope in one task can
    never touch another task's value -- there is no shared mutable object left to
    corrupt, which is the hazard a save/restore-over-a-plain-set design does not close
    (see the module docstring above ``_SENSITIVE_VALUES``).
    """
    token = _SENSITIVE_VALUES.set(_SENSITIVE_VALUES.get())
    try:
        arm(values)
        yield
    finally:
        _SENSITIVE_VALUES.reset(token)
