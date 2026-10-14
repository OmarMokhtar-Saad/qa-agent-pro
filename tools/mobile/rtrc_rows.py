"""Step-table rows built from trace entries, each re-redacted first."""

from __future__ import annotations

from typing import Any

from tools.mobile import run_store
from tools.mobile.report_text import MAX_TEXT, _ms, _text
from tools.mobile.rtrc_action import _action_line
from tools.mobile.rtrc_plain import _plain_action

MAX_ROWS = 400


def _trace_rows(trace: Any) -> list:
    """The step table's rows, each re-redacted before anything is rendered."""
    return [
        _trace_row(entry)
        for entry in list(trace or [])[:MAX_ROWS]
        if isinstance(entry, dict)
    ]


# The ``run_store.redact`` call below is STILL NOT canary-provable, because
# MUTATION SAID SO: deleting it kept every test green, since `_action_line`
# masks on the `secret` marker and on CREDENTIAL_TERMS ITSELF. The structural
# control is the one that holds; this call is kept as depth for the day a
# field IS rendered that only key-based redaction covers. A security comment
# that overstates its own line gets a control trusted for what it does not do.
def _trace_row(entry: dict) -> dict:
    """One step-table row from one trace entry, re-redacted first."""
    safe = run_store.redact(entry)
    safe = safe if isinstance(safe, dict) else {}
    action = safe.get("action")
    action = action if isinstance(action, dict) else {}
    return {**_step_cells(safe, action), **_screen_cells(safe)}


def _step_cells(safe: dict, action: dict) -> dict:
    """The action, outcome and timing cells of one step-table row."""
    return {
        "index": _text(safe.get("index"), 8),
        "op": _text(action.get("op"), 24) or "?",
        "action": _action_line(safe.get("action")),
        "plain": _plain_action(action),
        "act": action,
        "target_id": _text(
            (action.get("target") or {}).get("id")
            if isinstance(action.get("target"), dict)
            else "",
            40,
        ),
        "outcome": _text(safe.get("outcome"), 40) or "-",
        "ms": _ms(safe.get("ms")),
        "detail": _text(safe.get("detail"), MAX_TEXT),
        "notes_applied": _notes_applied(safe.get("knowledge")),
    }


# Derived ONCE. The id that names a screen and the key that names a LOOK
# at that screen must come from the same normalisation: a truncation on
# one side and none on the other is two derivations of one id, which is
# a defect the neighbouring plan for this file has already shipped.
# WHICH screen each end was, above; WHAT was on it, here. Two
# questions, two names, and the key is minted by the STORE's own
# producer so this reader cannot ask for a key the writer would
# not have written. An observation id is 25 characters -- a
# 12-hex screen id, a dash and 12 more -- so it survives the
# 40-character `_text` a frame applies to it, with no room for
# two observations to collide on a shared prefix.
def _screen_cells(safe: dict) -> dict:
    """The before and after screen ids and observation keys of one row."""
    before_id = _text(safe.get("before_screen_id"), 40)
    after_id = _text(safe.get("after_screen_id"), 40)
    return {
        "before": before_id,
        "after": after_id,
        "before_obs": _obs_key(before_id, safe.get("before_screen_hash")),
        "after_obs": _obs_key(after_id, safe.get("after_screen_hash")),
    }


# ``Notes applied: #3 wait confirmed (1200 ms); ...`` from a trace entry's
# ``knowledge`` list, or ``""`` when no saved note touched the step.
def _notes_applied(raw: object) -> str:
    """The ``Notes applied: ...`` text of a trace entry, or ``""`` when none."""
    parts = []
    for item in (raw if isinstance(raw, list) else [])[:6]:
        if not isinstance(item, dict):
            continue
        parts.append(
            "#%s %s %s%s"
            % (
                _text(item.get("note_id"), 12),
                _text(item.get("kind"), 12),
                _text(item.get("outcome"), 20).replace("_", " "),
                " (%s ms)" % _text(item.get("ms"), 10) if item.get("ms") else "",
            )
        )
    return ("Notes applied: " + "; ".join(parts)) if parts else ""


# NOT `run_store.observation_key` directly. That function degrades to the bare
# screen id when a screen carries no content hash, which is correct where it
# lives -- a hashless screen IS its own single observation, and the store must
# put it somewhere. At THIS boundary the degraded value is poison: it is
# truthy, so `_changed`'s "do both ends have an observation?" guard passes,
# and it is a screen id, so the comparison is then an observation key against
# an id -- two namespaces, silently.
#
# That is not a rare shape. `executor` stamps `before_screen_hash` on every
# entry but `after_screen_hash` at ONE of fourteen sites, so "before hashed,
# after not" is what thirteen of fourteen replay paths produce. Measured on a
# four-turn chat: every "Screen after" frame rendered the FULL final
# conversation, including a reply the app had not yet produced, captioned as
# what the action left on screen. Before this feature the frame was suppressed;
# drawing a fabricated one is strictly worse, and a tester cannot tell.
#
# So: no hash, no observation key, and every reader falls back to the screen id
# it used before. One producer, one meaning.
def _obs_key(ident: object, screen_hash: object) -> str:
    """The observation key for this end of a step, or ``""`` when there is none."""
    digest = str(screen_hash or "")
    if not digest:
        return ""
    return run_store.observation_key(ident, digest)


# CONTENT when both ends recorded a content hash, identity otherwise. The id
# alone answers "a different screen", and on a chat every reply is the same
# screen with different content -- so the frame this used to suppress was the
# only evidence the reply ever arrived. The fallback is what a record written
# before observations existed still gets, unchanged.
def _changed(row: dict) -> bool:
    """Did this action leave a DIFFERENT screen than it found?"""
    if row["before_obs"] and row["after_obs"]:
        return row["after_obs"] != row["before_obs"]
    return row["after"] != row["before"]
