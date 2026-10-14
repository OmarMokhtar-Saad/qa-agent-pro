"""The platform rows of the mobile lane's non-Android refusal."""

from __future__ import annotations


def _non_android_refusal(wanted: str, platform: str) -> str:
    """The reply naming *wanted* as a *platform* device the lane cannot drive."""
    return (
        "## `"
        + wanted[:80]
        + "` is not an Android device\n\nThe mobile emulator lane drives "
        "Android only: it replays uiautomator scripts over `adb`, and this "
        "server has no equivalent for "
        + platform[:20]
        + ". Nothing was started and nothing was installed.\n\nThat device "
        "is still fully supported elsewhere -- `qa_capture_screens` takes "
        "its screenshots and `qa_list_devices` keeps listing it. To use "
        "this lane, pass an Android emulator serial (like `emulator-5554`) "
        "in `serial`, or call `qa_mobile_test` with no `serial` and let it "
        "adopt one or boot one of your AVDs."
    )


def _refusal_for(found: dict | None, wanted: str) -> str:
    """The refusal for *wanted* from the discovery result *found*, else ""."""
    for row in (found or {}).get("content") or []:
        # The loop is OUTSIDE the try, so a row that is not a dict raised
        # AttributeError straight through a function whose whole contract is to
        # fail open. A row this server cannot read names no platform, and only a
        # POSITIVE identification refuses.
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "") != wanted:
            continue
        platform = str(row.get("platform") or "")
        if not platform or platform == "android":
            return ""
        return _non_android_refusal(wanted, platform)
    return ""
