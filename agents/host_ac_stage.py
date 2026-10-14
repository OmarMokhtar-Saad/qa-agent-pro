"""Host mode: stage and id-assign the untrusted acceptance-criteria entries (split from host_ac.py)."""

from __future__ import annotations

from agents.host_ac_result import (
    _AC_ID_RE,
    _AC_MIN_DESC_CHARS,
    HostACResult,
    _ac_clean,
)
from tools.rtm import AcceptanceCriterion, normalize_ac_id


def _ac_entry_fields(entry: object) -> tuple[str, str] | None:
    """``(raw_id, description)`` of one entry, or None for an unusable entry type."""
    if isinstance(entry, str):
        return "", entry
    if not isinstance(entry, dict):
        return None
    raw_id = entry.get("ac_id") or entry.get("id") or ""
    desc = entry.get("description") or entry.get("text") or ""
    return (raw_id if isinstance(raw_id, str) else ""), desc


def _stage_ac_entries(entries: list, res: HostACResult) -> list:
    """``(requested_id_or_empty, description)`` per usable, distinct entry.

    Counts unusable entries into ``res.dropped``.
    """
    seen_text: set = set()
    staged: list = []
    for entry in entries:
        fields = _ac_entry_fields(entry)
        desc = _ac_clean(fields[1]) if fields else ""
        if fields is None or len(desc) < _AC_MIN_DESC_CHARS:
            res.dropped += 1
            continue
        key = desc.lower()
        if key in seen_text:
            continue
        seen_text.add(key)
        staged.append((normalize_ac_id(fields[0]), desc))
    return staged


def _keep_valid_ids(staged: list) -> tuple[set, list, list]:
    """Split ``staged`` into kept (valid, unused id) criteria and descriptions needing an id."""
    used_ids: set = set()
    out: list = []
    pending: list = []
    for want, desc in staged:
        if _AC_ID_RE.match(want or "") and want not in used_ids:
            used_ids.add(want)
            out.append(AcceptanceCriterion(ac_id=want, description=desc))
        else:
            pending.append(desc)
    return used_ids, out, pending


def _next_free_id(used_ids: set, counter: int) -> tuple[str, int]:
    """The next positional ``AC-NNN`` id not in ``used_ids``, and the counter it sits at."""
    while f"AC-{counter:03d}" in used_ids:
        counter += 1
    return f"AC-{counter:03d}", counter


def _assign_ac_ids(staged: list, res: HostACResult) -> list:
    """Keep valid unused ids, REASSIGN the rest to the next free positional id.

    Counts reassignments into ``res.reassigned``; returns criteria sorted by id.
    """
    used_ids, out, pending = _keep_valid_ids(staged)
    counter = 1
    for desc in pending:
        new_id, counter = _next_free_id(used_ids, counter)
        used_ids.add(new_id)
        res.reassigned += 1
        out.append(AcceptanceCriterion(ac_id=new_id, description=desc))
    out.sort(key=lambda a: a.ac_id)
    return out
