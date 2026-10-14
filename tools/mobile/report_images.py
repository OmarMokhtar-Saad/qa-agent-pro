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

import shutil  # noqa: F401  (tests patch ``report_images.shutil.which``)

from tools.mobile.rimg_core import (  # noqa: F401
    DERIVED,
    FFMPEG_TIMEOUT_S,
    HASH_CHARS,
    POSTER_WIDTH,
    WEBP_METHOD,
    WEBP_QUALITY,
    WIDTHS,
    Image,  # a stale copy: tests patch ``rimg_core.Image``, the one readers use
    _clamp,
    _digest,
    _ffmpeg,
    _int_width,
    logger,
)
from tools.mobile.rimg_poster import (  # noqa: F401
    _first_frame_png,
    _write_poster,
    ensure_poster,
)
from tools.mobile.rimg_srcset import (  # noqa: F401
    _Srcset,
    _srcset_fallback,
    srcset_markup,
)
from tools.mobile.rimg_variants import ensure_variants, overlay_style  # noqa: F401
