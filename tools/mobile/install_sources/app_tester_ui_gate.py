"""App Tester UI: which build a request wants, and what an install taught."""

from __future__ import annotations

from typing import Optional

from tools.mobile.app_info import AppVersion
from tools.mobile.install_source import ALREADY_LATEST, InstallOutcome, InstallRequest
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import ACTION_LABELS, _Run


def requested_code(request: InstallRequest, code: Optional[int]) -> Optional[int]:
    """The build asked for: the target code, else the chosen release's code, else
    (``latest``) the newest build listed while this very copy was installed."""
    if request.target_version_code is not None:
        return request.target_version_code
    if request.release is not None and request.release.version_code:
        return request.release.version_code
    if request.latest and code:
        at = memory.Installed(request.serial, request.package, code)
        return memory.known_latest(at)
    return None


def remember_landed(run: _Run, after: Optional[AppVersion]) -> None:
    """The copy just installed through ``latest`` is the newest build listed."""
    code = after.version_code if after else None
    if run.request.latest and code:
        req = run.request
        memory.remember_latest(memory.Installed(req.serial, req.package, code), code)


def card_proves_current(
    run: _Run, releases: list, texts=()
) -> Optional[InstallOutcome]:
    """No target asked for, a build installed, and the page lists nothing newer:
    the install is current, so tapping Update would only reinstall it."""
    req = run.request
    have = run.before.version_code if run.before else None
    if any(label in texts for label in ACTION_LABELS):
        return None  # an Update/Install button is on the page: not proven current
    if req.target_version_code is not None or req.release is not None or not have:
        return None
    codes = [rel.version_code for rel in releases if rel.version_code]
    if not codes or max(codes) > have:
        return None
    message = (
        "Already at versionCode " + str(have) + "; App Tester lists nothing newer."
    )
    return InstallOutcome(True, ALREADY_LATEST, message + " Nothing to install.", have)
