"""One place that builds ``adb`` argv, and the only place that can feed it stdin.

Every device identifier is re-validated with ``device_ids._valid_device_id``
and every package name with ``device_ids.valid_package_name`` before it
reaches a subprocess, so a client-controlled string can never smuggle an extra
argument. There is no shell anywhere in this module.

**Stated deviation from the phase spec.** The spec says "a thin async wrapper
over the ``device_manager._run`` shape". This module keeps that CONTRACT
(``(rc, stdout, stderr)``, kill on timeout) but does not call ``_run`` itself,
because ``_run`` cannot write to the child's stdin -- and stdin is exactly how
``ime.type_text`` must deliver a secret so it never appears in argv. Reusing a
runner that forces the payload onto the command line would have defeated the
one guarantee this phase exists to make. The validators ARE reused, which is
where the security value of that module actually sits.
"""

from __future__ import annotations

import asyncio  # noqa: F401  (tests patch adb.asyncio)
import logging
import shlex  # noqa: F401
import sys
import time  # noqa: F401  (host().time: tests patch adb.time)

from tools.device_ids import (
    _valid_device_id,  # noqa: F401
    valid_package_name,  # noqa: F401
)
from tools.mobile import adb_host, adb_run, platform_info, sdk_locator  # noqa: F401
from tools.mobile.adb_consts import (  # noqa: F401
    _PNG_MAGIC,
    _SWIPE_MAX,
    ALLOWED_URL_SCHEMES,
    APK_MAGIC,
    DEFAULT_TIMEOUT_S,
    DUMP_REMOTE_PATH,
    DUMP_TIMEOUT_S,
    KEYEVENT_RE,
    MAX_APK_BYTES,
    MAX_DUMP_BYTES,
    MAX_SCREENSHOT_BYTES,
    SAFE_URL_RE,
    SLOW_CALL_CEILING_S,
)
from tools.mobile.adb_consts_dev import (  # noqa: F401
    _BARE_PCAP_RE,
    _COMPONENT_RE,
    _DEVICE_PATH_RE,
    _EMU_WORD_RE,
    _EPOCH_MS_RE,
    _IME_REFUSAL_RE,
    _LOGCAT_TAG_RE,
    _REVERSE_SPEC_RE,
    _SETTING_NAME_RE,
    _SETTING_VALUE_RE,
    IME_ID_SHAPE,
    LAUNCH_FOREGROUND_POLL_S,
    LAUNCH_FOREGROUND_TIMEOUT_S,
    LAUNCH_START_TIMEOUT_S,
    LOCALE_TAG,
    MAX_RAW_SCREENSHOT_BYTES,
    NOT_AN_EMULATOR,
    PERSIST_LOCALE_PROP,
    PM_LIST_TIMEOUT_S,
    SCREENRECORD_BITRATE,
    SCREENRECORD_SIZE,
)
from tools.mobile.adb_density import display_density  # noqa: F401
from tools.mobile.adb_devices import (  # noqa: F401
    _devices_failed,
    _devices_truncated,
    devices,
)
from tools.mobile.adb_display import (  # noqa: F401
    _pick_display_size,
    _wm_lines,
    _wm_size_candidate,
    display_size,
)
from tools.mobile.adb_display_state import (  # noqa: F401
    _DENSITY_CACHE,
    _DENSITY_RE,
    _DISPLAY_CACHE,
    _DISPLAY_RE,
    DISPLAY_CACHE_TTL_S,
    MAX_DENSITY_DPI,
    MAX_DISPLAY_EDGE_PX,
    MIN_DENSITY_DPI,
    _cache_lookup,
)
from tools.mobile.adb_dump import (  # noqa: F401
    _DUMP_COMMAND,
    _DUMP_PROVIDERS,
    _dump_no_xml,
    _provider_dump,
    clear_dump_provider,
    set_dump_provider,
    uiautomator_dump,
)
from tools.mobile.adb_emu import (  # noqa: F401
    _emu_reply,
    _emu_words_refusal,
    emu,
    valid_capture_name,
)
from tools.mobile.adb_facts import current_activity, device_facts  # noqa: F401
from tools.mobile.adb_gone import (  # noqa: F401
    _DEVICE_GONE_RE,
    _device_gone_listeners,
    _notify_device_gone,
    device_gone_listeners,
    on_device_gone,
)
from tools.mobile.adb_input import (  # noqa: F401
    Swipe,
    _coord,
    is_ime_id,
    keyevent,
    open_url,
    pm_select,
    swipe,
    tap,
)
from tools.mobile.adb_install import apk_problem, install, uninstall  # noqa: F401
from tools.mobile.adb_launch import (  # noqa: F401
    _launch_component,
    _launch_monkey,
    force_stop,
    launch,
    wait_foreground,
)
from tools.mobile.adb_locale import (  # noqa: F401
    _tag_from_config,
    persisted_locale,
    runtime_locale,
    set_locale,
)
from tools.mobile.adb_logcat import (  # noqa: F401
    LogQuery,
    device_epoch_ms,
    logcat_clear,
    logcat_dump,
    pidof,
)
from tools.mobile.adb_media import pull, screenrecord_spawn  # noqa: F401
from tools.mobile.adb_pkg import (  # noqa: F401
    clear_app_data,
    installed_packages,
    resolve_launcher,
)
from tools.mobile.adb_props import device, getprop, shell  # noqa: F401
from tools.mobile.adb_proxy import (  # noqa: F401
    PortSpec,
    SettingPut,
    forward,
    global_setting_get,
    global_setting_put,
    reverse,
    reverse_remove,
)
from tools.mobile.adb_raw import raw  # noqa: F401
from tools.mobile.adb_reply import (  # noqa: F401
    _capped,
    _valid_device_path,
    clamp_timeout,
    rc_failure,
    refuse_device_id,
)
from tools.mobile.adb_run import (  # noqa: F401
    _cmd_for_log,
    _log_call_duration,
    resolve_adb,
)
from tools.mobile.adb_runas import (  # noqa: F401
    RunAsRead,
    _run_as_refused,
    run_as_cat,
    run_as_ls,
)
from tools.mobile.adb_screencap import (  # noqa: F401
    _screencap_timed_out,
    _screencap_verdict,
    screencap,
)
from tools.mobile.adb_screencap_raw import (  # noqa: F401
    _screencap_raw_verdict,
    screencap_raw,
)
from tools.mobile.adb_stdin import StdinFeed, _device_in, shell_in  # noqa: F401

logger = logging.getLogger("tools.mobile.adb")


async def _run_argv(cmd, timeout, stdin_data=None):
    """Delegating alias of :func:`adb_run.run_argv` (patch the public name)."""
    return await adb_run.run_argv(cmd, timeout, stdin_data)


adb_host.bind(sys.modules[__name__])
