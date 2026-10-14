"""Host mode: the prep_id shape gate (safe_prep_id)."""

from __future__ import annotations

import re

from config.settings_guard import never_raise

# A prep_id is a uuid4 hex (tools/prep_store.py). Anything else is not one --
# and a host-supplied string rendered inside a code span in instructions the
# model then follows is an injection surface: `PID` INJECTED **bold** followed
# by a `## Fake heading` broke out of the span on two separate surfaces.
# Gating the SHAPE here makes every echo site safe by construction; sanitising
# the sites instead leaves the fortieth site one edit away.
_PREP_ID_RE = re.compile(r"\A[0-9a-fA-F]{8,64}\Z")


def safe_prep_id(value: object) -> str:
    """``value`` if it is shaped like a prep_id, else a sanitised stand-in.

    Never raises, and never returns something that can close a code span or
    start a markdown block. An id that fails the shape check is still ECHOED --
    a host that sent a typo needs to see what it sent -- but stripped and
    capped, so it can only ever read as a wrong id, never as instructions.
    """
    # pragma: no cover - defensive
    with never_raise():
        text = str(value or "").strip()
        if _PREP_ID_RE.match(text):
            return text
        flattened = "".join(
            " " if ch in "`\n\r\t" else ch
            for ch in text[:64]
            if ch.isprintable() or ch in "\n\r\t"
        )
        return " ".join(flattened.split())
    return ""
