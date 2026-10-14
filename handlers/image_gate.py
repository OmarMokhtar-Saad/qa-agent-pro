"""Facade over the ``handlers.image_gate_*`` modules (the Jira image gate).

Every name the old god module exposed is re-exported here so imports and
monkeypatches through ``handlers.image_gate`` keep resolving. The code lives in
``image_gate_labels`` (plan labels and markers), ``image_gate_names`` (name and
count clamps), ``image_gate_corpus`` (corpus keys and caps), ``image_gate_plan``
(plan normalising), ``image_gate_menu`` (beat 1 menu), ``image_gate_elicit``
(the asks), ``image_gate_ground`` (grounding notes), ``image_gate_unread_notes``
and ``image_gate_unread`` (unreadable-image notes), ``image_gate_refusal_parts``
and ``image_gate_refusal`` (image-loss refusal), ``image_gate_beat2_text`` and
``image_gate_beat2`` (beat 2). A scalar cap bound here is a stale copy, so patch
``image_gate_corpus`` instead. A patched collaborator only bites in the module
that calls it.
"""

from __future__ import annotations

import logging

from handlers import _common, _elicit, jira  # noqa: F401
from handlers.image_gate_beat2 import (  # noqa: F401
    _BeatTwoReq,
    _second_beat_is_silent,
    _text_only_image_skip_stamp,
    image_gate_second_beat,
)
from handlers.image_gate_beat2_text import (  # noqa: F401
    _partial_outstanding_clause,
    _second_beat_full,
    _second_beat_partial,
)
from handlers.image_gate_corpus import (  # noqa: F401
    _CORPUS_SOURCE_KEY_CAP,
    _CORPUS_TEXT_KEY_PREFIX,
    _FEATURE_TEXT_METADATA_CAP,
    _corpus_source_key,
)
from handlers.image_gate_elicit import (  # noqa: F401
    _confirm_image_gate_skip,
    _text_tier_result,
    elicit_source_plan,
    elicit_source_plan_status,
)
from handlers.image_gate_ground import (  # noqa: F401
    _grounding_note_atlassian,
    _grounding_source_note,
)
from handlers.image_gate_labels import (  # noqa: F401
    _IMAGE_GATE_ATTACH_LABEL,
    _IMAGE_GATE_LINE,
    _IMAGE_GATE_SKIP_LABEL,
    _IMAGE_PLAN_ALIASES,
    _IMAGE_PLAN_ORDINALS,
    _IMAGE_SOURCE_LABELS,
    _IMAGE_SOURCE_PLANS,
    _MARK_UNNAMED_REMAINDER,
    _MARK_UNREADABLE_ANSWER,
)
from handlers.image_gate_menu import (  # noqa: F401
    _IMAGE_GATE_OPTIONS,
    _image_gate_cause,
    _image_gate_menu_markdown,
    _image_gate_warnings,
)
from handlers.image_gate_names import (  # noqa: F401
    _IMAGE_NAME_ALLOWED,
    _clamped_count,
    _int_or,
    _jira_image_cap,
    _safe_image_name,
)
from handlers.image_gate_plan import (  # noqa: F401
    _gate_jira_source,
    _gated_not_unsupported,
    _normalize_source_plan,
    _rejected_plan_note,
)
from handlers.image_gate_refusal import (  # noqa: F401
    _REFUSAL_OPTIONS,
    _refusal_text,
    _RefusalReq,
    _reprep_image_loss_refusal,
)
from handlers.image_gate_refusal_parts import (  # noqa: F401
    _refusal_ago,
    _refusal_carries_line,
    _refusal_channels,
    _refusal_consequence,
    _refusal_counts,
    _refusal_label_block,
    _RefusalCounts,
)
from handlers.image_gate_unread import (  # noqa: F401
    _ticket_image_evidence,
    _unreadable_images_note,
    _unreadable_images_text,
)
from handlers.image_gate_unread_notes import (  # noqa: F401
    _partial_fetch_unreadable_note,
    _zero_fetch_unreadable_note,
)

logger = logging.getLogger("tools.mcp_handlers")
