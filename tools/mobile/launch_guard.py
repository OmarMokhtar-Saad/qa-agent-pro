"""Which package a ``launch`` may open.

Owner decision: ``{"op": "launch", "package": ...}`` is allowed for the app under
test, the Firebase App Tester, and the packages the step declared in
``expect_apps``. Anything else is refused, and the refusal names the allowed set
so the model can fix the step instead of guessing.

The package is whitelist-validated (``actions.is_package_id``) before it is
echoed, so the refusal text never carries free-form model or device text.
The destructive guard is a separate check on the ACTUATED node and still runs.
"""

from __future__ import annotations

from typing import Iterable

from tools.mobile.actions import is_package_id

APP_TESTER_PACKAGE = "dev.firebase.appdistribution"


def allowed_packages(target: object, expect_apps: Iterable = ()) -> list[str]:
    """The packages a launch may open, target first, without duplicates.

    A blank or malformed *target* is left out (the App Tester and the declared
    apps stay), so a missing target can never widen the set.
    """
    declared = (expect_apps,) if isinstance(expect_apps, str) else tuple(expect_apps)
    out: list[str] = []
    for package in (target, APP_TESTER_PACKAGE, *declared):
        if is_package_id(package) and package not in out:
            out.append(package)
    return out


def launch_allowed(
    package: object, *, target: object, expect_apps: Iterable = ()
) -> tuple[bool, str]:
    """``(True, "")`` when *package* may be launched, else ``(False, reason)``.

    Callers skip this when the launch names no package (it reopens the run's own
    app). An empty or malformed *package* is refused rather than treated as the
    target, so a dropped field cannot launch something unintended.
    """
    allowed = allowed_packages(target, expect_apps)
    listing = ", ".join(allowed)
    if not is_package_id(package):
        return False, (
            "launch refused: package must be an Android package id such as "
            "com.example.app. Allowed here: " + listing + "."
        )
    if package in allowed:
        return True, ""
    return False, (
        "launch refused: " + str(package) + " is not an app this step may open. "
        "Allowed: " + listing + ". List it in `expect_apps` when the step really "
        "moves into it."
    )
