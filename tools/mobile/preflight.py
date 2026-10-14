"""Nothing runs until every gate is green -- and the tester is told about ALL of them.

The single most important property of this module is that it does **not**
short-circuit. A tester whose emulator is not booted AND whose app is not
installed AND whose IME is not selected must learn all three from one call:
reporting them one per round trip turns a two-minute setup into three
conversations, and it is the reason the reference project's preflight exists at
all. Every check therefore runs in its own ``try`` and appends its own record,
and the summary ``ok`` is computed from the collected list afterwards.

Each record is ``{name, ok, detail, fix}``. ``fix`` is the sentence a
non-technical tester can act on; a check that cannot say how to fix itself is
not worth failing on.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging

from tools import host_privileges  # noqa: F401
from tools.device_manager import valid_package_name  # noqa: F401
from tools.mobile import (  # noqa: F401
    adb,
    downloader,
    emulator,
    ime,
    paths,
    platform_info,
)
from tools.mobile.preflight_adb import (  # noqa: F401
    _adb_listed,
    _path_row,
    _step_adb_first_on_path,
    _step_adb_responds,
    _step_virtualization,
    _virtualization_row,
)
from tools.mobile.preflight_boot import (  # noqa: F401
    _ambiguous_row,
    _boot_row,
    _choose_serial,
    _no_emulator_row,
    _step_emulator_booted,
)
from tools.mobile.preflight_consts import (  # noqa: F401
    _STEP_ERRORS,
    ADVISORY_CHECKS,
    CHECK_NAMES,
    RUN_FREE_BYTES,
    TYPING_BLOCKING_CHECKS,
)
from tools.mobile.preflight_dns import (  # noqa: F401
    _device_dns_row,
    _step_device_dns,
    dns_probe,
)
from tools.mobile.preflight_dns_consts import (  # noqa: F401
    DNS_DEVICE_FIX,
    DNS_EMULATOR_FIX,
    DNS_NO_ROUTE_FIX,
    DNS_PING_WAIT_S,
    DNS_PROBE_HOST,
    DNS_PROBE_IP,
    DNS_PROBE_TIMEOUT_S,
    DNS_SLOW_NOTE,
)
from tools.mobile.preflight_dns_fail import (  # noqa: F401
    _can_route,
    _dns_failure_detail,
    _dns_failure_record,
    _dns_fix,
    _failed_detail,
    _overran_detail,
)
from tools.mobile.preflight_host import (  # noqa: F401
    _disk_row,
    _ownership_row,
    _privilege_row,
    _step_cache_ownership,
    _step_free_disk,
    _step_host_privileges,
)
from tools.mobile.preflight_ime import (  # noqa: F401
    _not_pinned_rows,
    _oracle_row,
    _step_ime_oracle,
    _step_ime_pinned,
    _step_ime_probes,
)
from tools.mobile.preflight_ime_present import (  # noqa: F401
    _installed_row,
    _selected_row,
    _step_ime_installed,
    _step_ime_selected,
)
from tools.mobile.preflight_markdown import (  # noqa: F401
    _advisory_failure,
    _mark,
    _record_lines,
    _sort_key,
    render,
)
from tools.mobile.preflight_package import (  # noqa: F401
    _package_refusal,
    _package_row,
    _step_package_installed,
)
from tools.mobile.preflight_rows import (  # noqa: F401
    _exit_code,
    _failed,
    _mark_typing_blocking,
    _unanswered,
)
from tools.mobile.preflight_rows import (  # noqa: F401
    record_row as _record,
)
from tools.mobile.preflight_run import (  # noqa: F401
    _blocking_names,
    _cancel_unread,
    _early_ime,
    _run_device_steps,
    _run_probe_steps,
    _run_steps,
    _start_early_probes,
    _summarise,
    check,
)

logger = logging.getLogger("tools.mobile.preflight")
