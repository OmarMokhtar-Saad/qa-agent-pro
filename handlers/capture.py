"""Facade over the ``handlers.capture_*`` modules (``qa_capture_screens``).

Every name the old god module exposed is re-exported here so imports and
monkeypatches through ``handlers.capture`` keep resolving. The code lives in
``capture_limits`` (caps and the shared tray / eviction / shelf state),
``capture_tray`` and ``capture_shelf`` (stash, peek, shelve), ``capture_revive``
and ``capture_carry`` (carry-forward), ``capture_device`` and ``capture_peek``
(picker, peek), ``capture_screens`` (loop), ``capture_reply`` and
``capture_flow`` (the tool handler). The state dicts are the same objects; a
scalar cap bound here is a stale copy, so patch ``capture_limits`` instead. A
patched collaborator only bites in the module that calls it.
"""

from __future__ import annotations

import logging
import uuid  # noqa: F401
from typing import Any, NamedTuple  # noqa: F401

from agents.host_gen_args import CarryAsk  # noqa: F401
from handlers import _common, _elicit, _reply, image_gate  # noqa: F401
from handlers import capture_screens as _screens
from handlers._boundary import handler_guard  # noqa: F401
from handlers.capture_carry import (  # noqa: F401
    _carry_acked_note,
    _carry_refusal,
    _carry_verdict,
    carry_forward_or_refuse,
)
from handlers.capture_carry_plan import (  # noqa: F401
    _carry_age,
    _carry_plan,
    _CarryPlan,
)
from handlers.capture_device import (  # noqa: F401
    _device_menu_markdown,
    _elicit_device_with_rescan,
    _pick_capture_device,
)
from handlers.capture_evict import (  # noqa: F401
    _dedup_ids,
    _eviction_reason,
    _normalised_ids,
    _note_eviction,
    _over_cap_ids,
)
from handlers.capture_flow import (  # noqa: F401
    _CAPTURE_DISABLED_NOTICE,
    _capture_from_device,
    handle_capture_screens,
)
from handlers.capture_labels import (  # noqa: F401
    _capture_labels,
    _shelve_ticket_image_labels,
    _take_ticket_image_labels,
)
from handlers.capture_limits import (  # noqa: F401
    _CAPTURE_COUNT_MAX,
    _CAPTURE_EVICTIONS,
    _CAPTURE_EVICTIONS_MAX,
    _CAPTURE_TRAY,
    _CAPTURE_TRAY_MAX,
    _CAPTURE_TRAY_MAX_BYTES,
    _CAPTURE_TRAY_TTL_S,
    _CARRY_SHELF,
    _CARRY_SHELF_MAX,
    _CARRY_SHELF_MAX_BYTES,
    _MAX_MISSING_SHOWN,
    _TICKET_IMAGE_LABELS,
)
from handlers.capture_missing import (  # noqa: F401
    _capture_retry_hint,
    _describe_missing,
    _render_missing,
)
from handlers.capture_peek import (  # noqa: F401
    _capture_peek,
)
from handlers.capture_reply import (  # noqa: F401
    _capture_rows,
    _capture_specs,
    _capture_tray_notes,
    _captured_reply,
    _stash_with_accounting,
    _TrayNotes,
)
from handlers.capture_revive import (  # noqa: F401
    _revive_one_capture,
    resolvable_captures,
    revive_captures,
    revive_resent_captures,
)
from handlers.capture_screens import (  # noqa: F401
    _capture_want,
    _fa_capture_another,
    _fa_capture_bound,
    _ShotLoop,
)
from handlers.capture_shelf import (  # noqa: F401
    _drop_captures,
    _shelve_capture,
    _sweep_carry_shelf,
)
from handlers.capture_tray import (  # noqa: F401
    _stash_captures,
    _tray_image_payload,
    _usable_shot,
    peek_captures,
)
from handlers.capture_tray_sweep import (  # noqa: F401
    _sweep_capture_tray,
)
from tools import clock  # noqa: F401
from tools.device_manager import capture_screenshot, list_devices  # noqa: F401

logger = logging.getLogger("tools.mcp_handlers")


async def _fa_capture_screens(run: _ShotLoop) -> tuple:
    """Call-time hop to ``capture_screens.fa_capture_screens`` (patch seam)."""
    return await _screens.fa_capture_screens(run)
