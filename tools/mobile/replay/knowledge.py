"""Replay engine: app-knowledge reads, hints and wait guards. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    app_knowledge,
    knowledge_hooks,
)
from tools.mobile.replay.context import Context, _ReplayRun
from tools.mobile.replay.dump import _focus_probe
from tools.mobile.replay.guard import NON_ACTUATING_OPS
from tools.mobile.replay.screen import _screen_has, _screen_has_element
from tools.mobile.replay.waits import _wait_until_element, _wait_until_text

logger = logging.getLogger("tools.mobile.executor")


def _load_knowledge(ctx: Context) -> None:
    """Load this app's saved-note guards ONCE per replay. Read-only: an app
    with no store creates nothing. Never raises; any failure is "no notes"."""
    if getattr(ctx, "knowledge", None) is not None:
        return
    ctx.knowledge = _read_knowledge(str(getattr(ctx, "package", "") or ""))


def _read_knowledge(package: str, facts: object = None) -> dict:
    """*package*'s guards plus empty tallies. Touches no Context, so a worker
    thread still running after its awaiting task was cancelled cannot set
    ``ctx.knowledge`` behind ``_flush_knowledge``'s back. Never raises.

    Env-scoped guards are filtered here against ``facts.env``: a match is
    stamped ``_env_ok`` (so ``candidates`` applies it), a mismatch goes to
    ``skipped_env``. ``snap`` holds the plugs' read-only snapshot."""
    guards: list = []
    try:
        if package:
            loaded = app_knowledge.load_guards(package)
            if not loaded.get("error"):
                guards = list(loaded.get("content") or [])
    except Exception:
        logger.exception("mobile app knowledge: load failed; continuing without notes")
    guards, skipped = _split_env(guards, getattr(facts, "env", None))
    return {
        "package": package,
        "guards": guards,
        "counts": {},
        "events": [],
        "spent_ms": 0,
        "skipped_env": skipped,
        "snap": _read_snapshot(package, facts),
    }


def _split_env(guards: list, env: object) -> tuple[list, list]:
    """Keep guards with no env condition or a matching one (stamped ``_env_ok``);
    the rest are skipped. An empty run env fails closed."""
    from tools.mobile import knowledge_run

    kept: list = []
    skipped: list = []
    for guard in guards:
        want = (guard.get("when") or {}).get("env")
        if not want:
            kept.append(guard)
        elif knowledge_run.env_matches(want, env):
            kept.append(dict(guard, _env_ok=True))
        else:
            skipped.append(guard)
    return kept, skipped


def _read_snapshot(package: str, facts: object) -> dict:
    """The plugs' merged read-only snapshot; ``{}`` without facts or an existing
    store (nothing is created). One connection, closed here. Never raises."""
    if facts is None or not package:
        return {}
    try:
        path = app_knowledge.db_path(package)
        if path is None or not path.exists():
            return {}
        conn = app_knowledge._connect(path, write=False)
        try:
            return knowledge_hooks.snapshots(conn, facts)
        finally:
            conn.close()
    except Exception:
        logger.exception("mobile app knowledge: snapshot failed")
        return {}


def _flush_kbuf(ctx: Context) -> None:
    """Hand ``ctx.kbuf`` to the plugs' ``flush`` in ONE batched call, then empty
    it. Never raises."""
    events = list(getattr(ctx, "kbuf", None) or [])
    if not events:
        return
    try:
        knowledge_hooks.flush_kbuf(ctx, events)
    except Exception:
        logger.exception("mobile app knowledge: kbuf flush failed")
    finally:
        ctx.kbuf = []


def _flush_knowledge(ctx: Context) -> None:
    """Write the tallies buffered by ``_apply_knowledge``, in ``replay``'s
    ``finally`` so success, error, exception and abort all flush. Never raises.

    It then DROPS the loaded notes (``ctx.knowledge = None``), so the next
    ``replay`` on the same Context reloads them: a note added or retired
    between two replays is seen by the second one."""
    know = getattr(ctx, "knowledge", None)
    ctx.knowledge = None
    _flush_kbuf(ctx)
    if not isinstance(know, dict):
        return
    know.pop("snap", None)
    try:
        if know.get("counts") or know.get("events"):
            app_knowledge.flush_counters(
                str(know.get("package") or ""),
                know.get("counts") or {},
                know.get("events") or [],
                run_id=str(getattr(ctx, "run_id", "") or ""),
            )
    except Exception:
        logger.exception("mobile app knowledge: flush failed")
    finally:
        know["counts"] = {}
        know["events"] = []
        know["spent_ms"] = 0


