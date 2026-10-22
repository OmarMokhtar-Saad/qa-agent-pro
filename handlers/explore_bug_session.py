"""Exploratory-testing coaching sessions (explore_bug leaf)."""

from __future__ import annotations

from dataclasses import dataclass

from handlers import _agents, _common, explore_sessions


@dataclass(frozen=True)
class ExploreStepOpts:
    """Optional inputs of one coaching step."""

    tester_response: str = ""
    progress: _common.ProgressCb = None


def _new_session(feature: str) -> dict:
    """A fresh coaching session (registered by the caller)."""
    return {
        "feature": feature or "(unspecified feature)",
        "history": [],
        "memory": _agents.create_session_memory(),
    }


def _fold_tester_response(sess: dict, tester_response: str) -> None:
    """Fold the tester's observation of the previous step into the session."""
    resp = (tester_response or "").strip()
    if resp:
        explore_sessions.append_history(sess, "user", resp)
        sess["memory"] = _agents.update_coverage(
            sess["memory"], _agents.CoverageTurn(resp)
        )


def _explore_session_update(
    session_id: str, feature: str, tester_response: str
) -> dict:
    """Find or create the coaching session and fold in this turn's input."""
    feature = (feature or "").strip()
    sess = explore_sessions.get_session(session_id)
    if sess is None:
        sess = _new_session(feature)
        explore_sessions.put_session(session_id, sess)
    if feature:
        sess["feature"] = feature
    _fold_tester_response(sess, tester_response)
    return sess


def _record_covered_area(sess: dict, coach_meta: dict | None) -> None:
    """Add the host's <meta> area label to the session's covered areas."""
    # P10: the <meta> label is the structured replacement for the heuristic
    # area regex, and it is the host that now emits it.
    area = str((coach_meta or {}).get("area") or "").strip()[: _agents.MAX_AREA_CHARS]
    covered = (sess.get("memory") or {}).get("covered_areas")
    if (
        area
        and isinstance(covered, list)
        and area not in covered
        and len(covered) < _agents.MAX_AREAS
    ):
        covered.append(area)
