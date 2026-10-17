"""App Tester UI: which build a request wants, and what an install taught."""

from __future__ import annotations

from typing import Optional

from tools.mobile.app_info import AppVersion
from tools.mobile.install_source import InstallRequest
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import _Run


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