def _action_rids(action: object, screen: object) -> list:
    """Every resource id this action could hit: the target's own ``rid``, plus
    the rid of each element on *screen* that its ``id`` names or whose text or
    content-desc contains its ``text`` / ``label`` (``tap_text``'s ``text``
    too). So an avoid note on a rid also stops a tap aimed by its words."""
    target = getattr(action, "target", None)

    def pick(name: str) -> str:
        raw = (
            target.get(name) if isinstance(target, dict) else getattr(target, name, "")
        )
        return " ".join(str(raw or "").split()).lower()

    rids = [pick("rid")] if pick("rid") else []
    words = [w for w in (pick("text"), pick("label")) if w]
    own = " ".join(str(getattr(action, "text", "") or "").split()).lower()
    if target is None and own:
        words.append(own)
    want_id = pick("id")
    elements = screen.get("elements") if isinstance(screen, dict) else None
    for element in elements or []:
        if not isinstance(element, dict) or not element.get("rid"):
            continue
        labels = [
            " ".join(str(element.get(f) or "").split()).lower()
            for f in ("text", "desc")
        ]
        by_id = bool(want_id) and str(element.get("id") or "").lower() == want_id
        if by_id or any(w in label for w in words for label in labels if label):
            rids.append(str(element["rid"]))
    return rids


async def _apply_knowledge(
    run: "_ReplayRun",
    step_at: tuple,
    op: str,
    screen: object,
    deadline: float,
) -> tuple[object, str | None]:
    """Apply this app's saved notes to ONE action, BEFORE it resolves a target.

    ``(screen, refusal)``. An ``avoid`` note that matches refuses the op (the
    reply names the note and echoes it as untrusted text). A ``wait`` note polls
    for what it names within a budget that can never hang the replay: the
    smallest of the note's ms, ``MAX_WAIT_MS``, what the script's waits leave of
    ``MAX_TOTAL_WAIT_MS`` after the guard waits already spent, and the time left
    to the replay deadline. A timeout PROCEEDS and is buffered as
    ``contradicted``; an interruption proceeds too, logged as ``interrupted``
    and tallied neither way. This decides nothing about the destructive guard, which
    still runs after and outranks it. Never raises.

    *step_at* is ``(index, action, entry)``; the context and the script's
    items come from *run*.
    """
    ctx, items = run.ctx, run.items
    _index, action, entry = step_at
    try:
        know = getattr(ctx, "knowledge", None)
        matched = _matched_notes(know, action, op, screen)
        if not matched:
            return screen, None
        live = await _live_guards(ctx, know, matched, deadline)
        refusal = _knowledge_refusal(know, live, op, entry)
        if refusal is not None:
            return screen, refusal
        fired: list[dict] = []
        for guard in live:
            if guard.get("kind") != "wait":
                continue
            budget = _guard_wait_budget(guard, know, items, deadline)
            if budget <= 0:
                continue
            screen, outcome, spent = await _run_wait_guard(
                ctx, know, guard, screen, budget
            )
            _tally_wait(know, guard, outcome, op)
            fired.append(_fired_note(guard, "wait", outcome, spent))
        if fired:
            entry["knowledge"] = fired
        return screen, None
    except Exception:
        logger.exception("mobile app knowledge: guard failed; continuing without notes")
        return screen, None


def _matched_notes(know: object, action: object, op: str, screen: object) -> list:
    """The saved guard notes that match this action, or ``[]`` when none apply."""
    if op == "wait" or not isinstance(know, dict) or not know.get("guards"):
        return []
    return app_knowledge.candidates(know["guards"], op, _action_rids(action, screen))


def _knowledge_refusal(know: dict, live: list, op: str, entry: dict) -> str | None:
    """The avoid-note refusal for *op*, or ``None``."""
    # An avoid note refuses an ACTION. A read-only assert or a poll wait
    # changes nothing, so it is never refused.
    if op in NON_ACTUATING_OPS or op in actions_mod.WAIT_OPS:
        return None
    return _avoid_refusal(know, live, op, entry)


