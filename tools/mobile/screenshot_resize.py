"""Scaling a screen PNG (or a RAW frame) down to the long-edge bound."""

from __future__ import annotations

import io
import logging
import struct
from typing import Any, cast

from config.settings_guard import never_raise
from tools.mobile.screenshot_limits import MAX_DECODE_PIXELS, MAX_LONG_EDGE_PX

try:
    import PIL.Image as _pil_image
except ImportError as _exc:
    if _exc.name not in ("PIL", "PIL.Image"):
        raise
    _pil_image = cast("Any", None)

logger = logging.getLogger("tools.mobile.screenshot")


# Why `_resized` behaves as it does (kept here so the function stays short).
#
# NEVER RAISES, never returns nothing: the INPUT comes back unchanged whenever
# this cannot do better -- Pillow absent, bytes that are not a decodable image,
# an image already inside the bound, or any failure at all. A full-size picture
# is worth strictly more than no picture, and the job is bytes, not verdicts.
#
# TOO MANY PIXELS TO DECODE is one of those "cannot do better" cases, refused
# BEFORE ``load()`` rather than after: the size is in the header, the cost is
# in the decode, and the whole point is not to pay it. See MAX_DECODE_PIXELS.
#
# RESAMPLING IS LANCZOS ONLY FOR CONTINUOUS-TONE MODES. Measured on Pillow
# 12.3.0: a ``P`` (palette) or ``1`` (bilevel) image resized with
# ``Image.LANCZOS`` comes back byte-identical to the same resize with
# ``Image.NEAREST`` -- Pillow downgrades silently and raises nothing. That
# matters because the legibility case for a 768 long edge ASSUMES LANCZOS ran;
# where it did not, the floor's argument does not hold. It is documented rather
# than fixed because ``adb.screencap -p`` does not produce those modes, and a
# conversion would be an unreachable branch -- which this module already
# refuses to add, for the reason stated at the end of `_resized`.
#
# STILL PNG. JPEG would buy transport bytes we are not short of -- the model's
# cost depends on the dimensions, not on the file size -- and its ringing lands
# on exactly the canvas-drawn screens where the picture is the only
# description of the screen there is.
#
# THE BYTES CAN GROW, and that is accepted rather than guarded. What this buys
# is TOKENS, which are a function of the dimensions alone, so a 354x768
# attachment costs the same whatever it weighs. LANCZOS resampling of a fine
# repeating pattern -- a transparency checkerboard, a QR code, a dense chart --
# rings, turning a cheap two-colour image into continuous tone that PNG cannot
# pack: measured at +233% on an 8px checkerboard and +64% on this module's own
# test fixture, against -58% to -95% on ordinary app screens. Returning the
# original whenever it is the smaller of the two would be the wrong trade by a
# factor of four: 1513 tokens against 363. The growth is bounded and cannot
# reach the transport: the worst case at this long edge is about 3.6MB against
# ``adb.MAX_SCREENSHOT_BYTES`` of 8MB, and an input capable of producing it was
# already refused at that cap.
def _downscaled_png(image: Any, pil_image: Any) -> bytes | None:
    """PNG bytes of the open *image* shrunk to the long-edge bound, or None."""
    # ``None`` means "send the original": too many pixels to decode, or already
    # inside the bound.
    if _too_big(image):
        return None
    image.load()
    longest = max(image.size)
    if longest <= MAX_LONG_EDGE_PX:
        # Already inside the bound. Returned UNTOUCHED rather than re-encoded: a
        # round trip through the encoder cannot make this picture cheaper and
        # can only lose pixels off it.
        return None
    return _shrunk(image, pil_image, longest)


def _too_big(image: Any) -> bool:
    """True (after a warning) when the open *image* has too many pixels to decode."""
    # SIZE BEFORE LOAD. `Image.open` reads only the header, so the dimensions
    # are known before a single pixel is decoded -- which is the one moment this
    # can be refused cheaply. `load()` below is the decode, and its cost is PIXELS.
    width, height = image.size
    if width * height > MAX_DECODE_PIXELS:
        logger.warning(
            "mobile.screenshot: %dx%d is above MAX_DECODE_PIXELS, so "
            "this screen is attached at full size rather than decoded.",
            width,
            height,
        )
        return True
    return False


