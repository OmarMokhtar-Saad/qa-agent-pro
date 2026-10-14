"""Report image core: Pillow loading, the width constants, digests and the shared WebP writer."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, cast

#: The Pillow ``Image`` module, or None when Pillow is absent (tests set it).
try:
    import PIL.Image as Image
except ImportError as _exc:  # pragma: no cover - Pillow ships with requirements
    if _exc.name not in ("PIL", "PIL.Image"):
        raise
    Image = cast("Any", None)

logger = logging.getLogger("tools.mobile.report_images")

WIDTHS = (480, 960)
POSTER_WIDTH = 480
WEBP_QUALITY = 82
WEBP_METHOD = 6
FFMPEG_TIMEOUT_S = 20
DERIVED = "derived"
HASH_CHARS = 16


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            sha.update(chunk)
    return sha.hexdigest()[:HASH_CHARS]


def _clamp(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 2)


def _int_width(value) -> int:
    """``value`` as an int pixel width; 0 for a bool, empty or unparsable value."""
    if isinstance(value, bool):
        return 0
    try:
        return int(value) if value else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _save_webp(rgb: Any, width: int, target: Path) -> None:
    """Resize *rgb* to *width* (aspect kept) and write it as WebP at *target*, atomically."""
    h = max(1, round(rgb.height * width / rgb.width))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    rgb.resize((width, h), Image.Resampling.LANCZOS).save(
        tmp, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD
    )
    tmp.replace(target)
