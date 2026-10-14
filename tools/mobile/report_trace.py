"""Action trace rendering for the report: action lines, step rows, sequence rows and plain-language step words."""

from __future__ import annotations

from typing import Any  # noqa: F401

from tools.mobile import actions as actions_mod  # noqa: F401
from tools.mobile import run_store  # noqa: F401
from tools.mobile.report_text import MAX_TEXT, _ms, _text, esc, fmt_ms  # noqa: F401
from tools.mobile.report_widgets import _outcome_pill  # noqa: F401
from tools.mobile.rfrm_look import _resolve_look  # noqa: F401
from tools.mobile.rfrm_phone import _phone_html  # noqa: F401
from tools.mobile.rfrm_pic import _step_pics  # noqa: F401
from tools.mobile.rtrc_action import (  # noqa: F401
    _action_hint,
    _action_line,
    _typed_literal,
)
from tools.mobile.rtrc_plain import (  # noqa: F401
    _FIXED_WORDS,
    _TARGET_VERBS,
    _TURN_OPS,
    _WAIT_OPS,
    _element_of,
    _named_target,
    _parts_html,
    _plain_action,
    _plain_turn_op,
    _plain_wait,
    _q,
    _rid_tail,
    _step_words,
)
from tools.mobile.rtrc_rows import (  # noqa: F401
    MAX_ROWS,
    _changed,
    _notes_applied,
    _obs_key,
    _trace_row,
    _trace_rows,
)
from tools.mobile.rtrc_seq import (  # noqa: F401
    OP_KIND,
    _seq_bar,
    _seq_detail,
    _seq_head,
    _seq_item,
    _seq_rows,
    _SeqParts,
)
from tools.mobile.rtrc_step import (  # noqa: F401
    _AFTER_LOOK,
    OUTCOME_WORDS,
    _step_html,
    _StepSlot,
)
