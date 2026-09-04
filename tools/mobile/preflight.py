"""Nothing runs until every gate is green -- and the tester is told about ALL of them.

The single most important property of this module is that it does **not**
short-circuit. A tester whose emulator is not booted AND whose app is not
installed AND whose IME is not selected must learn all three from one call:
reporting them one per round trip turns a two-minute setup into three
conversations, and it is the reason the reference project's preflight exists at
all. Every check therefore runs in its own ``try`` and appends its own record,
and the summary ``ok`` is computed from the collected list afterwards.

Each record is ``{name, ok, detail, fix}``. ``fix`` is the sentence a
non-technical tester can act on; a check that cannot say how to fix itself is
not worth failing on.
"""

from __future__ import annotations

import logging

from config.settings import settings
from tools.device_manager import valid_package_name
from tools.mobile import (
    adb,
    downloader,
    emulator,
    ime,
    paths,
    platform_info,
    provisioner,
)

logger = logging.getLogger(__name__)

#: Check names, in report order. Also the set the tests assert against, so a
#: check that silently stops being emitted fails the suite.
CHECK_NAMES: tuple[str, ...] = (
    "virtualization",
    "adb_responds",
    "adb_first_on_path",
    "emulator_booted",
    "package_installed",
    "ime_pinned",
    "ime_installed",
    "ime_selected",
    "ime_oracle",
    "free_disk",
)

#: Free space the lane wants available before a RUN (not a provision): room for
#: dumps, checkpoints and a report.
RUN_FREE_BYTES = 2 * 1024 * 1024 * 1024

_FLAG_FIX = (
    "Add `QA_MOBILE_RUN_ENABLED=true` to `.env` and restart the MCP server "
    "(quit and reopen the editor)."
)


