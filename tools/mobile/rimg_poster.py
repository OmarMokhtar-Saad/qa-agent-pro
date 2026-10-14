"""Report image posters: the first frame of a clip as a 480 px WebP."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from config.settings_guard import never_raise
from tools.mobile import rimg_core
from tools.mobile.rimg_core import (
    DERIVED,
    FFMPEG_TIMEOUT_S,
    POSTER_WIDTH,
    _digest,
    _ffmpeg,
    _save_webp,
    logger,
)
from tools.process_runner import run


def _frame_argv(exe: str, clip: Path) -> list[str]:
    """The ffmpeg argv that writes ``clip``'s first frame to stdout as PNG."""
    return [
        exe,
        *("-nostdin", "-v", "error", "-i", str(clip)),
        *("-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"),
    ]


def _first_frame_png(exe: str, clip: Path) -> bytes:
    """The first frame of ``clip`` as PNG bytes; ``b""`` when ffmpeg fails."""
    result = run(_frame_argv(exe, clip), timeout=FFMPEG_TIMEOUT_S)
    if result.exc is not None:
        raise result.exc
    if result.rc != 0:
        return b""
    return result.stdout or b""


def _write_poster(png: bytes, target: Path) -> None:
    """Resize the PNG frame to at most ``POSTER_WIDTH`` and save it as WebP."""
    with rimg_core.Image.open(io.BytesIO(png)) as frame:
        frame.load()
        _save_webp(frame.convert("RGB"), min(POSTER_WIDTH, frame.width), target)


def _poster_blocker(exe: str | None) -> str | None:
    """Why no poster can be made (no ffmpeg, no Pillow), else None."""
    if not exe:
        return "ffmpeg not found; clips have no poster"
    if rimg_core.Image is None:
        return "Pillow is not installed; clips have no poster"
    return None


def _make_poster(exe: str, clip: Path, target: Path) -> str | None:
    """Write the poster unless it exists; the error string when ffmpeg yields no frame."""
    if target.exists():
        return None
    png = _first_frame_png(exe, clip)
    if not png:
        return f"ffmpeg could not read {Path(clip).name}"
    _write_poster(png, target)
    return None


# ffmpeg only decodes (to PNG on stdout, which every build can write);
# Pillow resizes and encodes, so a build without a WebP encoder still works.
def ensure_poster(clip: Path, media_dir: Path) -> dict:
    """First frame of ``clip`` as a 480 px WebP, or ``{"path": None, "error"}``."""
    out: dict[str, Any] = {"path": None, "error": None}
    with never_raise(
        logger,
        "mobile.report_images.ensure_poster failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        exe = _ffmpeg()
        out["error"] = _poster_blocker(exe)
        if out["error"] or not exe:
            return out
        name = f"{DERIVED}/{_digest(Path(clip))}-poster.webp"
        out["error"] = _make_poster(exe, clip, Path(media_dir) / name)
        out["path"] = None if out["error"] else name
    if g.failed:
        out["path"] = None
        out["error"] = f"no poster for {Path(clip).name}: {type(g.exc).__name__}"
    return out
