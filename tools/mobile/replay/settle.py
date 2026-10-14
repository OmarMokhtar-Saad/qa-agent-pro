"""Replay engine: auto-settle and system-dialog handling. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from tools.mobile.replay.context_consts import DIALOG_SETTLE_S  # noqa: F401
from tools.mobile.replay.settle_auto import _auto_settle  # noqa: F401
from tools.mobile.replay.settle_cut import (  # noqa: F401
    Cut,
    CutView,
    _cut_detail,
    _cut_of,
    _expected_here,
)
from tools.mobile.replay.settle_dialog import (  # noqa: F401
    DialogTarget,
    _answer_dialogs,
    _answer_one_dialog,
    _dialog_element,
    _dialog_tap_refusal,
    logger,
)
from tools.mobile.replay.settle_mark import (  # noqa: F401
    ScreenMark,
    SettleJob,
    SettleOpts,
    _unchanged,
)
from tools.mobile.replay.settle_run import (  # noqa: F401
    _cut_stop,
    _settle,
    _settle_redump,
    _settle_unchanged,
)
