"""Phone frames, wireframes and screenshot rendering for the report, including the screen look resolution."""

from __future__ import annotations

import logging  # noqa: F401
from contextvars import ContextVar  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import report_images, run_store, screen_phone  # noqa: F401
from tools.mobile.report_media import _IMAGES, _img_html, _ImgSpec  # noqa: F401
from tools.mobile.report_text import _text, esc  # noqa: F401
from tools.mobile.report_widgets import _PageData  # noqa: F401
from tools.mobile.rfrm_box import (  # noqa: F401
    MAX_FRAME_H,
    MAX_FRAME_W,
    _frame_box,
    _frame_scale,
    scale_bounds,
)
from tools.mobile.rfrm_extent import (  # noqa: F401
    MAX_DEVICE_EXTENT,
    _bounds_of,
    _device_size,
    _elements_extent,
    _stored_extent,
)
from tools.mobile.rfrm_frame import (  # noqa: F401
    _drawn_frame,
    _frame_html,
    _geometry_note,
    _missing_frame,
    _rect_html,
)
from tools.mobile.rfrm_look import (  # noqa: F401
    AMBIGUOUS_LOOK,
    LOOK_AMBIGUOUS,
    LOOK_EXACT,
    LOOK_UNCAPTURED,
    NOT_CAPTURED,
    _look_is_ambiguous,
    _resolve_look,
)
from tools.mobile.rfrm_phone import (  # noqa: F401
    _WIRE_LEGEND,
    _element_map_fold,
    _empty_phone,
    _phone_html,
    _PhoneHead,
    _seq_frames,
)
from tools.mobile.rfrm_pic import (  # noqa: F401
    STEP_SIZES,
    TARGET_OPS,
    _Caption,
    _exact_pic_body,
    _look_pic,
    _same_gap,
    _step_pics,
    _tap_overlay,
)
from tools.mobile.rfrm_shot import (  # noqa: F401
    _SHOTS,
    SHOT_MISSING_NOTE,
    SHOT_SRC,
    _shot_html,
    _shots,
    logger,
)
from tools.mobile.rfrm_wire import (  # noqa: F401
    MAX_RECTS,
    _place_elements,
    _rect_of,
    wireframe,
)
