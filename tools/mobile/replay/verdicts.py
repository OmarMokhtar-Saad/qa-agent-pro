"""Replay engine: budget, route and assert verdicts. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging

from tools.mobile.replay.verdicts_assert import (  # noqa: F401
    AssertView,
    _assert_element,
    _assert_new_text,
    _assert_screen_changed,
    _assert_text,
    _evaluate_assert,
    _new_text_added,
)
from tools.mobile.replay.verdicts_budget import (  # noqa: F401
    QueueTail,
    _budget_seconds,
    _route_budget_stop_reason,
    _serialize_queued,
    budget_stop_reason,
)
from tools.mobile.replay.verdicts_consts import (  # noqa: F401
    BROUGHT_TO_FRONT,
    MAX_VISUAL_DETAIL_CHARS,
    ROUTE_MISMATCH_DETAIL,
    SCROLL_TARGET_MISSED,
)
from tools.mobile.replay.verdicts_front import Landing, _brought_to_front  # noqa: F401
from tools.mobile.replay.verdicts_route import (  # noqa: F401
    _actuated,
    _route_expect_sentinel,
    _route_expected,
)

logger = logging.getLogger("tools.mobile.executor")
