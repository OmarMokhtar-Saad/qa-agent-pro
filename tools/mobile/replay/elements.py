"""Replay engine: element labels, tap-target resolution, swipe geometry and miss details. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from tools.mobile.replay.elements_detail import (  # noqa: F401
    AMBIGUOUS_ROLE_PREFIX,
    MISS_CONFLICT,
    MISS_PLAIN,
    MISS_STALE_ALL,
    MISS_STALE_ID,
    MISS_STALE_SELECTOR,
    TOO_SMALL_TO_SWIPE,
    _candidates,
    _stale_detail,
    ambiguous_role_detail,
    missing_element_detail,
)
from tools.mobile.replay.elements_geom import (  # noqa: F401
    _center,
    _clamp_source,
    _display_box,
    _edge,
    _on_screen_box,
    _panel_far_edges,
)
from tools.mobile.replay.elements_hit import (  # noqa: F401
    ACTUATING_OPS,
    _click_point,
    _clickable_box,
    _screen_elements,
    actuated_element,
    resolve_tap_text,
)
from tools.mobile.replay.elements_label import (  # noqa: F401
    _TOKEN_RE,
    IDENTIFYING_KEYS,
    _contained_text,
    _int_box,
    _is_contained_child,
    _label_part,
    _split_camel,
    _tokens,
    element_label,
    logger,
)
from tools.mobile.replay.elements_swipe import (  # noqa: F401
    _MIN_SWIPE_PX,
    _SWIPE,
    _SWIPE_INSET,
    TouchSpec,
    _centred_swipe,
    _swipe_points,
    _touch_point,
)