def _record(name: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {"name": str(name), "ok": bool(ok), "detail": str(detail), "fix": str(fix)}


async def check(target_package: str = "", serial: str = "") -> dict:
    """Run EVERY check and return all of them.

    ``{"error", "content": {"ok", "serial", "checks": [...], "failing": [...]}}``.
    ``error`` is reserved for this function itself failing; a failed CHECK is a
    normal result with ``ok=False``, because a refusal a tester can act on is
    not an error condition.
    """
    checks: list[dict] = []
    resolved_serial = str(serial or "")
    try:
        # 1. virtualization -----------------------------------------------
        try:
            virt = (platform_info.virtualization() or {}).get("content") or {}
            checks.append(
                _record(
                    "virtualization",
                    bool(virt.get("ok")),
                    str(virt.get("detail") or ""),
                    str(virt.get("fix") or ""),
                )
            )
        except Exception as exc:
            checks.append(_record("virtualization", False, "check failed: " + str(exc)))

        # 2. adb responds --------------------------------------------------
        serials: list[str] = []
        try:
            listed = await adb.devices()
            if listed.get("error"):
                checks.append(
                    _record(
                        "adb_responds",
                        False,
                        str(listed["error"]),
                        "Install Android Studio, or run the mobile provisioner, "
                        "then try again.",
                    )
                )
            else:
                serials = list(listed.get("content") or [])
                checks.append(
                    _record(
                        "adb_responds",
                        True,
                        "adb reports " + str(len(serials)) + " device(s)",
                    )
                )
        except Exception as exc:
            checks.append(_record("adb_responds", False, "check failed: " + str(exc)))

        # 3. adb first on PATH ---------------------------------------------
        try:
            path_state = (emulator.ensure_adb_first_on_path() or {}).get(
                "content"
            ) or {}
            shadowed = str(path_state.get("shadowed_by") or "")
            checks.append(
                _record(
                    "adb_first_on_path",
                    True,
                    (
                        "using "
                        + str(path_state.get("adb") or "adb")
                        + (
                            "; a different adb (" + shadowed + ") was earlier on "
                            "PATH and has been moved behind it for this process"
                            if shadowed
                            else ""
                        )
                    ),
                    (
                        "Two adb versions on one machine fight over the adb "
                        "server. Consider removing " + shadowed + " from PATH."
                        if shadowed
                        else ""
                    ),
                )
            )
        except Exception as exc:
            checks.append(
                _record("adb_first_on_path", False, "check failed: " + str(exc))
            )

        # 4. emulator booted -----------------------------------------------
        try:
            if not resolved_serial:
                running = (await emulator.find_running(provisioner.AVD_NAME)).get(
                    "content"
                ) or {}
                resolved_serial = str(running.get("serial") or "")
            if not resolved_serial and serials:
                resolved_serial = serials[0]
            if not resolved_serial:
                checks.append(
                    _record(
                        "emulator_booted",
                        False,
                        "no running emulator or device was found",
                        "Start the emulator from the mobile lane (or from "
                        "Android Studio) and try again.",
                    )
                )
            else:
                prop = await adb.getprop(resolved_serial, emulator.BOOT_PROP)
                booted = (
                    not prop.get("error")
                    and str(prop.get("content") or "").strip() == "1"
                )
                checks.append(
                    _record(
                        "emulator_booted",
                        booted,
                        resolved_serial
                        + ": "
                        + emulator.BOOT_PROP
                        + "="
                        + str(prop.get("content") or prop.get("error") or "?"),
                        (
                            ""
                            if booted
                            else "The device is visible but still booting. Wait "
                            "for the launcher to appear, then retry."
                        ),
                    )
                )
        except Exception as exc:
            checks.append(
                _record("emulator_booted", False, "check failed: " + str(exc))
            )

        # 5. target package installed --------------------------------------
        try:
            package = str(target_package or "").strip()
            if not package:
                checks.append(
                    _record(
                        "package_installed",
                        False,
                        "no target app package was given",
                        "Tell the mobile lane which app to test: a local APK "
                        "path, a download URL, a Play Store listing, or the "
                        "package name of an app already on the emulator.",
                    )
                )
            elif not valid_package_name(package):
                checks.append(
                    _record(
                        "package_installed",
                        False,
                        repr(package[:60]) + " is not a valid Android package name",
                        "Use the app's package id, for example com.example.app.",
                    )
                )
            elif not resolved_serial:
                checks.append(
                    _record(
                        "package_installed",
                        False,
                        "cannot check " + package + " without a device",
                        "Start the emulator first.",
                    )
                )
            else:
                installed = await adb.installed_packages(resolved_serial)
                present = package in (installed.get("content") or [])
                checks.append(
                    _record(
                        "package_installed",
                        present,
                        package + (" is installed" if present else " is NOT installed"),
                        (
                            ""
                            if present
                            else "Install the app first: give the mobile lane an "
                            "APK path or a download URL, or open its Play Store "
                            "listing on the emulator."
                        ),
                    )
                )
        except Exception as exc:
            checks.append(
                _record("package_installed", False, "check failed: " + str(exc))
            )

        # 6. IME pinned (Phase 0 Part B) -----------------------------------
        pinned = False
        try:
            status = (ime.manifest_status() or {}).get("content") or {}
            pinned = bool(status.get("ok"))
            checks.append(
                _record(
                    "ime_pinned",
                    pinned,
                    str(status.get("detail") or ""),
                    str(status.get("fix") or ""),
                )
            )
        except Exception as exc:
            checks.append(_record("ime_pinned", False, "check failed: " + str(exc)))

        # 7/8/9. IME installed, selected, oracle ---------------------------
        # Each is reported even when 6 failed, with the SAME reason, because a
        # tester reading "1 of 10 failed" and a tester reading "4 of 10 failed,
        # all for one missing release" take the same action -- and hiding three
        # of them would make the count a lie.
        for name in ("ime_installed", "ime_selected", "ime_oracle"):
            if not pinned:
                checks.append(
                    _record(
                        name,
                        False,
                        "not checked: " + ime.NOT_PINNED_DETAIL,
                        ime.NOT_PINNED_FIX,
                    )
                )
        if pinned:
            manifest = (ime.manifest() or {}).get("content") or {}
            ime_id = str(manifest.get("ime_id") or "")
            try:
                if not resolved_serial:
                    checks.append(
                        _record(
                            "ime_installed",
                            False,
                            "cannot check without a device",
                            "Start the emulator first.",
                        )
                    )
                else:
                    present = (await ime.installed(resolved_serial)) or {}
                    ok = bool((present.get("content") or {}).get("installed"))
                    checks.append(
                        _record(
                            "ime_installed",
                            ok,
                            str(manifest.get("package"))
                            + (" is installed" if ok else " is NOT installed"),
                            (
                                ""
                                if ok
                                else "The mobile lane installs the QA keyboard "
                                "itself on the next run with apply=true."
                            ),
                        )
                    )
            except Exception as exc:
                checks.append(
                    _record("ime_installed", False, "check failed: " + str(exc))
                )
            try:
                if not resolved_serial:
                    checks.append(
                        _record("ime_selected", False, "cannot check without a device")
                    )
                else:
                    current = await ime.current_ime(resolved_serial)
                    active = str(current.get("content") or "")
                    # By component identity: Android reports `pkg/.Class` and
                    # the manifest pins `pkg/pkg.Class`. `==` refused every run
                    # on a correctly configured device (2026-09-04, live).
                    ok = ime.same_component(active, ime_id)
                    checks.append(
                        _record(
                            "ime_selected",
                            ok,
                            "active input method: " + (active or "(none)"),
                            (
                                ""
                                if ok
                                else "The mobile lane selects the QA keyboard "
                                "itself, and restores yours when the run ends."
                            ),
                        )
                    )
            except Exception as exc:
                checks.append(
                    _record("ime_selected", False, "check failed: " + str(exc))
                )
            try:
                if not resolved_serial:
                    checks.append(
                        _record("ime_oracle", False, "cannot check without a device")
                    )
                else:
                    probed = await ime.probe(resolved_serial)
                    if probed.get("error"):
                        checks.append(
                            _record(
                                "ime_oracle",
                                False,
                                str(probed["error"]),
                                "Reinstall the QA keyboard and select it.",
                            )
                        )
                    else:
                        body = probed["content"] or {}
                        checks.append(
                            _record(
                                "ime_oracle",
                                bool(body.get("ok")),
                                "probe result=" + str(body.get("result")),
                                (
                                    ""
                                    if body.get("ok")
                                    else "The QA keyboard did not answer a probe. "
                                    "Reinstall it and select it again."
                                ),
                            )
                        )
            except Exception as exc:
                checks.append(_record("ime_oracle", False, "check failed: " + str(exc)))

        # 10. free disk -----------------------------------------------------
        try:
            disk = (downloader.check_disk(RUN_FREE_BYTES, paths.sub("runs")) or {}).get(
                "content"
            ) or {}
            checks.append(
                _record(
                    "free_disk",
                    bool(disk.get("ok")),
                    str(disk.get("detail") or ""),
                    (
                        ""
                        if disk.get("ok")
                        else "Free some space on the volume holding "
                        + str(paths.cache_root())
                        + " and try again."
                    ),
                )
            )
        except Exception as exc:
            checks.append(_record("free_disk", False, "check failed: " + str(exc)))

        failing = [record["name"] for record in checks if not record["ok"]]
        return {
            "error": None,
            "content": {
                "ok": not failing,
                "serial": resolved_serial,
                "checks": checks,
                "failing": failing,
            },
        }
    except Exception as exc:
        logger.exception("mobile.preflight.check failed")
        return {"error": str(exc), "content": None}


def flag_state() -> dict:
    """``{enabled, fix}`` for the lane's kill-switch.

    Separate from :func:`check` on purpose: the flag gates tool REGISTRATION in
    Phase 3, so by the time a check runs it is already true. This exists so
    ``qa-doctor`` and the Phase-3 handler can say the same thing in one place.
    """
    enabled = bool(settings.qa_mobile_run_enabled)
    return {
        "error": None,
        "content": {"enabled": enabled, "fix": "" if enabled else _FLAG_FIX},
    }


def render(content: dict) -> str:
    """The checks as tester-facing markdown, failures first with their fixes."""
    lines: list[str] = []
    body = dict(content or {})
    ordered = sorted(
        body.get("checks") or [], key=lambda record: (bool(record.get("ok")),)
    )
    for record in ordered:
        mark = "✅" if record.get("ok") else "❌"
        lines.append(
            mark + " **" + str(record.get("name")) + "** — " + str(record.get("detail"))
        )
        if not record.get("ok") and record.get("fix"):
            lines.append("   ↳ " + str(record["fix"]))
    return "\n".join(lines)
