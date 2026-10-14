"""The Android SDK, adb, per-tool and keyboard rows of the machine report."""

from __future__ import annotations

from typing import Any, cast

from tools.machine_report_core import _PROBE_ERRORS, NO_MOBILE_DETAIL, Row

# The mobile modules are absent from some builds: then the rows read "off".
try:
    import tools.mobile.adb as mobile_adb
except ImportError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    mobile_adb = cast("Any", None)
try:
    import tools.mobile.sdk_locator as sdk_locator
except ImportError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    sdk_locator = cast("Any", None)
try:
    import tools.mobile.ime as ime
except ImportError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    ime = cast("Any", None)

#: The SDK tools that get an ``sdk_tool:<name>`` row. ``adb`` is deliberately
#: NOT here: it has its own row and its own question. Declared ONCE and used by
#: BOTH branches of the SDK block below, so the present-edition and
#: absent-edition replies can never carry different component sets -- a
#: consumer that saw a row in one case and nothing in the other could not tell
#: "this edition has no mobile modules" from "this backend is too old to
#: report it". A test pins this set, plus ``adb``, against
#: ``sdk_locator.TOOL_NAMES``, so a new tool there fails by name until it has a
#: row and a PRODUCERS entry.
SDK_TOOL_ROWS = ("emulator", "sdkmanager", "avdmanager")


def _mobile_sdk_row(located: dict) -> Row:
    root = str(located.get("sdk_root") or "")
    missing = list(located.get("missing") or [])
    return Row(
        "mobile_sdk",
        "ok" if root and not missing else ("warn" if root else "fail"),
        ("Android SDK at " + root if root else "No Android SDK found.")
        + (" Missing: " + ", ".join(missing) if missing else ""),
        ""
        if root and not missing
        else "Android Studio's SDK Manager installs these; the desktop "
        "wizard never writes into the SDK itself.",
        tool_path=root,
    )


def _adb_row(located: dict, resolved_adb: str) -> Row:
    adb_path = str((located.get("tools") or {}).get("adb") or "")
    return Row(
        "adb",
        "ok" if adb_path else "fail",
        "adb at " + resolved_adb if adb_path else "No adb in the located SDK.",
        ""
        if adb_path
        else "Install Platform-Tools from Android Studio's SDK Manager; "
        "neither this server nor a desktop client installs it.",
        tool_path=resolved_adb,
    )


def _sdk_tool_row(tool: str, located: dict) -> Row:
    tool_path = str((located.get("tools") or {}).get(tool) or "")
    return Row(
        "sdk_tool:" + tool,
        "ok" if tool_path else "fail",
        tool + " at " + tool_path
        if tool_path
        else "No " + tool + " in the located SDK.",
        ""
        if tool_path
        else "Install it from Android Studio's SDK Manager; neither "
        "this server nor a desktop client writes into the SDK.",
        tool_path=tool_path,
    )


def _off_rows() -> list[Row]:
    rows = [Row("mobile_sdk", "off", NO_MOBILE_DETAIL)]
    rows.append(Row("adb", "off", NO_MOBILE_DETAIL))
    # The SAME component set as the located branch. A consumer that saw these
    # rows on one machine and nothing on another could not tell an edition
    # without the mobile modules from a backend too old to report them, and
    # would round the second one to "not installed".
    for tool in SDK_TOOL_ROWS:
        rows.append(Row("sdk_tool:" + tool, "off", NO_MOBILE_DETAIL))
    return rows


def sdk_items() -> list[Row]:
    """The ``mobile_sdk``, ``adb`` and ``sdk_tool:*`` rows."""
    rows: list[Row] = []
    if mobile_adb is None or sdk_locator is None:
        return _off_rows()
    try:
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        rows.append(_mobile_sdk_row(located))
        # The one shared resolver the mobile lane itself calls (tools.mobile.
        # adb.resolve_adb / device_manager._adb_binary), so the doctor can
        # never diverge from what a run actually drives -- audit item 6.
        rows.append(_adb_row(located, mobile_adb.resolve_adb()))
        for tool in SDK_TOOL_ROWS:
            rows.append(_sdk_tool_row(tool, located))
    except _PROBE_ERRORS:
        rows.extend(_off_rows())
    return rows


def ime_row() -> Row:
    """The ``mobile_ime`` row."""
    if ime is None:
        return Row("mobile_ime", "off", NO_MOBILE_DETAIL)
    try:
        pinned = (ime.manifest_status() or {}).get("content") or {}
        ok = bool(pinned.get("ok"))
        return Row(
            "mobile_ime",
            "ok" if ok else "warn",
            str(pinned.get("detail") or ("pinned" if ok else "not pinned")),
            "" if ok else str(pinned.get("fix") or ""),
        )
    except _PROBE_ERRORS:
        # `off`, not `undetermined`: on a build with no mobile modules the
        # keyboard is not an open question, it is absent by construction.
        return Row("mobile_ime", "off", NO_MOBILE_DETAIL)
