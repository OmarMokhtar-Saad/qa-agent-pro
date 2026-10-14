"""The two pixel bounds of the screenshot lane (long edge sent, pixels decoded)."""

from __future__ import annotations

#: The long edge, in pixels, the attached PNG is scaled down to.
#:
#: BOUNDED IN BOTH DIRECTIONS, and neither bound is a preference.
#:
#: DOWNWARD -- LEGIBILITY. A vision model is handed this image as the only
#: description of a screen whose app draws its own UI, so the smallest caption
#: on it must still be readable. The real 1440x3120 Arabic chat screen of
#: 2026-09-08 was rendered and READ at 1568/1024/768/640: every string on it,
#: including the small grey caption, is legible at 768, and that caption
#: degrades at 640. 768 is an OBSERVED floor rather than a round number, and
#: ``tests/mobile/test_mobile_bounds_upper.py``'s
#: ``test_the_screenshot_long_edge_floor_still_holds`` is what stops it being
#: lowered by someone chasing the tokens below.
#:
#: UPWARD -- TOKENS. A vision model downscales before it tokenizes, so this
#: image's cost is a function of these dimensions ALONE and not of the file
#: size: measured 1513 tokens at a 1568 long edge against 363 at 768. Above
#: roughly 1024 the reduction this constant exists for is gone, which is why
#: its CEILINGS row is a binding ceiling and not an inert one.
#:
#: It cannot affect TARGETING. Every coordinate the lane plans comes from the
#: dump's ``bounds``, never from a pixel -- which :data:`ATTACHED_NOTE` already
#: promises the model in words -- so resolution is a perception input only.
MAX_LONG_EDGE_PX = 768

#: PIXELS this process will DECODE. A different axis from every other cap here,
#: and the ONLY bound anything on it has -- there is nothing upstream. A device
#: reports its display size through ``adb._DISPLAY_RE``, which admits SEVEN
#: digits per dimension by design, so ``9999999x9999999`` parses as a plausible
#: answer: 10^14 pixels. A reader who assumes this is defence in depth and
#: relaxes it is removing the whole defence, not a second layer.
#:
#: THE CONSTRAINT, not the number. The largest screen this lane can capture is
#: 1440x3200 = 4.6M pixels (the real Galaxy S21 it is validated against is
#: 1440x3120 = 4.5M), so this sits FIVE TIMES above anything genuine and cannot
#: refuse a real capture -- which would be worse than the defect it prevents.
#: At the measured ~8.6 bytes per pixel it caps peak decode near 200MB, which is
#: the real constraint: this runs inside the MCP server process, on every turn.
#: And it is seven times BELOW the 169M-pixel cost point, i.e. in the middle of
#: the exposed band rather than at its top -- Pillow's own decompression-bomb
#: error already fires around 178M, so a bound set there would protect nothing
#: that was not already protected.
#:
#: REASONING FROM THE WIDEST SCREEN GIVES THE WRONG ANSWER: 2560x1600 is FEWER
#: pixels than 1440x3200. Aspect ratio moves pixels around rather than adding
#: them, so the tallest phone, not the widest tablet, is the shape that bounds
#: this. Bounded from above in ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_DECODE_PIXELS = 24_000_000
