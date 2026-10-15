"""Foreign screens: what the run saw outside the package under test is not learned."""

from __future__ import annotations


def observed(rec: dict) -> str:
    """The package of the screen a step ran on: the stamped one, else the activity's."""
    stamped = str(rec.get("screen_package") or "")
    if stamped:
        return stamped
    activity = str(rec.get("activity") or "")
    return activity.split("/", 1)[0] if "/" in activity else ""


def _activity_package(activity: str) -> str:
    return activity.split("/", 1)[0] if "/" in activity else ""


def _same(package: str, *seen: str) -> bool:
    return all(not one or one == package for one in seen)


def own(rec: dict, package: str) -> bool:
    """True unless the step ran on a screen of another package (unknown: own)."""
    if not package:
        return True
    stamped = str(rec.get("screen_package") or "")
    activity = str(rec.get("activity") or "")
    return _same(package, stamped, _activity_package(activity))


def own_steps(steps: list, package: str) -> list:
    return [rec for rec in steps if own(rec, package)]


def screen_own(screen: object, package: str, activity: str = "") -> bool:
    """Whether a perception screen belongs to *package* (no package read: yes).
    The activity's package counts too, so a foreign activity is never buffered."""
    seen = str(screen.get("package") or "") if isinstance(screen, dict) else ""
    return not package or _same(package, seen, _activity_package(str(activity or "")))


def package_problem(package: str, steps: list) -> str:
    """Why a run must not be learned into *package*'s store, or ``""``."""
    if not package or package == "null":
        return "no package under test"
    seen = {observed(rec) for rec in steps} - {""}
    if seen and package not in seen:
        return "no observed screen belongs to the run package"
    return ""
