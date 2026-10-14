"""Virtualization verdicts and the host-content builder of platform_info (split out).

Pure: the facade runs the probes (``_run_sync``) and passes the replies in; the
fix texts arrive as callables so a failed or negative verdict is the only path
that ever asks for them.
"""

from __future__ import annotations

from collections.abc import Callable

from tools.mobile.platform_info_table import (
    ARM64_ABI,
    SUPPORT,
    WHPX_FEATURE,
    WINDOWS,
    X86_64_ABI,
)

Reply = tuple[int, str, str]


def _detail(err: str, rc: int) -> str:
    return (err.strip() or "rc=" + str(rc))[:200]


def macos_verdict(reply: Reply, fix: Callable[[], str]) -> dict:
    """The ``kern.hv_support`` verdict for a macOS host."""
    rc, out, err = reply
    if rc != 0:
        return {
            "ok": False,
            "detail": "could not query kern.hv_support (" + _detail(err, rc) + ")",
            "fix": fix(),
        }
    value = out.strip()
    ok = value == "1"
    return {
        "ok": ok,
        "detail": "kern.hv_support=" + (value or "?"),
        "fix": "" if ok else fix(),
    }


def _windows_failed(err: str, rc: int, fix: Callable[[], str]) -> dict:
    return {
        "ok": False,
        # A failed query is NOT a verdict: Get-WindowsOptionalFeature needs an
        # elevated shell. preflight treats this row as advisory (R1a).
        "undetermined": True,
        "detail": (
            "could not query the "
            + WHPX_FEATURE
            + " feature ("
            + _detail(err, rc)
            + "). Get-WindowsOptionalFeature itself needs an elevated "
            "shell, so this is NOT proof the feature is off."
        ),
        "fix": fix(),
    }


def windows_verdict(reply: Reply, fix: Callable[[], str]) -> dict:
    """The WHPX feature-state verdict for a Windows host."""
    rc, out, err = reply
    if rc != 0:
        return _windows_failed(err, rc, fix)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    state = lines[-1] if lines else ""
    ok = state.lower() == "enabled"
    return {
        "ok": ok,
        "detail": WHPX_FEATURE + " state=" + (state or "?"),
        "fix": "" if ok else fix(),
    }


def _refusal(os_name: str, arch: str, plat: str) -> str:
    """Why this host cannot run the emulator, or ``""``."""
    # The ARCH clause stays an EXPLICIT refusal rather than becoming
    # `arch in SUPPORT[os_name]["arches"]`. A table-driven arch test would
    # newly refuse a host whose `normalize_arch` answered "unknown", which
    # proceeds today -- a behaviour change with no Windows evidence behind
    # it. OS membership is derived (below); the arch decision is not.
    if os_name == WINDOWS and arch == "arm64":
        return (
            "Windows on ARM is not supported by the mobile lane: Google "
            "publishes no Android emulator build for it, so there is "
            "nothing to download. Use an x86_64 Windows machine or a Mac."
        )
    if os_name not in SUPPORT:
        return (
            "The mobile lane supports macOS and native Windows only; this "
            "host reports sys.platform=" + str(plat) + "."
        )
    return ""


def host_content(plat: str, os_name: str, arch: str) -> dict:
    """The ``content`` of ``host_info``: ``{os, arch, image_abi, emulator_ok, reason}``."""
    reason = _refusal(os_name, arch, plat)
    return {
        "os": os_name,
        "arch": arch,
        "image_abi": ARM64_ABI if arch == "arm64" else X86_64_ABI,
        "emulator_ok": not reason,
        "reason": reason,
    }
