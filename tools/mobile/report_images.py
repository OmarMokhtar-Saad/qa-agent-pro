"""Report-time image derivatives for the mobile run report.

Everything here is read-only over the run's captured media except
``<media_dir>/derived/``, the one directory this module writes. Frames are
resized to 480 and 960 px WebP (never upscaled), clips get a 480 px WebP
poster, and every file is named by the SHA-256 of its source so a re-render
reuses what is already there.

No public function raises: a failure comes back in the returned ``error``
string so the renderer can fall back to the original file and say so in
Diagnostics. :func:`srcset_markup` escapes through the caller's own ``esc``,
so the report keeps its single escaping funnel.
"""

from __future__ import annotations

import hashlib
import io
import logging
import shutil
import subprocess
from pathlib import Path


def _load_pillow():
    try:
        from PIL import Image as pil_image
    except ImportError:  # pragma: no cover - Pillow ships with requirements
        return None
    return pil_image


#: The Pillow ``Image`` module, or None when Pillow is absent (tests set it).
Image = _load_pillow()

logger = logging.getLogger(__name__)

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


def ensure_variants(source: Path, media_dir: Path) -> dict:
    """Write the 480/960 WebP variants of ``source`` under ``media_dir/derived``.

    Returns ``{"hash", "width", "height", "widths": {480: name|None, 960: ...},
    "error"}``. A width at or above the source's own is ``None``: the original
    already serves it. A source narrower than every width gets one more entry,
    keyed by its own width: the same pixels as WebP, which for a PNG
    screenshot is several times smaller. ``names`` are relative to ``media_dir``.
    """
    out = {
        "hash": None,
        "width": None,
        "height": None,
        "widths": {w: None for w in WIDTHS},
        "error": None,
    }
    try:
        out["hash"] = _digest(Path(source))
        if Image is None:
            out["error"] = "Pillow is not installed; showing original frames"
            return out
        with Image.open(source) as img:
            img.load()
            out["width"], out["height"] = img.size
            derived = Path(media_dir) / DERIVED
            rgb = None
            wanted = [w for w in WIDTHS if w < img.width]
            if not wanted and img.format != "WEBP":
                wanted = [img.width]
            for w in wanted:
                name = f"{DERIVED}/{out['hash']}-{w}.webp"
                target = Path(media_dir) / name
                if not target.exists():
                    if rgb is None:
                        rgb = img.convert("RGB")
                    h = max(1, round(img.height * w / img.width))
                    derived.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(".tmp")
                    rgb.resize((w, h), Image.Resampling.LANCZOS).save(
                        tmp, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD
                    )
                    tmp.replace(target)
                out["widths"][w] = name
    except Exception as exc:
        logger.warning("mobile.report_images.ensure_variants failed: %s", exc)
        out["widths"] = {w: None for w in WIDTHS}
        out["error"] = f"could not resize {Path(source).name}: {type(exc).__name__}"
    return out


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _first_frame_png(exe: str, clip: Path) -> bytes:
    """The first frame of ``clip`` as PNG bytes; ``b""`` when ffmpeg fails."""
    proc = subprocess.run(
        [
            exe,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        timeout=FFMPEG_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return b""
    return proc.stdout or b""


def _write_poster(png: bytes, target: Path) -> None:
    """Resize the PNG frame to at most ``POSTER_WIDTH`` and save it as WebP."""
    with Image.open(io.BytesIO(png)) as frame:
        frame.load()
        w = min(POSTER_WIDTH, frame.width)
        h = max(1, round(frame.height * w / frame.width))
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        frame.convert("RGB").resize((w, h), Image.Resampling.LANCZOS).save(
            tmp, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD
        )
        tmp.replace(target)


def ensure_poster(clip: Path, media_dir: Path) -> dict:
    """First frame of ``clip`` as a 480 px WebP, or ``{"path": None, "error"}``.

    ffmpeg only decodes (to PNG on stdout, which every build can write);
    Pillow resizes and encodes, so a build without a WebP encoder still works.
    """
    out = {"path": None, "error": None}
    try:
        exe = _ffmpeg()
        if not exe:
            out["error"] = "ffmpeg not found; clips have no poster"
            return out
        if Image is None:
            out["error"] = "Pillow is not installed; clips have no poster"
            return out
        name = f"{DERIVED}/{_digest(Path(clip))}-poster.webp"
        target = Path(media_dir) / name
        if not target.exists():
            png = _first_frame_png(exe, clip)
            if not png:
                out["error"] = f"ffmpeg could not read {Path(clip).name}"
                return out
            _write_poster(png, target)
        out["path"] = name
    except Exception as exc:
        logger.warning("mobile.report_images.ensure_poster failed: %s", exc)
        out["path"] = None
        out["error"] = f"no poster for {Path(clip).name}: {type(exc).__name__}"
    return out


def _clamp(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 2)


def overlay_style(bounds, dev_w, dev_h) -> dict | None:
    """Percent box + tap-dot centre for ``bounds`` on a ``dev_w`` x ``dev_h`` screen.

    ``bounds`` is a sorted ``(left, top, right, bottom)`` in device pixels.
    Returns None for a degenerate device size or unusable bounds.
    """
    try:
        if isinstance(dev_w, bool) or isinstance(dev_h, bool):
            return None
        dev_w, dev_h = float(dev_w), float(dev_h)
        if not (dev_w > 0 and dev_h > 0):
            return None
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
    except Exception:
        return None


def _int_width(value) -> int:
    """``value`` as an int pixel width; 0 for a bool, empty or unparsable value."""
    if isinstance(value, bool):
        return 0
    try:
        return int(value) if value else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def srcset_markup(
    esc,
    media_rel_dir,
    content_hash,
    widths,
    original_name,
    original_width,
    original_dir=None,
) -> dict:
    """``{"src", "srcset", "sizes", "hash", "error"}``, each value passed through ``esc``.

    ``widths`` is the ``widths`` dict from :func:`ensure_variants`; the original
    is always the largest candidate. ``src`` is the 480 variant when there is
    one, else the original. Malformed input falls back to the original alone
    with ``error`` set; if even that cannot be escaped every value is ``""``,
    never an unescaped string. ``original_dir`` is where the original lives
    when that is not ``media_rel_dir`` (a screenshot beside the report).
    """
    try:
        base = str(media_rel_dir).rstrip("/")
        home = base if original_dir is None else str(original_dir).rstrip("/")
        original = f"{home}/{original_name}"
        ow = _int_width(original_width)
        candidates = [
            (f"{base}/{name}", int(w))
            for w, name in sorted((widths or {}).items())
            if name and not isinstance(w, bool)
        ]
        parts = [f"{esc(url)} {w}w" for url, w in candidates]
        # A native-width copy already serves the original's descriptor, and
        # two candidates with one width make the whole srcset invalid.
        if ow > 0 and not any(w >= ow for _url, w in candidates):
            parts.append(f"{esc(original)} {ow}w")
        src = candidates[0][0] if candidates else original
        return {
            "src": esc(src),
            "srcset": ", ".join(parts),
            "sizes": esc("(max-width: 600px) 92vw, 480px"),
            "hash": esc(content_hash or ""),
            "error": None,
        }
    except Exception as exc:
        logger.warning("mobile.report_images.srcset_markup failed: %s", exc)
        return _srcset_fallback(esc, media_rel_dir, original_name, original_dir, exc)


def _srcset_fallback(esc, media_rel_dir, original_name, original_dir, exc) -> dict:
    """The original alone, with ``error`` set; every value ``""`` if even that fails."""
    out = {
        "src": "",
        "srcset": "",
        "sizes": "",
        "hash": "",
        "error": f"no srcset: {type(exc).__name__}",
    }
    try:
        home = media_rel_dir if original_dir is None else original_dir
        out["src"] = esc(f"{str(home).rstrip('/')}/{original_name}")
    except Exception:
        out["src"] = ""
    return out
