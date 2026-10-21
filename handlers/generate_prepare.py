"""Moved from handlers (refactor P2): generate_prepare."""

from __future__ import annotations

import json  # noqa: F401
import logging
from dataclasses import dataclass  # noqa: F401
from typing import Any  # noqa: F401

from agents.host_prep_serde import PrepSerdeError  # noqa: F401
from agents.host_submission import safe_prep_id  # noqa: F401
from handlers import (  # noqa: F401
    _common,
    _elicit,
    capture,
    export,
    generate,
    image_gate,
)
from handlers.gprep_args import _PrepArgs, _PrepFront, _PrepParts  # noqa: F401
from handlers.gprep_assemble import assemble_prepare_payload  # noqa: F401
from handlers.gprep_budget import (  # noqa: F401
    _budget_keep_flags,
    _kept_image_bytes,
    _shipped_capture_count,
)
from handlers.gprep_early import _prepare_early_refusal  # noqa: F401
from handlers.gprep_entry import (  # noqa: F401
    _args_from,
    _prepare_generate_guarded,
    _prepare_run,
    handle_prepare_test_cases,
)
from handlers.gprep_front import _prepare_front  # noqa: F401
from handlers.gprep_limits import (  # noqa: F401
    _MAX_IMAGE_RESULT_BYTES,
    _MAX_TEXT_BLOCK_BYTES,
    _SPLIT_META_KEYS,
)
from handlers.gprep_nextcall import (  # noqa: F401
    _category_floors,
    _next_call_block,
    _next_call_lines,
)
from handlers.gprep_notes import (  # noqa: F401
    _image_gap_channels,
    _image_gap_message,
    _image_gap_note_text,
    attested_image_gap_note,
    text_only_image_skip_note,
)
from handlers.gprep_pre import (  # noqa: F401
    _beat1_clarify,
    _host_generation_mode,
    _plan_nudge_if_due,
    _revive_shelved,
)
from handlers.gprep_render import render_prepare_payload  # noqa: F401
from handlers.gprep_run import (  # noqa: F401
    _build_prep_run,
    _missing_note_of,
    _prepare_generate,
)
from handlers.gprep_split import (  # noqa: F401
    _split_body_blocks,
    _split_prepare_text_blocks,
)
from tools import dispatch_guard, handover_cost  # noqa: F401
from tools.tool_briefs import FLOW_BRIEF  # noqa: F401

logger = logging.getLogger("tools.mcp_handlers")
