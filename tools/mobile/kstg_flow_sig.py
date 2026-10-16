"""A flow's identity: its goal plus the signature of its route (see ``kstg_flow_build``).

The signature hashes the ordered ``(screen key before, op, stable target)`` of every
step. A typed value is already a ``{placeholder}`` in a saved step, and only the
selector fields of a target count (never coordinates or timing), so the same route
learned twice gets the same signature. Works on stored flows too (steps + meta).
"""

from __future__ import annotations

import hashlib
import json

_STABLE = ("rid", "text", "label", "role")
SIG_CHARS = 8


def _target(step: dict) -> dict:
    got = step.get("target")
    raw: dict = got if isinstance(got, dict) else {}
    out = {k: str(raw[k]) for k in _STABLE if raw.get(k)}
    for key in ("text", "label"):
        if step.get(key) and key not in out:
            out[key] = str(step[key])
    return out


def step_sig(step: dict, before: str) -> list:
    """The identity tuple of one step: ``[screen key, op, stable target]``."""
    return [before, str(step.get("op") or ""), _target(step)]


def signature(steps: list, befores: list) -> str:
    """Short hash of the ordered step identities (*befores* may be shorter than *steps*)."""
    padded = list(befores) + [""] * (len(steps) - len(befores))
    ids = [step_sig(s, str(b or "")) for s, b in zip(steps, padded)]
    raw = json.dumps(ids, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:SIG_CHARS]


def of_template(template: dict) -> str:
    """The signature of a stored flow template."""
    meta = template.get("meta") or {}
    return signature(list(template.get("steps") or []), list(meta.get("before") or []))


def of_records(recs: list) -> str:
    """The signature of the step records a learn run is about to save."""
    steps = [r.get("flow_action") or {} for r in recs]
    return signature(steps, [str(r.get("screen_key") or "") for r in recs])
