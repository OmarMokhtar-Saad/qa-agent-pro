"""qa-doctor's Feature gates rows (moved from setup_check)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _common, mobile_gate
from tools import rag_store

logger = logging.getLogger("tools.mcp_handlers")


# Feature Analysis is a FULL-edition capability only: the
# test-cases-only edition does not register its tools
# (2026-08-03), so listing it there advertises something the
# tester cannot reach. The flag was DELETED 2026-08-14 (batch
# 8c) and hardcoded ON, so on the full edition this row is now
# always true -- it is kept because a tester reading qa-doctor
# wants to know the capability EXISTS, not which flag carried it.
# The emulator LANE, which is not the same capability as capture
# above: capture takes a screenshot to ground generation, this
# runs cases on a device. Named on every edition, on or off, so a
# tester reading qa-doctor learns the capability exists -- and the
# label carries no recipe, because the per-install detail is in
# the section further down and only when the lane is on.
def _capability_gate_rows(mobile_state: str) -> list[tuple[str, bool]]:
    """qa-doctor's capability rows, before the RAG row. Reads flags at call time."""
    return [
        *(
            []
            if _common.test_cases_only()
            else [
                (
                    "Feature Analysis (always on since 2026-08-14)",
                    True,
                )
            ]
        ),
        ("Mobile capture (always on since 2026-08-13)", _common.mobile_capture()),
        mobile_gate._mobile_gate_row(mobile_state),
        ("Swagger/OpenAPI links (always on since 2026-08-13)", True),
    ]


# Say what was OBSERVED, in the label. A bare tick on an empty
# corpus advertises grounding that this server's own docstring
# calls a silent no-op, and the count is observable right here.
def _corpus_label(entries: int) -> str:
    return (
        f"RAG corpus — grounding on {entries} past case(s) from this install"
        if entries
        else "RAG corpus — empty so far, so nothing is "
        "grounded on past work yet; it fills as you generate"
    )


# The seam's own docstring concedes this is client-dependent:
# "a client that cannot show dialogs is UNAFFECTED... renders
# the menu". So state what is true of the SERVER, and what the
# tester sees when their client cannot.
async def _corpus_and_dialog_rows() -> list[tuple[str, bool]]:
    """The RAG row (with the observed corpus count) and the wizard-dialogs row."""
    _corpus_entries = 0
    with never_raise(logger, "corpus count skipped", exc_info=True):
        _corpus_entries = await rag_store.corpus_entry_count()
    return [
        (_corpus_label(_corpus_entries), _common.rag_enabled()),
        (
            "Wizard dialogs — offered to every client; one without "
            "dialog support gets the same choices as a numbered menu",
            True,
        ),
    ]


async def setup_gate_lines(mobile_state: str) -> list[str]:
    """qa-doctor's rows under "### Feature gates", one ticked line per gate.

    The mobile section and the unfinished-preps note live in
    `setup_handler._setup_mobile_lines`: tests/mobile/test_doctor_off_lane_devices.py
    pins the off-lane note beside the disclosure in that function's own source.
    Every flag is a module global read at call time.
    """
    gates = _capability_gate_rows(mobile_state) + await _corpus_and_dialog_rows()
    return [f"- {'✅' if value else '⬜'} {label}" for label, value in gates]
