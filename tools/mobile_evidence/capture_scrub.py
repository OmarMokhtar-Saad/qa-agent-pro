"""Redaction before the first byte reaches disk (plan D4).

The tester's typed values for a call are armed into the value net for the
duration of the slice and forgotten in a ``finally``; the key/pair nets need no
arming and run always. ``run_store``'s own redaction is dict-key based and cannot
see free text, so this module is where a logcat line is made safe. The two secret
tests that ``rglob`` every file under the cache root are the reason.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from tools.mobile import run_store
from tools.mobile_shared import scrub


class _SliceDest(NamedTuple):
    run_id: str
    tc_id: str
    name: str


def _scrub_lines(text: str, tester_inputs: object) -> tuple[str, int]:
    """Every line through the nets, with the tester's values armed for the duration."""
    values = (
        list((tester_inputs or {}).values()) if isinstance(tester_inputs, dict) else []
    )
    try:
        scrub.arm(values)
        lines = [scrub.scrub_text(line) for line in str(text or "").splitlines()]
    finally:
        scrub.forget_sensitive()
    return ("\n".join(lines) + "\n") if lines else "", len(lines)


def _scrub_record(line: str) -> str:
    """One ndjson line: parsed and scrubbed as a structure when it is JSON, as text otherwise."""
    stripped = line.strip()
    if not stripped:
        return ""
    try:
        record = json.loads(stripped)
    except ValueError:
        return scrub.scrub_text(stripped)
    if not isinstance(record, (dict, list)):
        return scrub.scrub_text(stripped)
    return json.dumps(scrub.scrub_json(record), ensure_ascii=False, sort_keys=True)


def _write_slice(
    body: dict, dest: _SliceDest, tester_inputs: object
) -> tuple[dict, str, int]:
    """Scrub the dumped text and write it; returns (write result, scrubbed text, line count)."""
    text, count = _scrub_lines(str(body.get("text") or ""), tester_inputs)
    written = run_store.write_evidence_text(run_store.EvidenceRef(*dest), text)
    return written, text, count
