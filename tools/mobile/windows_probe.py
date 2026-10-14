"""Collect this machine's platform facts so a tester can paste them back.

WHY A SCRIPT AND NOT MORE PROSE. ``docs/MOBILE_TESTING.md``'s Windows acceptance
checklist has sixteen rows and not one has ever been executed. Its rows are two
different kinds of thing, and only one kind can be automated honestly:

* **HOST FACTS** -- which SDK was found and where, which suffix each tool
  resolved to, whether the hypervisor feature reads Enabled, whether the cache is
  the user's own, how much space is free, and whether the file lock actually
  works. These need no chat, no emulator and no judgement. A tester runs one
  command and pastes one block back. **That is this module.** Leaving them as
  prose is strictly worse: it asks a non-technical tester to TRANSCRIBE facts,
  which is where "looked fine to me" rows come from, and it is why the ask has
  never been taken up -- sixteen manual rows is too big a request.
* **CONVERSATION** -- pasting a case table into a chat, completing a run, taking
  a run over from a second chat, reading the report. Those need a tester, an MCP
  client and a booted emulator. A script that claimed to check them would be the
  same dishonesty this module exists to reduce, so they STAY prose, and the doc
  now says which rows are covered here so a tester knows what is left for them.

THE ONE THING HERE THAT NO FAKED TEST CAN REACH is the lock round trip.
``locks._lock_fd`` picks its primitive by IMPORT SUCCESS (``fcntl``, else
``msvcrt``), not by ``sys.platform``, so monkeypatching the platform on macOS
still exercises ``fcntl`` and the ``msvcrt`` branch has never executed anywhere
at all. This probe takes and releases the real lock, so a single paste-back from
one Windows machine settles it.

SAFETY, stated rather than assumed. Nothing here downloads anything, spawns an
emulator, installs anything, or touches a device. It needs no kill-switch because
it performs none of the effects the flag governs: the only child process is the
PowerShell feature query, through ``platform_info.virtualization()``'s existing
timeout-bounded, console-hidden seam, and the only file written is the lock file
the lane creates anyway. Every function returns data or a string; nothing raises
to the caller.

OUTPUT GOES TO ``sys.stdout.write``, not ``print`` (house rule: no bare
``print``) and not ``logging``: this is a CLI whose stdout IS its product, which
is the same call ``tools/mobile/report_selfcheck.py`` makes.

    python -m tools.mobile.windows_probe
"""

from __future__ import annotations

import logging
import platform
import sys

from tools.mobile import paths, platform_info, sdk_locator
from tools.mobile.windows_probe_lock import PROBE_OWNER, _lock_round_trip  # noqa: F401

logger = logging.getLogger(__name__)

#: The fact keys, in report order. :func:`render` walks THIS rather than the
#: dict, so a fact that stops being rendered fails a test instead of quietly
#: dropping out of a tester's paste-back.
FACT_KEYS: tuple[str, ...] = (
    "support",
    "python",
    "host",
    "virtualization",
    "suffixes",
    "sdk",
    "java",
    "studio",
    "cache",
    "lock",
)


def _python_facts() -> dict:
    return {
        "version": sys.version.split()[0],
        "sys.platform": platform_info.raw_platform(),
        "platform.machine": platform.machine(),
    }


def _suffix_facts() -> dict:
    return {
        "adb": platform_info.exe("adb"),
        "emulator": platform_info.exe("emulator"),
        "sdkmanager": platform_info.script("sdkmanager"),
        "avdmanager": platform_info.script("avdmanager"),
    }


def _cache_facts() -> dict:
    return {
        "root": str(paths.cache_root()),
        "free_bytes": paths.free_bytes(),
        "ownership": (paths.ownership() or {}).get("content") or {},
    }


def _fill_host(out: dict, host: dict) -> None:
    out["support"] = platform_info.support_statement(
        str(host.get("os") or platform_info.WINDOWS)
    )
    out["python"] = _python_facts()
    out["host"] = host
    out["virtualization"] = (platform_info.virtualization() or {}).get("content") or {}


def _fill_tools(out: dict, located: dict, java: dict) -> None:
    out["suffixes"] = _suffix_facts()
    out["sdk"] = located
    out["java"] = java
    out["studio"] = sdk_locator.studio_present()


def facts() -> dict:
    """Every host fact this probe can establish without a device. Never raises."""
    out: dict = {}
    try:
        host = (platform_info.host_info() or {}).get("content") or {}
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        java = (sdk_locator.locate_java() or {}).get("content") or {}
        _fill_host(out, host)
        _fill_tools(out, located, java)
        out["cache"] = _cache_facts()
        out["lock"] = _lock_round_trip()
    except Exception as exc:  # pragma: no cover - every callee is non-raising
        logger.exception("mobile.windows_probe.facts failed")
        out["error"] = str(exc)[:300]
    return out


def render(collected: dict) -> str:
    """The paste-back block. Walks :data:`FACT_KEYS`, not the dict."""
    body = dict(collected or {})
    lines: list[str] = ["qa-agents mobile lane -- host probe", ""]
    for key in FACT_KEYS:
        lines.append(key + ": " + repr(body.get(key, "(not collected)")))
    if body.get("error"):
        lines.append("error: " + str(body["error"]))
    lines.append("")
    lines.append(
        "Paste the whole block above into the issue. The conversational rows of "
        "the acceptance checklist in docs/MOBILE_TESTING.md still need a real "
        "chat and a booted emulator; this covers only the host facts."
    )
    return "\n".join(lines) + "\n"


def main(argv: list | None = None) -> int:
    """Write the paste-back block. ``0`` always: a fact is not a pass or a fail.

    Deliberately not an exit CODE per fact. A tester is being asked to report
    what their machine says, and a non-zero exit would invite them to read a
    refusal into a normal answer -- "no SDK found" is a fact this probe exists to
    collect, not an error.
    """
    del argv
    sys.stdout.write(render(facts()))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
