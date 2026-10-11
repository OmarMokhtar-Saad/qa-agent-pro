"""Moved from handlers (refactor P2): corpus."""

from __future__ import annotations

import json
import logging
import uuid

from config.settings import settings
from handlers import _common, _reply
from tools.rag_store import query_corpus

logger = logging.getLogger("tools.mcp_handlers")


def shape_corpus_hits(query: str, hits: list) -> str:
    if not hits:
        return f"No corpus matches for **{_reply._clip_echo(query)}**."
    lines = [
        f"## Corpus matches for “{_reply._clip_echo(query)}” ({len(hits)})",
        "",
    ]
    for hit in hits[:10]:
        score = hit.get("score", 0.0)
        snippet = (hit.get("content") or "").replace("\n", " ").strip()[:160]
        meta = hit.get("metadata") or {}
        label = meta.get("feature") or meta.get("description") or ""
        prefix = f"{label}: " if label else ""
        lines.append(f"- (score={score:.2f}) {prefix}{snippet}")
    return "\n".join(lines)


async def handle_search_corpus(
    query: str,
    entry_type: str = "test_case",
    feature: str = "",
    *,
    progress: _common.ProgressCb = None,
) -> str:
    query = (query or "").strip()
    if not query:
        return "⚠️ Provide a search query."
    if not _common._rag_enabled():
        return "ℹ️ Corpus search is disabled in this build."
    entry_type = (entry_type or "test_case").strip()
    if entry_type not in ("test_case", "bug_report"):
        return f"⚠️ Unknown corpus '{entry_type}'. Choose 'test_case' or 'bug_report'."
    try:
        result = await query_corpus(
            query,
            entry_type=(entry_type or "test_case"),
            top_k=settings.qa_rag_top_k,
            metadata_filter=(
                {"feature": feature.strip()} if (feature or "").strip() else None
            ),
        )
        if result.get("error"):
            return f"⚠️ Corpus search failed: {result['error']}"
        hits = result.get("content") or []
        await _common._audit(
            "mcp_search_corpus", detail={"query": query[:120], "entry_type": entry_type}
        )
        return shape_corpus_hits(query, hits)
    except Exception as exc:
        logger.exception("handle_search_corpus failed")
        _common._capture_error(exc, "qa_search_corpus")
        return f"⚠️ Corpus search failed: {exc}"


# --------------------------------------------------------------------------- #
# Mirror hold (qa_mirror_hold)
#
# The desktop Mirror screen holds ONE device's lock for as long as it mirrors,
# so a test run cannot start driving a phone somebody is watching. The owner is
# `mirror-<token>`, one token per server process: only a hold made by the
# Mirror screen counts as the app's own, and a run's `mrun-...` hold on the same
# lock stays foreign to it (control and scrcpy stay blocked while a test holds
# the phone). The lock is a file lock held by this process, so it drops when the
# process exits -- the server exits when its stdin closes.
#
# No device command is sent and nothing here raises: every path returns JSON.

_MIRROR_OWNER_PREFIX = "mirror-"
_MIRROR_HOLD_OWNER = _MIRROR_OWNER_PREFIX + uuid.uuid4().hex[:12]
_MIRROR_HOLD_ACTIONS = ("acquire", "release", "status")


def _mirror_hold_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True)


def _mirror_hold_acquire(mobile_locks, name: str, base: dict) -> bool:
    """Take the lock; False when *base* already holds the final refusal."""
    res = mobile_locks.acquire(name, owner=_MIRROR_HOLD_OWNER)
    body = res.get("content")
    if res.get("error") or body is None:
        base["reason"] = str(res.get("error") or "the lock could not be taken")
        return False
    if not body.get("acquired"):
        base["reason"] = str(body.get("reason") or "held")
        base["owner"] = str(body.get("holder") or "")
        return False
    return True


def _mirror_hold_release(mobile_locks, name: str, base: dict) -> bool:
    """Release the lock; False when *base* already holds the final answer."""
    res = mobile_locks.release(name, owner=_MIRROR_HOLD_OWNER, as_holder=True)
    body = res.get("content")
    if res.get("error") or body is None:
        base["reason"] = str(res.get("error") or "the lock could not be released")
        return False
    base["reason"] = str(body.get("reason") or "")
    if not body.get("released") and str(base["reason"]).startswith("held by "):
        base["owner"] = str(base["reason"])[len("held by ") :]
        return False
    return True


def _mirror_hold_probe(mobile_locks, name: str, base: dict) -> None:
    """Read the lock's state into *base* (ok, owner, mine, mirror_hold, reason)."""
    probed = mobile_locks.holder(name)
    info = probed.get("content")
    if probed.get("error") or info is None:
        base["reason"] = str(probed.get("error") or "the lock could not be read")
        return
    held = bool(info.get("held"))
    owner = str(info.get("owner") or "") if held else ""
    mine = held and bool(info.get("mine"))
    base.update(
        ok=True,
        owner=owner,
        mine=mine,
        mirror_hold=mine and owner.startswith(_MIRROR_OWNER_PREFIX),
        reason=base["reason"] or ("held" if held else "free"),
    )


def handle_mirror_hold(serial: str = "", action: str = "status") -> str:
    """``qa_mirror_hold``: acquire, release or read ONE device's lock.

    Always a JSON object with ``ok``, ``action``, ``serial``, ``owner``,
    ``mine`` and ``mirror_hold``. ``mirror_hold`` is true only when the lock is
    held by THIS process (``mine``) AND its owner starts with ``mirror-``; it is
    computed here so the Kotlin side never reads ``mine`` on its own.
    """
    what = str(action or "").strip().lower()
    text = str(serial or "").strip()
    base = {
        "ok": False,
        "action": what,
        "serial": text,
        "owner": "",
        "mine": False,
        "mirror_hold": False,
        "reason": "",
    }
    try:
        from tools.mobile import locks as mobile_locks

        if what not in _MIRROR_HOLD_ACTIONS:
            base["reason"] = "action must be one of: " + ", ".join(_MIRROR_HOLD_ACTIONS)
            return _mirror_hold_json(base)
        name = mobile_locks.device_lock_name(text)
        if not name:
            base["reason"] = "that is not a device serial"
            return _mirror_hold_json(base)
        if what == "acquire":
            if not _mirror_hold_acquire(mobile_locks, name, base):
                return _mirror_hold_json(base)
        elif what == "release":
            if not _mirror_hold_release(mobile_locks, name, base):
                return _mirror_hold_json(base)
        _mirror_hold_probe(mobile_locks, name, base)
        return _mirror_hold_json(base)
    except Exception as exc:
        logger.exception("handle_mirror_hold failed")
        base["ok"] = False
        base["reason"] = str(exc)[:200]
        return _mirror_hold_json(base)