def _shrunk(image: Any, pil_image: Any, longest: int) -> bytes:
    """PNG bytes of *image* scaled so its long edge is MAX_LONG_EDGE_PX."""
    scale = MAX_LONG_EDGE_PX / float(longest)
    size = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    smaller = image.resize(size, pil_image.LANCZOS)
    buffer = io.BytesIO()
    smaller.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _pillow() -> Any:
    """The ``PIL.Image`` module, or None when Pillow is not installed."""
    if _pil_image is None:
        # An install that predates the dependency. Say it once, in the log, to
        # the operator -- the packet's note is about whether a picture exists.
        logger.warning(
            "mobile.screenshot: Pillow is not installed, so this screen's PNG "
            "is attached at full size. Reinstall to pick up the dependency."
        )
        return None
    return _pil_image


# The input comes back unchanged whenever this cannot do better; see the
# comment block above `_downscaled_png`.
def _resized(data: bytes) -> bytes:
    """*data* scaled so its long edge is at most :data:`MAX_LONG_EDGE_PX`."""
    image_mod = _pillow()
    if image_mod is None:
        return data
    try:
        with image_mod.open(io.BytesIO(data)) as image:
            out = _downscaled_png(image, image_mod)
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.screenshot resize failed")
        return data
    # No `or data` fallback: a successful PNG encode cannot yield zero bytes, so
    # that arm was unreachable and its mutant survived -- an unkillable branch
    # reads as defence while grading nothing. Every real failure path above
    # returns `data` explicitly.
    return data if out is None else out


#: ``screencap``'s pixel format id for RGBA_8888, the only one decoded here.
_RGBA_8888 = 1


def _frame_header(raw: object) -> tuple[int, int, int] | None:
    """``(width, height, header_len)`` of a decodable RAW frame, else None."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 12:
        return None
    width, height, fmt = struct.unpack_from("<III", raw, 0)
    pixels = width * height
    if width <= 0 or height <= 0 or pixels > MAX_DECODE_PIXELS:
        return None
    if fmt != _RGBA_8888:
        return None
    header = len(raw) - pixels * 4
    return (width, height, header) if header in (12, 16) else None


def _encoded(image: Any, pil_image: Any) -> bytes:
    """PNG bytes of *image*, scaled first when its long edge is over the bound."""
    longest = max(image.size)
    if longest > MAX_LONG_EDGE_PX:
        scale = MAX_LONG_EDGE_PX / float(longest)
        size = (
            max(1, int(round(image.width * scale))),
            max(1, int(round(image.height * scale))),
        )
        image = image.resize(size, pil_image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# The frame is ``width, height, format`` as little-endian uint32 -- plus a
# fourth word (the colour space) on newer Android -- then RGBA_8888 pixels.
# The header length is DERIVED from the byte count (12 or 16) rather than
# assumed per Android version. Scaled BEFORE it is encoded, which is the
# point: the device's PNG encode of the full frame is what item 7 removes.
#
# Never raises. ``None`` means "take the PNG path" -- any format, size or
# Pillow problem, and anything over :data:`MAX_DECODE_PIXELS`.
def png_from_raw(raw: object) -> bytes | None:
    """A RAW ``screencap`` frame as a PNG scaled to :data:`MAX_LONG_EDGE_PX`."""
    # never-raise: a picture is not a verdict
    with never_raise(logger, "mobile.screenshot.png_from_raw failed", exc_info=True):
        frame = _frame_header(raw)
        if frame is None:
            return None
        width, height, header = frame
        pil_image = _pillow()
        if pil_image is None:
            return None
        pixels = bytes(cast(bytes, raw)[header:])
        image = pil_image.frombuffer(
            "RGBA", (width, height), pixels, "raw", "RGBA", 0, 1
        )
        image = image.convert("RGB")
        return _encoded(image, pil_image)
    return None
