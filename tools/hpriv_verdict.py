"""The OS verdicts of the host-privilege probe (POSIX sudo test, Windows token and group)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import NamedTuple

from tools.hpriv_consts import _DENIED_MARKERS, _SUDOER_MARKERS, ADMIN_SID


class Ask(NamedTuple):
    """The facade's seams, handed down per call so a patched name still bites."""

    run: Callable[..., tuple[int, str, str]]
    slice: Callable[[float], float]


# (elevated, can_elevate, undetermined)
_ROOT = (True, True, False)
_SUDO = (False, True, False)
_DENY = (False, False, False)
_UNDET = (False, False, True)


def _verdict(state: tuple[bool, bool, bool], method: str) -> dict:
    """The verdict dict; the key order is the JSON contract."""
    elevated, can_elevate, undetermined = state
    return {
        "elevated": elevated,
        "can_elevate": can_elevate,
        "undetermined": undetermined,
        "method": method,
    }


def budget_spent(method: str) -> dict:
    """Out of time is UNDETERMINED, never a denial -- same rule as everywhere."""
    return _verdict(_UNDET, method)


def _sudo_answer(rc: int, err: str) -> dict:
    """Classify the ``sudo -n true`` outcome (pure)."""
    low = (err or "").lower()
    if rc == 0:
        return _verdict(_SUDO, "sudo -n")
    if any(marker in low for marker in _SUDOER_MARKERS):
        return _verdict(_SUDO, "sudo -n password-required")
    if any(marker in low for marker in _DENIED_MARKERS):
        return _verdict(_DENY, "sudo -n sudoers-denied")
    return _verdict(_UNDET, "sudo -n unreadable")


# POSIX: euid, then a NON-INTERACTIVE sudo test.
#
# Four clauses, each individually decisive, each with its own fixture:
# root; ``sudo -n`` accepted; ``sudo -n`` refused BECAUSE a password is
# needed (the account is a sudoer); ``sudo -n`` refused because the account
# is not a sudoer. Anything else is UNDETERMINED.
def posix_verdict(ask: Ask, deadline: float) -> dict:
    """POSIX verdict: euid, then a non-interactive sudo test."""
    if getattr(os, "geteuid", None) is not None and os.geteuid() == 0:
        return _verdict(_ROOT, "geteuid")
    budget = ask.slice(deadline)
    if budget <= 0:
        return budget_spent("probe budget spent before the sudo check")
    # `-n` is what makes this non-prompting. Removing it turns an advisory
    # probe into a password prompt inside a stdio server.
    rc, _out, err = ask.run(["sudo", "-n", "true"], timeout=budget)
    return _sudo_answer(rc, err)


def _is_in_role_argv() -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "([Security.Principal.WindowsPrincipal]"
        "[Security.Principal.WindowsIdentity]::GetCurrent())"
        ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
    ]


def _in_role(ask: Ask, budget: float) -> str | None:
    """``"true"``/``"false"`` from IsInRole, or None when unreadable."""
    rc, out, _err = ask.run(_is_in_role_argv(), timeout=budget)
    lines = [ln.strip().lower() for ln in (out or "").splitlines() if ln.strip()]
    state = lines[-1] if lines else ""
    if rc != 0 or state not in ("true", "false"):
        return None
    return state


# Windows: IsInRole for the CURRENT token, then Administrators-group SID.
#
# Being in the group and running elevated are different facts, and the
# product needs both: a group member who is not elevated CAN elevate, while a
# non-member cannot, and Group Policy can deny even a member. An unreadable
# probe is UNDETERMINED, never a denial.
#
# TWO sequential subprocesses, and they share ONE deadline: each is started
# with what is left of ``PROBE_BUDGET_S``, so the wait a caller feels is
# bounded by that single number rather than by the sum of the per-call
# timeouts. Out of budget returns UNDETERMINED instead of starting the second
# call.
#
# NO WINDOWS MACHINE HAS RUN THIS. Both branches are graded by fixtures only,
# which is stated rather than implied.
def windows_verdict(ask: Ask, deadline: float) -> dict:
    """Windows verdict: IsInRole for the current token, then the group SID."""
    budget = ask.slice(deadline)
    if budget <= 0:
        return budget_spent("probe budget spent before the IsInRole check")
    state = _in_role(ask, budget)
    if state is None:
        return _verdict(_UNDET, "IsInRole unreadable")
    if state == "true":
        return _verdict(_ROOT, "IsInRole")
    return windows_group(ask, deadline)


def windows_group(ask: Ask, deadline: float) -> dict:
    """Windows second step: Administrators-group SID of a non-elevated token."""
    group_budget = ask.slice(deadline)
    if group_budget <= 0:
        return budget_spent("probe budget spent before the group check")
    grc, gout, _gerr = ask.run(["whoami", "/groups"], timeout=group_budget)
    if grc != 0:
        return _verdict(_UNDET, "group membership unreadable")
    member = ADMIN_SID in (gout or "")
    method = "Administrators SID " + ("present" if member else "absent")
    return _verdict((False, member, False), method)