def _fired_note(guard: dict, kind: str, outcome: str, spent: int) -> dict:
    return {"note_id": guard.get("id"), "kind": kind, "outcome": outcome, "ms": spent}


async def _live_guards(
    ctx: Context, know: dict, matched: list, deadline: float
) -> list:
    """The matched guards whose activity condition holds on the live focus."""
    probe = ""
    if any((g.get("when") or {}).get("activity") for g in matched):
        began = time.monotonic()
        probe = await _focus_probe(ctx.serial, min(2.0, max(0.0, deadline - began)))
        know["spent_ms"] = int(know.get("spent_ms") or 0) + int(
            max(0.0, time.monotonic() - began) * 1000
        )
    return [g for g in matched if app_knowledge.activity_ok(g, probe)]


def _avoid_refusal(know: dict, live: list, op: str, entry: dict) -> str | None:
    """The refusal text for the first live ``avoid`` note, logging it on *entry*."""
    for guard in live:
        if guard.get("kind") != "avoid":
            continue
        know["events"].append(
            {"note_id": guard.get("id"), "event": "avoided", "detail": op}
        )
        entry["knowledge"] = [_fired_note(guard, "avoid", "avoided", 0)]
        return (
            "A saved note for this app says not to do this here (note #%s). "
            "Do something else, skip this step or mark it blocked, or ask the "
            "tester. If the note is wrong, retire it with "
            'qa_mobile_notes(action="retire", note_id=%s). %s'
            % (guard.get("id"), guard.get("id"), app_knowledge.echo(guard))
        )
    return None


def _guard_wait_budget(guard: dict, know: dict, items: list, deadline: float) -> int:
    """Ms a ``wait`` note may poll: the smallest of every cap that applies."""
    then = guard.get("then") or {}
    return min(
        int(then.get("ms") or app_knowledge.DEFAULT_GUARD_WAIT_MS),
        actions_mod.MAX_WAIT_MS,
        actions_mod.MAX_TOTAL_WAIT_MS
        - actions_mod.total_wait_ms(items)
        - int(know.get("spent_ms") or 0),
        int((deadline - time.monotonic()) * 1000),
    )


async def _run_wait_guard(
    ctx: Context, know: dict, guard: dict, screen: object, budget: int
) -> tuple[object, str, int]:
    """Poll for what a ``wait`` note names: ``(screen, outcome, spent_ms)``."""
    then = guard.get("then") or {}
    began = time.monotonic()
    ctx.wait_interruption = ""
    if then.get("until_rid"):
        already = _screen_has_element(
            screen, str(then["until_rid"]), str(then.get("until_rid_text") or "")
        ) != bool(then.get("until_gone"))
    else:
        already = _screen_has(screen, str(then.get("until_text") or ""))
    if already:
        # The screen already shows what the note waits for: nothing was
        # waited on, so it neither confirms nor contradicts the note.
        return screen, "already_met", 0
    if then.get("until_rid"):
        satisfied, screen, _late = await _wait_until_element(
            ctx,
            screen,
            str(then["until_rid"]),
            str(then.get("until_rid_text") or ""),
            bool(then.get("until_gone")),
            budget,
        )
    else:
        satisfied, screen, _late = await _wait_until_text(
            ctx, screen, str(then.get("until_text") or ""), budget
        )
    spent = int(max(0.0, time.monotonic() - began) * 1000)
    know["spent_ms"] = int(know.get("spent_ms") or 0) + spent
    interrupted, ctx.wait_interruption = ctx.wait_interruption, ""
    if satisfied:
        return screen, "confirmed", spent
    return screen, "interrupted" if interrupted else "contradicted", spent


def _tally_wait(know: dict, guard: dict, outcome: str, op: str) -> None:
    """Log a wait outcome; an interruption says nothing about the note, so it
    or an already-met condition is logged but tallied neither way."""
    if outcome not in ("interrupted", "already_met"):
        tally = know["counts"].setdefault(
            guard.get("id"), {"confirmed": 0, "contradicted": 0}
        )
        tally[outcome] += 1
    know["events"].append({"note_id": guard.get("id"), "event": outcome, "detail": op})
