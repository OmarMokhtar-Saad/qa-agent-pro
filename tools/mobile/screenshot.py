"""One screen, as pixels, in the shape the MCP tool layer can already attach.

The pruned uiautomator dump is empty or nearly empty on any app whose UI is not
in the accessibility tree -- Flutter, a React Native canvas, a game, a
custom-drawn view, a WebView -- and the tester's own model then plans from a
list of nothing. This module turns the PNG ``adb.screencap`` returned into the
``{filename, mime, data}`` spec ``mcp_server._image_content_blocks`` already
consumes for ``qa_capture_screens``, so the same proven transport carries the
mobile lane's screen.

**It declares TWO caps, on two different axes, and the second exists because
this module now DECODES.** A cap bounds the axis it measures, and nothing else:

* the LONG EDGE (:data:`MAX_LONG_EDGE_PX`) bounds what the model is asked to
  read, because the size of the picture is a fact about perception and this is
  the only module that has the picture;
* the PIXEL COUNT (:data:`MAX_DECODE_PIXELS`) bounds what this process is asked
  to DECODE, which is a different quantity with a different cost. Decode cost
  is pixels, not bytes: a 690KB PNG -- comfortably inside the transport's byte
  cap -- is 169M pixels and costs about 1.46GB of peak RSS and a second inside
  ``_resized``. That band was reachable and unguarded, and every declared cap
  stayed green over it, because a completeness sweep can only assert over caps
  that EXIST and this axis had never been declared.

This paragraph previously said the module declared ONE cap. That was true while
the PNG was only ever forwarded -- bytes in, bytes out, never decoded -- and it
is recorded here because the sentence outliving its truth is what made the
missing axis invisible: a reader auditing whether this module was bounded found
an explicit assurance that it was, and stopped.

The BYTES are still capped at the transport, in ``adb.MAX_SCREENSHOT_BYTES``,
which is where the dump's cap lives too -- and that cap keeps its job: it bounds
the raw device answer read into memory, and on the degraded path where Pillow is
absent and the full-size capture rides along it is once again the only thing
standing between a chat transport and a reply no client will render. Three caps,
three constraints, a CEILINGS row each.

**Why a module of its own**, rather than beside the packet builders:
``tools/mobile/run_packets.py`` builds PACKETS -- dicts the model answers -- and knows
nothing about MCP content types; ``tools/mobile/render.py`` renders markdown a
tester reads. An image spec is neither, and it has exactly one consumer
(``handlers``), so putting it in either of those would put a transport
artifact inside a builder with no other reason to know the transport exists. It
is also small and importable without the composition root, which is what makes
the note text below assertable on its own.

**Three notes, not one with a caveat.** A model must act differently in each of
three states, so each state gets its own name and its own words:

* :data:`ATTACHED_NOTE` -- a picture of this screen rode along; look at it.
* :data:`CAPTURE_FAILED_NOTE` -- a picture was ATTEMPTED and the device refused
  (a secure window, a timeout, no device). Plan from the element list alone.
* :data:`NO_IMAGE_CHANNEL_NOTE` -- nothing was attempted, because this caller
  has no way to carry image content back (the ``str``-returning projection of
  the handlers, which every non-MCP caller uses).

Collapsing the last two would tell a tester's model "the capture failed" when
nothing was ever tried, which is the half-wired-sentinel failure this project
has already paid for: one name, two meanings, and the reader cannot tell which
one they were handed.

**The resize does NOT get a fourth note.** The picture is scaled down to
:data:`MAX_LONG_EDGE_PX` before it is attached, and on an install without
Pillow it rides at full size instead. That is a real difference, but not a
difference in what the reading model DOES: every claim
:data:`ATTACHED_NOTE` makes is true at either resolution, and a fourth name
would be a caveat wearing a state's clothes -- packet budget spent teaching
a distinction nobody can act on. The party who needs to know is the operator,
so the degraded path says so in the LOG. One producer, one meaning: the note
answers whether there is a picture, the log answers why it is big.
"""

from __future__ import annotations

import io  # noqa: F401
import logging
import struct  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile.screenshot_capture import (  # noqa: F401
    MIN_RETRY_TIMEOUT_S,
    RETRY_MARGIN_S,
    capture,
    retry_timeout_s,
)
from tools.mobile.screenshot_limits import (  # noqa: F401
    MAX_DECODE_PIXELS,
    MAX_LONG_EDGE_PX,
)
from tools.mobile.screenshot_notes import (  # noqa: F401
    ATTACHED_NOTE,
    CAPTURE_FAILED_NOTE,
    CAPTURE_TIMED_OUT_NOTE,
    NO_IMAGE_CHANNEL_NOTE,
    NOT_ATTACHED_NOTE,
)
from tools.mobile.screenshot_resize import (  # noqa: F401
    _RGBA_8888,
    _downscaled_png,
    _resized,
    png_from_raw,
)
from tools.mobile.screenshot_spec import (  # noqa: F401
    _NAME_KEEP,
    _NAME_PART_CHARS,
    MIME,
    filename_for,
    to_spec,
)

logger = logging.getLogger(__name__)
