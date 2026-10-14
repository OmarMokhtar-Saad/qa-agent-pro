"""Replay engine: destructive-tap and screen-sentinel guards and their detail strings. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from tools.mobile.replay.guard_confirm import (  # noqa: F401
    ConfirmArgs,
    ConfirmHint,
    _confirmed,
    _consume_confirm,
    _guard_node,
    _matches_prior,
    _with_confirm_hint,
)
from tools.mobile.replay.guard_consts import (  # noqa: F401
    CONFIRM_HINT,
    CONFIRM_MISMATCH_HINT,
    GUARD_DETAIL,
    GUARD_DETAIL_CONTROL_NOT_READ,
    GUARD_DETAIL_NOT_FULLY_SEEN,
    GUARD_DETAIL_REFUSED,
    GUARD_DETAIL_SCREEN_GENERIC,
    MAX_GUARD_NODE_CHARS,
    NEW_ELEMENT_KINDS,
    NO_FABRICATION_NOTE,
    NON_ACTUATING_OPS,
    NOTHING_MOVED_NOTE,
    NOTHING_MOVED_PREFIX,
    SCREEN_CONTROL_NOT_READ,
    SCREEN_NOT_FULLY_SEEN,
    SCREEN_NOT_THE_APP,
)
from tools.mobile.replay.guard_lex import (  # noqa: F401
    DESTRUCTIVE_LEXICON,
    _asserts_new_element,
    _control_detail,
    destructive_hit,
    inert_ops,
    is_destructive,
    not_the_app_detail,
)
from tools.mobile.replay.guard_screen import (  # noqa: F401
    _unrecognised_guard_term,
    guard_detail,
    logger,
    screen_hit,
    screen_sentinels,
    screen_stop_details,
)
