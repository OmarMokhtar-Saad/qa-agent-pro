"""The capture loop of ``qa_capture_screens``: screenshots from a device."""

from __future__ import annotations

from typing import Any, NamedTuple

from handlers import _common, _elicit
from handlers import capture_limits as _limits
from tools.device_manager import capture_screenshot


async def _capture_one(
    device: dict, screens: list, progress: _common.ProgressCb
) -> str:
    """Capture one screen onto *screens*; the error text, or "" on success."""
    label = device.get("name") or device.get("id")
    await _common.emit(
        progress, f"📸 Capturing screen {len(screens) + 1} from {label}…"
    )
    shot = await capture_screenshot(device)
    if shot.get("error") or not shot.get("content"):
        return shot.get("error") or "no image returned"
    screens.append(
        {
            "filename": f"screen_{len(screens) + 1}.png",
            "mime": "image/png",
            "data": shot["content"],
        }
    )
    return ""


# qa_capture_screens asked for an EXPLICIT number of screens when *count* is
# set, so the capture-another question is not asked at all; the round bound is
# the only limit. count=0 (every pre-existing caller) is byte-identical to before.
async def _wants_more(count: int, choose: _common.ChooseCb, shown: int) -> bool:
    """Whether to take another screen after *shown* have been captured."""
    if count:
        return True
    if choose is None:
        return False
    return await _fa_capture_another(choose, shown)


# Mirrors app.py's capture-another loop over MCP elicitation: after each
# capture the tester picks "Capture another screen" or "Generate the report",
# bounded by _MAX_ELICIT_ROUNDS. Without elicitation exactly one screen is
# captured. *screens* items match tools.image_description.describe_images
# input ({filename, mime, data}). An error on the FIRST capture is returned;
# a later failure just stops the loop with the screens gathered so far.
class _ShotLoop(NamedTuple):
    """One capture run: the device, how many screens, and its callbacks."""

    device: dict
    n_screens: int = 0
    choose: _common.ChooseCb = None
    progress: _common.ProgressCb = None


async def fa_capture_screens(run: _ShotLoop) -> tuple:
    """Capture 1..N screenshots from *device*; returns (screens, error)."""
    device, count, choose, progress = run
    screens: list = []
    rounds = 0
    bound = _fa_capture_bound(count)
    while rounds < bound:
        rounds += 1
        error = await _capture_one(device, screens, progress)
        if error:
            return screens, error
        if not await _wants_more(count, choose, len(screens)):
            break
    return screens, ""


def _capture_want(count: Any) -> tuple:
    """``(want, clamp_note)``: the screen count, clamped to _CAPTURE_COUNT_MAX.

    A clamped request is DISCLOSED in *clamp_note*; a bad count means 1."""
    try:
        want_raw = max(1, int(count or 1))
    except (TypeError, ValueError, OverflowError):
        want_raw = 1
    want = min(want_raw, _limits._CAPTURE_COUNT_MAX)
    if want_raw <= want:
        return want, ""
    return want, (
        f"\n\n> ℹ️ You asked for {want_raw} screens; {want} is the "
        "per-call maximum. Call `qa_capture_screens` again for more -- "
        "capture_ids from several calls can be passed together."
    )


def _fa_capture_bound(count: int) -> int:
    """Round bound for the capture loop.

    count > 0 (qa_capture_screens) gets its OWN bound. _MAX_ELICIT_ROUNDS caps
    how many DIALOGS to show, and the count-driven path shows none per screen,
    so reusing it would silently turn a request for 8 screens into 5 -- while
    the docstring and the gate's markdown promise "screen after screen".
    """
    if count:
        return max(1, min(int(count), _limits._CAPTURE_COUNT_MAX))
    return _common._MAX_ELICIT_ROUNDS


async def _fa_capture_another(choose: _common.ChooseCb, captured: int) -> bool:
    """Ask the tester whether to capture one more screen."""
    picked = await _elicit.elicit_choice(
        choose,
        f"Captured screen {captured}. Capture another?",
        ["Capture another screen", "Generate the report"],
    )
    return picked.status == _common.CHOSEN and picked.value == "Capture another screen"
