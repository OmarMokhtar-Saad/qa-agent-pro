"""The network capture's case record and its cell vocabulary."""

from __future__ import annotations

from tools.mobile_evidence.render_esc import ev_esc, fmt

# A DIFFERENT PRODUCER from the app-log renderers: the parsed capture that
# ``capture.finish_network`` already attributed and wrote onto the case
# checkpoint. These functions take the CASE, and the join is the case id.

#: What a case says when it carries no network record at all -- an older run,
#: or a lane that does not capture. DISTINCT from an empty capture on purpose:
#: "nothing was recorded" and "nothing was found" are opposite news for a
#: tester, and only one of them means the app made no calls.
NO_RECORD = (
    "no packet capture was taken for this case, so nothing is known about what "
    "the app called -- this is not a case that made no calls"
)


#: What a case says when its capture was begun and never stopped -- a turn
#: whose replay failed, or a chat abandoned mid-turn. A third state, because it
#: is neither "nothing was recorded" nor "nothing was found".
NOT_STOPPED = (
    "a capture was started for this case and never stopped, so nothing was read "
    "back from it -- this is not a case that made no calls"
)


def _tail(body: dict) -> dict:
    return {
        "packets": body.get("packets"),
        "samples": body.get("samples"),
        "truncated": bool(body.get("truncated")),
        "note": str(body.get("note") or ""),
    }


def net(record: object) -> dict:
    """One case network record, every key present. Never raises.

    ``present`` comes from the fields, not from the dict being non-empty: a
    blank record has no stage, was never started, and names no reason.
    """
    body = record if isinstance(record, dict) else {}
    stage = str(body.get("stage") or "")
    skipped = str(body.get("skipped") or "") or None
    started = bool(body.get("started"))
    kept = [row for row in (body.get("rows") or ()) if isinstance(row, dict)]
    return {
        "present": bool(stage or skipped or started or kept),
        "started": started,
        "skipped": skipped,
        "stage": stage,
        "rows": kept,
        **_tail(body),
    }


def net_cell(value: object) -> str:
    """A number, or the em dash that means "this capture did not measure it".

    NOT a zero: a nought would read as "it sent nothing".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return '<span class="netcap">not measured</span>'
    return ev_esc(str(int(value)), 20)


def net_offset(row: dict) -> str:
    offset = row.get("first_offset_ms")
    if not isinstance(offset, int):
        return '<span class="netcap">not timed</span>'
    return ev_esc(fmt(max(0, offset)), 20)
