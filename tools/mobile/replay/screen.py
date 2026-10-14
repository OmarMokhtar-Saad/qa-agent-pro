"""Replay engine: screen identity, package and text predicates, verification stamps. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from tools.mobile.replay.screen_has import (  # noqa: F401
    _DIGITS_RE,
    _digit_shape,
    _has_verification,
    _is_furniture,
    _screen_has,
    _screen_has_element,
    _typed_by_script,
    _verification_summary,
    _verified,
)
from tools.mobile.replay.screen_ident import (  # noqa: F401
    EntrySpec,
    _entry,
    _screen_hash,
    _screen_id,
    _screen_package,
    _stamp_after,
    _texts,
    _wall_clock,
)
from tools.mobile.replay.screen_pkg import (  # noqa: F401
    DEVICE_ERROR_OUTCOMES,
    LAUNCHER_PACKAGES,
    LEAVES_ON_PURPOSE,
    PLATFORM_PACKAGES,
    SYSTEM_DIALOG_PACKAGES,
    _platform_detail,
    left_app_detail,
    system_dialog_detail,
    system_dialog_package,
)
