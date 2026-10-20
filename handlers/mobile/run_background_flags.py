"""Device-state flags of a setup call: a retry that changes one is not the same request.

Setup (install, launch, grants, locale, capture) does not depend on the goal or the
cases, so a retry with a new goal still adopts the orphan; a changed flag here does
not, and the note names what changed."""

from __future__ import annotations

#: Request fields that change what setup does to the device.
FIELDS = ("locale", "reset_app", "launch", "grant_permissions", "capture")


def of(req: object) -> dict[str, str]:
    """The device-state flag values of *req*, as comparable strings."""
    return {n: str(getattr(req, n, "") or "") for n in FIELDS}


def changed(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Names of the flags that differ between two calls."""
    return [n for n in FIELDS if old.get(n, "") != new.get(n, "")]


def note(names: list[str]) -> str:
    """The reply prefix after an orphan setup was dropped for changed flags."""
    return (
        "(Your previous setup call used different %s; that setup was dropped "
        "and this call started a fresh one.)\n\n" % ", ".join(names)
    )
