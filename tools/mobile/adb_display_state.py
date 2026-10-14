"""Display/density constants, TTL caches and ``_cache_lookup`` (moved from adb.py).

The caches are defined ONCE here; the facade re-exports the same dict objects.
"""

from __future__ import annotations

import re

# The digit guards are load-bearing: without them `\d{1,7}` happily matches a
# SEVEN-digit slice of an eight-digit number, so a device reporting a size
# too large to be representable would be read as a plausible one instead of
# refused. Bounded on both sides, an over-long run matches nothing. The
# lookbehind also refuses a leading minus: `-1080x2400` used to match from the
# digit after the sign and be read as a positive 1080 (round-13 review), when a
# negative width is an answer this function must not trust.
_DISPLAY_RE = re.compile(r"(?<![\d-])(\d{1,7})x(\d{1,7})(?!\d)")
#: The largest edge this lane will believe a device reported. The REGEX bounds
#: the digit COUNT so a longer number is refused rather than sliced; this bounds
#: the VALUE, which the regex cannot: `9999999x9999999` is seven digits each and
#: parses as a plausible answer, and the only other check on this path is
#: `width <= 0 or height <= 0` -- a floor with no ceiling.
#:
#: Why a ceiling is needed at all: this number is not decoration. It is read by
#: `case_runner`, `explore_runner` and `executor` as the coordinate space a
#: planned action lives in, and it scales the report. An absurd value does not
#: fail loudly; it silently rescales, which is the same shape as the density
#: multiplier two constants below -- and that one is bounded on both sides for
#: exactly this reason.
#:
#: The constraint that decides the number, not the number itself: it must sit
#: far above any panel this lane can drive (the largest in the fixtures is 3200,
#: and 8K's long edge is 7680) and far below the point where a value stops being
#: a plausible panel at all. Sizes above this are refused, and a refusal here
#: means "no answer", which every consumer already handles -- the dump's own
#: bounds remain the authority for targeting either way.
MAX_DISPLAY_EDGE_PX = 32768
#: How long a display answer is reused before the device is asked again. Not
#: forever: a SERIAL is not a device identity. Emulator serials are recycled, so
#: a phone AVD and a tablet AVD both arrive as `emulator-5554`; a foldable
#: changes size mid-run; `wm size WxH` can be set mid-session. Caching for the
#: life of the process made each of those a stale answer that no later call
#: could correct. The window is short enough that a device swap self-heals and
#: long enough that a burst of actions costs one round trip.
DISPLAY_CACHE_TTL_S = 60
#: Per serial: `(monotonic deadline, size or None)`. A FAILED answer is cached
#: too -- a `wm` hiccup must not cost a round trip on every action -- but only
#: for the same window, so it retries rather than degrading for the whole run.
_DISPLAY_CACHE: dict = {}


#: Plausibility bounds on a density the DEVICE reports. `wm density` is an
#: untrusted number like every other line this module parses, and dp -> px is a
#: MULTIPLIER: an absurd value silently rescales the accessibility floor instead
#: of failing, so it is rejected and the reader falls back to no answer.
MIN_DENSITY_DPI = 40
MAX_DENSITY_DPI = 1000
#: A bare integer on the line. Bounded on both sides for the same reason
#: `_DISPLAY_RE` is: an unbounded `\d+` reads a seven-digit slice of a longer
#: number as a plausible answer instead of refusing it.
_DENSITY_RE = re.compile(r"(?<![\d-])(\d{1,4})(?!\d)")
#: Per serial: `(monotonic deadline, dpi or None)`. Shares
#: `DISPLAY_CACHE_TTL_S` deliberately -- it is the same staleness question about
#: the same device, and a second window would be a second answer to "how long is
#: a device fact good for". A FAILED answer is cached for the same window, so a
#: `wm` hiccup costs one round trip rather than one per screen.
_DENSITY_CACHE: dict = {}


def _cache_lookup(cache: dict, key: str, now: float) -> tuple[bool, object]:
    """``(True, value)`` when *key* holds an unexpired entry, else ``(False, None)``.

    A cached ``None`` is a real answer (a failed probe is cached too), which is
    why the hit is a separate flag.
    """
    cached = cache.get(key)
    if cached is not None and now < cached[0]:
        return True, cached[1]
    return False, None
