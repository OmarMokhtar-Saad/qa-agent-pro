"""PREPARE's first-reply gate: a suite for this source already exists."""

from __future__ import annotations

import logging
import time

from config.settings_guard import never_raise
from handlers import generate
from handlers.gprep_args import _PrepArgs
from tools import suite_family, suite_prep_paths, suite_store

logger = logging.getLogger("tools.mcp_handlers")

NOTICE_MAX_SUITES = 5
_PAST_TTL_S = 1800.0
# source -> when its flow passed the gate (shown-and-answered, or nothing to show)
_past_gate: dict[str, float] = {}


def existing_suite_notice(earlier: list) -> str:
    """The notice that OPENS the reply: the newest earlier suites and the choices."""
    shown = earlier[-NOTICE_MAX_SUITES:]
    lines = "\n".join(suite_family.earlier_line(e) for e in shown)
    if len(earlier) > len(shown):
        lines += f"\n- +{len(earlier) - len(shown)} more older suites"
    return (
        f"## A suite for this source already exists ({len(earlier)} earlier)\n\n"
        f"{lines}\n\n"
        "Nothing has been generated. Ask the tester which they want:\n\n"
        "1. **reuse** - stop here and point to the latest workbook above.\n"
        "2. **open** - stop here and show where the workbook is (path above).\n"
        "3. **regenerate** - call this tool again with the SAME arguments plus "
        "`proceed_anyway=true`. The new suite is saved as the next version of "
        "this source's suite and its overlap with the previous version is "
        "reported at the end.\n\n"
        "Do not call the tool again until the tester has chosen."
    )


async def existing_suite_gate(
    a: _PrepArgs, stage_token: str
) -> generate.PreparePayloadResult | None:
    """A clarify listing the newest earlier suites of the source, or None to go on.

    The notice shows once per flow, whatever fields the first call carries: a
    call with a stage token, an ack or `proceed_anyway=true` is past it, and so
    is any call for a source already marked past the gate (the first call found
    nothing to warn about, or the tester answered the notice). Never raises."""
    if _is_follow_up(a, stage_token):
        _mark_past(a.text)
        return None
    with never_raise(logger, "existing-suite gate failed", exc_info=True):
        earlier = await suite_store.earlier_suites(a.text)
        if earlier and not _is_past(a.text, earlier[-1].created_at):
            notice = existing_suite_notice(await _with_prep_paths(earlier))
            return generate.PreparePayloadResult(clarify=notice)
        _mark_past(a.text)
    return None


def _gate_key(source: str) -> str:
    """The same ticket gets one key whether given as a URL or a bare key."""
    text = (source or "").strip()
    return suite_family.source_key(text) or text[:200]


def _mark_past(source: str) -> None:
    now = time.time()
    for k in [k for k, t in _past_gate.items() if now - t > _PAST_TTL_S]:
        del _past_gate[k]
    _past_gate[_gate_key(source)] = now


def _is_past(source: str, newest_suite_at: float) -> bool:
    """True when the source passed the gate AFTER its newest suite was saved."""
    seen = _past_gate.get(_gate_key(source), 0.0)
    return seen > newest_suite_at and time.time() - seen <= _PAST_TTL_S


def _is_follow_up(a: _PrepArgs, stage_token: str) -> bool:
    """True when this call is an answer to the notice or a staged continuation."""
    return bool(
        a.proceed_anyway or stage_token.strip() or a.image_gate_ack or a.image_carry_ack
    )


async def _with_prep_paths(earlier: list) -> list:
    """Fill a missing workbook path from the prep records (one query for all)."""
    wanted = [e.prep_id for e in earlier if not e.export_path and e.prep_id]
    paths = await suite_prep_paths.export_paths(wanted)
    return [_fill(e, paths) for e in earlier]


def _fill(e: suite_family.Earlier, paths: dict) -> suite_family.Earlier:
    if e.export_path or e.prep_id not in paths:
        return e
    return e._replace(export_path=paths[e.prep_id])
