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

import importlib.util
import logging
import platform
import sys

from tools.mobile import locks, paths, platform_info, sdk_locator

logger = logging.getLogger(__name__)

#: The owner label the probe takes the lock under. Distinctive on purpose: if a
#: paste-back ever shows this holding a lock, the probe crashed mid-round-trip
#: and the process that would release it is gone -- which the kernel handles.
PROBE_OWNER = "windows-probe"

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


def _lock_round_trip() -> dict:
    """Take and release the real emulator lock. Reports the PRIMITIVE used.

    The primitive is determined the same way ``locks._lock_fd`` determines it --
    by which module is importable -- rather than by asking ``locks``, so this
    needs no change to the locking module whose invariant is the most
    load-bearing thing in the lane.
    """
    primitive = ""
    for name in ("fcntl", "msvcrt"):
        try:
            if importlib.util.find_spec(name) is not None:
                primitive = name
                break
        except (ImportError, ValueError):  # pragma: no cover - defensive
            continue
    body: dict = {
        "primitive": primitive or "none",
        "acquired": False,
        "released": False,
        "detail": "",
    }
    try:
        taken = locks.acquire(locks.EMULATOR_LOCK, owner=PROBE_OWNER)
        if taken.get("error"):
            body["detail"] = str(taken["error"])[:300]
            return body
        got = taken.get("content") or {}
        body["acquired"] = bool(got.get("acquired"))
        if not body["acquired"]:
            body["detail"] = (
                "the lane's lock is held by "
                + str(got.get("owner") or "another process")
                + " -- close other chats and run this again"
            )
            return body
        given = locks.release(locks.EMULATOR_LOCK, owner=PROBE_OWNER, as_holder=True)
        body["released"] = not given.get("error")
        body["detail"] = str(given.get("error") or "")[:300]
    except Exception as exc:  # pragma: no cover - locks never raises
        logger.warning("mobile.windows_probe: lock round trip failed: %s", exc)
        body["detail"] = str(exc)[:300]
    return body


def facts() -> dict:
    """Every host fact this probe can establish without a device. Never raises."""
    out: dict = {}
    try:
        host = (platform_info.host_info() or {}).get("content") or {}
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        java = (sdk_locator.locate_java() or {}).get("content") or {}
        out["support"] = platform_info.support_statement(
            str(host.get("os") or platform_info.WINDOWS)
        )
        out["python"] = {
            "version": sys.version.split()[0],
            "sys.platform": platform_info.raw_platform(),
            "platform.machine": platform.machine(),
        }
        out["host"] = host
        out["virtualization"] = (platform_info.virtualization() or {}).get(
            "content"
        ) or {}
        out["suffixes"] = {
            "adb": platform_info.exe("adb"),
            "emulator": platform_info.exe("emulator"),
            "sdkmanager": platform_info.script("sdkmanager"),
            "avdmanager": platform_info.script("avdmanager"),
        }
        out["sdk"] = located
        out["java"] = java
        out["studio"] = sdk_locator.studio_present()
        out["cache"] = {
            "root": str(paths.cache_root()),
            "free_bytes": paths.free_bytes(),
            "ownership": (paths.ownership() or {}).get("content") or {},
        }
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
