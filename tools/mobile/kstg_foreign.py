"""Foreign screens: what the run saw outside the package under test is not learned."""

from __future__ import annotations


def observed(rec: dict) -> str:
    """The package of the screen a step ran on: the stamped one, else the activity's."""
    stamped = str(rec.get("screen_package") or "")
    if stamped:
        return stamped
    activity = str(rec.get("activity") or "")
    return activity.split("/", 1)[0] if "/" in activity else ""


def own(rec: dict, package: str) -> bool:
    """True unless the step ran on a screen of another package (unknown: own)."""
    seen = observed(rec)
    return not seen or not package or seen == package


def own_steps(steps: list, package: str) -> list:
    return [rec for rec in steps if own(rec, package)]


def screen_own(screen: object, package: str) -> bool:
    """Whether a perception screen belongs to *package* (no package read: yes)."""
    seen = str(screen.get("package") or "") if isinstance(screen, dict) else ""
    return not seen or not package or seen == package
