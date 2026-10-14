"""Mistake detail: which label picked which element, and which one was right.

Names only (op, visible label, resource ids); never a typed value. It rides in the
mistake's ``fix`` text so locator healing and the model avoid the same wrong pick.
"""

from __future__ import annotations

_CLIP = 320


def _rid(rec: dict | None) -> str:
    return str((rec or {}).get("target_rid") or "")


def detail(rec: dict, fix: dict | None) -> str:
    """``label 'X' chose A; correct B`` from the failed step and its fixing step."""
    parts = []
    label = str(rec.get("target_text") or rec.get("text_field") or "")
    if label:
        parts.append("%s label '%s'" % (rec.get("op") or "step", label[:60]))
    if _rid(rec):
        parts.append("chose %s" % _rid(rec))
    if _rid(fix) and _rid(fix) != _rid(rec):
        parts.append("correct %s" % _rid(fix))
    return "; ".join(parts)


def join(fixed: str, extra: str) -> str:
    """The fix text with *extra* appended, clipped."""
    return "; ".join(p for p in (fixed, extra) if p)[:_CLIP]
