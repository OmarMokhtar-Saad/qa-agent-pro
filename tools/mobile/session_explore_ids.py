"""Mobile session explore lane: turn id and module constants (split from session_explore)."""

from __future__ import annotations

from typing import Any

#: What an exploratory turn is checkpointed as. An explore run used to write NO
#: case record at all: ``_submit_explore`` replayed the turn, folded the reply
#: and returned, while ``tools/mobile/report.py`` is case-driven from
#: ``cases/TC-*.json`` -- so a real four-turn run
#: (``mrun-20260904-194622-bd5142``) produced a 147 KB report reading "0 cases
#: checkpointed" and "No case has been checkpointed yet". A turn IS the unit of
#: work in this lane, so it is checkpointed as one, through the same
#: ``run_store`` path the suite lane uses. That is why the existing report
#: machinery draws it with no change to what a "case" means on disk.
EXPLORE_MODULE = "Exploration"
EXPLORE_TITLE_PREFIX = "Exploratory turn "


def explore_turn_tc_id(turn: Any) -> str:
    """The synthetic, STABLE case id for one exploratory turn.

    Stable is the load-bearing word: the same turn submitted twice (a refused
    script, a resumed chat) must reuse its id, or the report grows a duplicate
    card per retry. The turn number is the only identifier a turn has that
    survives an MCP restart, and ``run_store.valid_tc_id`` already accepts
    exactly this shape -- so no store contract moves for this fix.
    """
    try:
        number = int(turn or 0)
    except (TypeError, ValueError, OverflowError):
        number = 0
    return "TC-%03d" % max(1, min(number, 999999))


def explore_turn_number(state: object) -> int:
    """THE turn number an exploratory state is on. One coercion, one place.

    The checkpoint needs the NUMBER (it titles the card with it) and the
    recorder needs the ID; both must agree, so the coercion that turns a junk
    field into a usable number lives here and nowhere else.
    """
    body = state if isinstance(state, dict) else {}
    try:
        return max(1, int(body.get("turn") or 1))
    except (TypeError, ValueError, OverflowError):
        return 1


def explore_turn_case_id(state: object) -> str:
    """THE case id one exploratory turn writes, derived from its STATE.

    ONE producer, because two callers need the same answer at two moments:
    ``_checkpoint_explore_turn`` WRITES the turn's case under this id, and the
    recorder around the replay must file its clip onto that same case -- and it
    needs the id BEFORE the checkpoint exists. Deriving the number twice is how
    a clip lands on a case id nothing else uses, which is silent: the report
    finds no media on the case it renders and draws the old wireframe.
    """
    return explore_turn_tc_id(explore_turn_number(state))
