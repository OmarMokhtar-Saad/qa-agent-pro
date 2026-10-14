"""Replay engine: device operations (tap, type, press, scroll, keyboard, launch). Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging

from tools.mobile.replay.device_ops_basic import (  # noqa: F401
    _clear_app_data_op,
    _launch_named,
    _launch_op,
    _press_op,
    _scroll_op,
    _tap_op,
)
from tools.mobile.replay.device_ops_consts import (  # noqa: F401
    _SYSTEM_KEY_OPS,
    KEYBOARD_NOTE,
    KEYBOARD_OPS,
    PASSWORD_REFUSAL,
    PASSWORD_REFUSAL_STATUS,
    QA_IME_NOT_ACTIVE,
    QA_IME_SEEN_CAP,
)
from tools.mobile.replay.device_ops_ime import _qa_ime_blocker, _typer  # noqa: F401
from tools.mobile.replay.device_ops_judge import (  # noqa: F401
    _judge_landed,
    _landed_no_reply,
    _recovery_text,
    _unconfirmed_type_reply,
)
from tools.mobile.replay.device_ops_keyboard import (  # noqa: F401
    _keyboard_arm_and_probe,
    _keyboard_failure,
    _keyboard_install,
    _keyboard_select,
    keyboard_down,
    keyboard_up,
    needs_keyboard,
)
from tools.mobile.replay.device_ops_perform import (  # noqa: F401
    _dispatch_touch,
    _perform,
)
from tools.mobile.replay.device_ops_text import (  # noqa: F401
    _clear_refusal,
    _focus_field,
    _no_secret_held,
    _password_refusal,
    _text_entry_op,
    _type_plain,
    _type_secret,
)

logger = logging.getLogger("tools.mobile.executor")
