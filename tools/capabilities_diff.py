"""Capability artifact diff: what moved between two snapshots."""

from __future__ import annotations

from typing import Any, Mapping

#: The edition facts a snapshot records. Booleans only: a flip in any of them
#: changes which tools can exist at all, so a flip is a capability event even
#: when no tool name moved.
EDITION_FIELDS = ("full_edition", "test_cases_only", "mobile_modules_present")


# ---------------------------------------------------------------- diff ----- #


def _matrix_changes(
    base_matrix: Mapping[str, Any], cur_matrix: Mapping[str, Any]
) -> tuple[set, set, dict]:
    """``(lost, gained, detail)`` across the editions both matrices share."""
    lost: set = set()
    gained: set = set()
    detail: dict = {}
    for key in sorted(set(base_matrix) & set(cur_matrix)):
        for registry in ("tools", "prompts"):
            before = set((base_matrix[key] or {}).get(registry) or ())
            after = set((cur_matrix[key] or {}).get(registry) or ())
            for name in sorted(before - after):
                lost.add(name)
                detail.setdefault(name, []).append(key)
            gained |= after - before
    return lost, gained, detail


def _edition_flips(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> list:
    """``"edition:<field>"`` for each edition boolean whose value changed."""
    return [
        f"edition:{field}"
        for field in EDITION_FIELDS
        if bool((baseline.get("edition") or {}).get(field))
        != bool((current.get("edition") or {}).get(field))
    ]


# Returns ``{"lost", "gained", "flips", "detail"}``.
#
# * ``lost`` -- bare tool/prompt names that disappeared from AT LEAST ONE
#   edition. Bare names, not edition-qualified, because the opt-out flag has
#   to name the capability and an operator should not have to spell an edition
#   key to authorise a deliberate removal. Any-edition rather than
#   every-edition: a tool that stops registering on ONE edition is exactly the
#   v1.77.0 defect, and it would survive an intersection.
# * ``gained`` -- names that appeared. Never a failure.
# * ``flips`` -- ``"edition:<field>"`` for each edition boolean whose value
#   changed. A loss-class event because it changes which tools can exist.
# * ``detail`` -- ``{name: [edition keys]}`` for the message, so the operator
#   is told WHERE it disappeared rather than just that it did.
#
# An edition key present in ``current`` but absent from ``baseline`` (a NEW
# axis) contributes nothing: no capability was lost by asking a question the
# previous release was never asked.
def diff(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> dict:
    """What moved between two snapshots."""
    lost, gained, detail = _matrix_changes(
        baseline.get("matrix") or {}, current.get("matrix") or {}
    )
    flips = _edition_flips(baseline, current)
    return {
        "lost": sorted(lost),
        "gained": sorted(gained),
        "flips": sorted(flips),
        "detail": {name: sorted(keys) for name, keys in sorted(detail.items())},
    }


def losses(delta: Mapping[str, Any]) -> list:
    """The capability names a build must not lose silently, sorted."""
    return sorted(set(delta.get("lost") or ()) | set(delta.get("flips") or ()))
