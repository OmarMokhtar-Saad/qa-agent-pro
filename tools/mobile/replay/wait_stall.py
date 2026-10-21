"""Replay waits: a wait that ran out after its progress went away is a stalled work (G02).

Measured (``mrun-20261021-162641-0fedce``): a download went ``Download`` ->
``Downloading... 0%`` and, a turn later, back to ``Download``. Every wait said
plain "timed out": nothing remembered that progress had been on screen. The
progress texts a timed-out wait sees are kept for the run (in memory for the
wait itself, in the run folder across turns); a later timed-out wait for the
same text that finds the progress gone says the work stalled and reset.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import run_io
from tools.mobile.replay.wait_inprogress import progress_on
from tools.mobile.replay.waits_progress import _grew_into, _texts

logger = logging.getLogger("tools.mobile.executor")

_KEY = "wait_seen_progress"
_FILE = "wait_progress.json"


def forget_seen(ctx: Any) -> None:
    """Start a wait with an empty memo: progress an earlier wait saw is not its own."""
    env = getattr(ctx, "env", None)
    if env is not None:
        env.pop(_KEY, None)


def note_seen(ctx: Any, screen: object) -> None:
    """Remember the progress texts a read of the running wait showed."""
    env = getattr(ctx, "env", None)
    if env is not None and (seen := progress_on(screen)):
        env.setdefault(_KEY, {}).update(dict.fromkeys(seen))


def _wanted(entry: dict) -> str:
    """The text the timed-out wait looked for (the memo's key)."""
    action = entry.get("action")
    return str((action or {}).get("text") or "") if isinstance(action, dict) else ""


def _memo_path(ctx: Any):
    run_id = str(getattr(ctx, "run_id", "") or "")
    return run_io.run_path(run_id) / _FILE if run_io.valid_run_id(run_id) else None


def _load(ctx: Any) -> dict:
    path = _memo_path(ctx)
    body = run_io._read_json(path) if path else None
    return body if isinstance(body, dict) else {}


def _save(ctx: Any, body: dict) -> None:
    try:
        if path := _memo_path(ctx):
            run_io._write_json(path, body)
    except Exception:
        logger.exception("mobile.wait_stall: could not keep the progress memo")


def earlier_progress(ctx: Any, wanted: str) -> list[str]:
    """Progress texts seen by this wait or by an earlier turn's wait for *wanted*."""
    env = getattr(ctx, "env", None) or {}
    here = list(env.pop(_KEY, None) or [])
    before = _load(ctx)
    kept = list(before.get("seen") or []) if before.get("wanted") == wanted else []
    return here or kept


def note_stalled(ctx: Any, entry: dict, screen: object) -> bool:
    """Re-word a timed-out wait *entry* whose progress is gone; True when it did.

    While progress is still on *screen* the texts are kept for the next turn
    (``note_in_progress`` words that case); a stall clears the memo.
    """
    if entry.get("outcome") != "wait_timeout":
        return False
    wanted = _wanted(entry)
    seen = earlier_progress(ctx, wanted)
    if now := progress_on(screen):
        _save(ctx, {"wanted": wanted, "seen": now})
        return False
    _save(ctx, {})
    if not seen or not _fell_back(seen, screen):
        return False
    entry["stalled"] = True
    entry["detail"] = _stalled_detail(seen[-1], entry)
    return True


def _fell_back(seen: list[str], screen: object) -> bool:
    """True when *screen* shows a label a seen progress text grew out of.

    Progress that is gone with no such label (the work finished and the screen
    moved on) is not a reset: the plain timeout stands.
    """
    return any(_grew_into(text, set(seen)) for text in _texts(screen))


def _stalled_detail(shown: str, entry: dict) -> str:
    """The sentence of a stalled wait."""
    return (
        "stalled: %r was showing earlier and the screen is back without any "
        "progress, so the work reset (not still running). %s"
        % (shown, entry.get("detail") or "")
    ).strip()[:300]
