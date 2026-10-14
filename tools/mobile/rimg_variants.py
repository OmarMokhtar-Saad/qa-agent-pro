"""Report image variants: the 480/960 WebP frames and the tap-overlay geometry."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config.settings_guard import never_raise
from tools.mobile import rimg_core
from tools.mobile.rimg_core import DERIVED, WIDTHS, _clamp, _digest, _save_webp, logger


def _blank_variants() -> dict:
    """The result dict of :func:`ensure_variants` before anything is written."""
    return {
        "hash": None,
        "width": None,
        "height": None,
        "widths": {w: None for w in WIDTHS},
        "error": None,
    }


def _wanted_widths(img: Any) -> list:
    """The widths to write: those below the source's own, else its native width as WebP."""
    wanted = [w for w in WIDTHS if w < img.width]
    if not wanted and img.format != "WEBP":
        wanted = [img.width]
    return wanted


def _fill_variants(img: Any, media_dir: Path, out: dict) -> None:
    """Write each wanted width's WebP under *media_dir*; record its name in *out*."""
    rgb = None
    for w in _wanted_widths(img):
        name = f"{DERIVED}/{out['hash']}-{w}.webp"
        target = media_dir / name
        if not target.exists():
            if rgb is None:
                rgb = img.convert("RGB")
            _save_webp(rgb, w, target)
        out["widths"][w] = name


def _write_variants(out: dict, source: Path, media_dir: Path) -> None:
    out["hash"] = _digest(Path(source))
    if rimg_core.Image is None:
        out["error"] = "Pillow is not installed; showing original frames"
        return
    with rimg_core.Image.open(source) as img:
        img.load()
        out["width"], out["height"] = img.size
        _fill_variants(img, Path(media_dir), out)


# Returns ``{"hash", "width", "height", "widths": {480: name|None, 960: ...},
# "error"}``. A width at or above the source's own is ``None``: the original
# already serves it. A source narrower than every width gets one more entry,
# keyed by its own width: the same pixels as WebP, which for a PNG
# screenshot is several times smaller. ``names`` are relative to ``media_dir``.
def ensure_variants(source: Path, media_dir: Path) -> dict:
    """Write the 480/960 WebP variants of ``source`` under ``media_dir/derived``."""
    out = _blank_variants()
    with never_raise(
        logger,
        "mobile.report_images.ensure_variants failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        _write_variants(out, source, media_dir)
    if g.failed:
        out["widths"] = {w: None for w in WIDTHS}
        out["error"] = f"could not resize {Path(source).name}: {type(g.exc).__name__}"
    return out


def _dev_size(dev_w: Any, dev_h: Any) -> tuple | None:
    """The device size as positive floats, or None for a bool, zero or negative size."""
    if isinstance(dev_w, bool) or isinstance(dev_h, bool):
        return None
    size = float(dev_w), float(dev_h)
    return size if size[0] > 0 and size[1] > 0 else None


def _pct_box(bounds: Any, size: tuple) -> dict:
    """The percent box and tap-dot centre of *bounds* on a *size* screen."""
    dev_w, dev_h = size
    left, top, right, bottom = (float(v) for v in bounds)
    x1, x2 = _clamp(left / dev_w * 100), _clamp(right / dev_w * 100)
    y1, y2 = _clamp(top / dev_h * 100), _clamp(bottom / dev_h * 100)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    return {
        "left": x1,
        "top": y1,
        "width": round(x2 - x1, 2),
        "height": round(y2 - y1, 2),
        "dot_x": round((x1 + x2) / 2, 2),
        "dot_y": round((y1 + y2) / 2, 2),
    }


# ``bounds`` is a sorted ``(left, top, right, bottom)`` in device pixels.
# Returns None for a degenerate device size or unusable bounds.
def overlay_style(bounds, dev_w, dev_h) -> dict | None:
    """Percent box + tap-dot centre for ``bounds`` on a ``dev_w`` x ``dev_h`` screen."""
    try:
        size = _dev_size(dev_w, dev_h)
        return None if size is None else _pct_box(bounds, size)
    except (ArithmeticError, TypeError, ValueError):
        return None
