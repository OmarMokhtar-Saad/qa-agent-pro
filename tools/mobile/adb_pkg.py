"""Package queries and data wipe: ``installed_packages``, ``resolve_launcher``, ``clear_app_data``."""

from __future__ import annotations

from tools.device_ids import (
    SYSTEM_PACKAGE_PREFIXES,
    parse_pm_packages,
    valid_package_name,
)
from tools.mobile.adb_consts_dev import _COMPONENT_RE, PM_LIST_TIMEOUT_S
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import rc_failure


# A NON-ZERO EXIT IS A FAILED PROBE, NOT AN EMPTY DEVICE. That is the rule
# ``devices`` above already states, from the same evidence and for the same
# reason: ``shell`` does not inspect the exit code, so a command that RAN and
# failed (device offline, no permission, a dead adb server) arrives here with
# ``error=None`` and no stdout. Returning ``[]`` for it told every caller the
# device ANSWERED and the app is simply not there, and
# ``session.install_state`` printed that as "`pkg` is not installed on the
# emulator" (2026-09-08).
#
# The rc is surfaced HERE rather than guessed at by each caller. An empty
# list is a plausible-looking answer, so a caller-side heuristic ("a real
# device lists hundreds of packages") would be a second, weaker derivation of
# a fact this module already holds exactly -- one question with two answers,
# which is how mirrored conditions drift.
async def installed_packages(serial: str) -> dict:
    """Every package id ``pm list packages`` reports."""
    result = await host().shell(
        serial, ["pm", "list", "packages"], timeout=PM_LIST_TIMEOUT_S
    )
    if result.get("error"):
        return result
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        return rc_failure("adb shell pm list packages", body, rc)
    return {
        "error": None,
        "content": sorted(set(parse_pm_packages(str(body.get("out") or "")))),
    }


def _resolve_argv(package: str) -> list[str]:
    return [
        "cmd",
        "package",
        "resolve-activity",
        "--brief",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
        str(package),
    ]


async def resolve_launcher(serial: str, package: str) -> str:
    """The launcher component of *package* as ``pkg/class``, or ``""``.

    Read from ``cmd package resolve-activity --brief``, whose last line is the
    component. A component that does not belong to *package* is discarded: the
    answer decides what ``am start -n`` is given, and it comes off the device.
    """
    if not valid_package_name(package):
        return ""
    result = await host().shell(serial, _resolve_argv(package))
    body = result.get("content") or {}
    if result.get("error") or int(body.get("rc") or 0) != 0:
        return ""
    lines = [line.strip() for line in str(body.get("out") or "").splitlines()]
    last = lines[-1] if lines else ""
    match = _COMPONENT_RE.match(last)
    if not match or match.group(1) != str(package):
        return ""
    return last


def _clear_failed(combined: str) -> dict:
    return {"error": "adb pm clear failed: " + combined.strip()[:400], "content": None}


# Whitelisted name only. Every caller of this function passes the RUN's own
# ``ctx.package``, never a value read off a script action, so this refusal
# is a second, independent check rather than the only one: a well-formed
# but SYSTEM package name (``com.android.*``, ``com.google.android.*``,
# ``android.*``) is refused too, because looking like a valid package is not
# the same question as being an app this lane may wipe.
async def clear_app_data(serial: str, package: str) -> dict:
    """``pm clear`` *package* -- wipes its data and re-stops it."""
    name = str(package or "")
    if not valid_package_name(name) or name.startswith(SYSTEM_PACKAGE_PREFIXES):
        return {
            "error": "Refusing to clear data for " + repr(name[:60]),
            "content": None,
        }
    result = await host().shell(serial, ["pm", "clear", name])
    if result.get("error"):
        return result
    # `shell` never inspects rc, and `pm clear` can exit 0 while printing
    # "Failed". Only its literal "Success" means the data is gone.
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    out = str(body.get("out") or "")
    if rc != 0 or "Success" not in out:
        return _clear_failed(out + str(body.get("err") or ""))
    return result
