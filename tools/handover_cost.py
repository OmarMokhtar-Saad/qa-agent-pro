"""What this server HANDS OVER to the tester's own chat model, measured once,
at the moment of handover.

WHY THIS IS NOT THE DELETED TOKEN METER (read before changing it). The old
``tools/token_meter.py`` accumulated per-phase usage and rendered a ``$``
estimate on the submit reply. It was deleted on 2026-08-16 (dead-code deletion
P2-G2a) for a reason that is a design lesson rather than a bug: generation went
chat-only on 2026-08-12, so nothing ever called ``note()`` again and
``summary_line()`` returned ``""`` on every single submit. It was flagless,
always ON, and always EMPTY, and no tester ever saw a cost line.

The shape difference that keeps this module honest:

* It ACCUMULATES NOTHING. There is no ``note()``, no store, no session total.
  Every number is computed by ``measure()`` from the payload object being
  handed over in that same call, so it cannot go stale and it cannot go empty
  while a payload exists.
* It measures the HANDOVER, not the spend. The server can see exactly how many
  characters and images it puts on the wire. It cannot see a token, cannot see
  which model the tester runs, and cannot see the output length -- so none of
  those are reported, and ``handover_line`` says so out loud.
* There is NO currency figure and NO per-model table. A ``$`` here would be a
  fabricated number, which is precisely what got the last feature deleted.

Pure and synchronous: no I/O, no settings read, no model call. Never raises --
a cost disclosure may not break a prepare.
"""

from __future__ import annotations

from tools.hcost_core import Handover, payload_bytes, token_band  # noqa: F401
from tools.hcost_measure import measure  # noqa: F401
from tools.hcost_render import handover_line  # noqa: F401
