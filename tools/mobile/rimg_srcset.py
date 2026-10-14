"""Report image srcset: the responsive-image markup values, escaped through the caller's ``esc``."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config.settings_guard import never_raise
from tools.mobile.rimg_core import _int_width, logger


@dataclass(frozen=True)
class _Srcset:
    """The inputs of :func:`srcset_markup` besides the caller's ``esc``."""

    media_rel_dir: Any
    hash_: Any
    widths: Any
    original_name: Any
    original_width: Any
    original_dir: Any = None


def _home(spec: _Srcset, base: str) -> str:
    """Where the original lives: *base*, or ``original_dir`` when that is given."""
    return base if spec.original_dir is None else str(spec.original_dir).rstrip("/")


def _candidates(base: str, widths: Any) -> list:
    """The ``(url, width)`` variants in width order; unnamed or boolean-keyed ones skipped."""
    return [
        (f"{base}/{name}", int(w))
        for w, name in sorted((widths or {}).items())
        if name and not isinstance(w, bool)
    ]


def _srcset_parts(esc: Any, candidates: list, native: tuple) -> list:
    """The ``url Nw`` entries; the original joins them unless a native-width copy serves it."""
    original, ow = native
    parts = [f"{esc(url)} {w}w" for url, w in candidates]
    # A native-width copy already serves the original's descriptor, and
    # two candidates with one width make the whole srcset invalid.
    if ow > 0 and not any(w >= ow for _url, w in candidates):
        parts.append(f"{esc(original)} {ow}w")
    return parts


def _markup(esc, spec: _Srcset) -> dict:
    base = str(spec.media_rel_dir).rstrip("/")
    home = _home(spec, base)
    original = f"{home}/{spec.original_name}"
    candidates = _candidates(base, spec.widths)
    native = original, _int_width(spec.original_width)
    parts = _srcset_parts(esc, candidates, native)
    src = candidates[0][0] if candidates else original
    return {
        "src": esc(src),
        "srcset": ", ".join(parts),
        "sizes": esc("(max-width: 600px) 92vw, 480px"),
        "hash": esc(spec.hash_ or ""),
        "error": None,
    }


# ``widths`` is the ``widths`` dict from :func:`ensure_variants`; the original
# is always the largest candidate. ``src`` is the 480 variant when there is
# one, else the original. Malformed input falls back to the original alone
# with ``error`` set; if even that cannot be escaped every value is ``""``,
# never an unescaped string. ``original_dir`` is where the original lives
# when that is not ``media_rel_dir`` (a screenshot beside the report).
def srcset_markup(esc, spec: _Srcset) -> dict:
    """``{"src", "srcset", "sizes", "hash", "error"}``, each value passed through ``esc``."""
    with never_raise(
        logger,
        "mobile.report_images.srcset_markup failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        return _markup(esc, spec)
    return _srcset_fallback(esc, spec, g.exc)


def _srcset_fallback(esc, spec: _Srcset, exc: BaseException | None) -> dict:
    """The original alone, with ``error`` set; every value ``""`` if even that fails."""
    out = {
        "src": "",
        "srcset": "",
        "sizes": "",
        "hash": "",
        "error": f"no srcset: {type(exc).__name__}",
    }
    with never_raise() as g:
        home = spec.media_rel_dir if spec.original_dir is None else spec.original_dir
        out["src"] = esc(f"{str(home).rstrip('/')}/{spec.original_name}")
    if g.failed:
        out["src"] = ""
    return out
