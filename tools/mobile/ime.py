"""The QA input method: install, select, restore, and the state oracle.

Three properties are load-bearing and each is pinned by a test.

**1. A secret never reaches argv.** ``type_text(..., TypeOpts(secret=True))`` base64-encodes
the text in Python and hands the whole ``am broadcast`` command line to
``adb shell`` on **stdin**. Command lines are world-readable on every OS this
lane supports (``ps``, Windows' process list, and adb's own logging), so a
password passed as an argument is a password disclosed. The base64 payload is
additionally checked against the base64 alphabet before it is sent, so no
crafted text can close the quote it sits in.

**2. The previous input method is restored, whatever it was.** On the machine
this phase was measured on, the emulator's default IME belongs to a THIRD-PARTY
package that has nothing to do with qa-agents. Restore therefore reads and
replays whatever was there -- it never assumes ours was the pre-existing one,
and an absent previous value is reported rather than papered over.

**3. An unpinned asset refuses BY NAME.** ``tools/mobile/ime_manifest.py`` pins
the published qa-ime release asset URL and its SHA-256. :func:`manifest`
imports it by name at call time; if it is missing or incomplete (a stripped
build, a deleted file) :func:`manifest` says so, with the fix, and every
function that needs the APK refuses the same way. Nothing here carries a
placeholder hash: a hash nobody published is not a weaker verification, it is
the absence of one.
"""

from __future__ import annotations

import base64  # noqa: F401
import importlib  # noqa: F401
import logging
import re  # noqa: F401
import sys
from pathlib import Path  # noqa: F401

from tools.mobile import adb, downloader, ime_host, ime_nonce, paths  # noqa: F401
from tools.mobile.ime_apk import (  # noqa: F401
    apk_cache_path,
    ensure_apk,
    install,
    installed,
)
from tools.mobile.ime_arm import (  # noqa: F401
    _arm_outcome,
    _still_ours,
    arm,
    disarm,
)
from tools.mobile.ime_component import display_id, same_component  # noqa: F401
from tools.mobile.ime_consts import (  # noqa: F401
    _B64_RE,
    _DATA_RE,
    _IME_ID_RE,
    _IME_REFUSAL_RE,
    _INPUT_UNSAFE_RE,
    _PM_PATH_RE,
    _RESULT_RE,
    _SEPARATORS,
    _SHA256_TOKEN_RE,
    ALREADY_ARMED,
    IME_ID_DISPLAY_CHARS,
    IME_NOT_LISTED,
    LANDED_NO,
    LANDED_UNKNOWN,
    LANDED_YES,
    MAX_TEXT_CHARS,
    NO_FOCUSED_FIELD,
    NO_RECEIVER,
    NOT_PINNED,
    NOT_PINNED_FIX,
    UNEXPECTED_ARM_REPLY,
    TypeOpts,
)
from tools.mobile.ime_current import current_ime, remember_previous  # noqa: F401
from tools.mobile.ime_fallback import (  # noqa: F401
    _unsafe_input_refusal,
    fallback_notice,
    type_via_input,
)
from tools.mobile.ime_landed import (  # noqa: F401
    Seen,
    _could_be_transform,
    _landed_verdict,
    _secret_landed_verdict,
)
from tools.mobile.ime_listing import _list_ime_ids, resolve_installed_id  # noqa: F401
from tools.mobile.ime_pin import (  # noqa: F401
    MANIFEST_MODULE,
    NOT_PINNED_DETAIL,
    _manifest_actions,
    manifest,
)
from tools.mobile.ime_query import (  # noqa: F401
    _field_snapshot,
    _focused_field,
    _undelivered,
    clear,
    probe,
    query,
)
from tools.mobile.ime_reply import (  # noqa: F401
    _decode_field_text,
    _parse_reply,
    _refusal_of,
    _reply_code,
    _reply_fields,
    reply_of,
)
from tools.mobile.ime_select import enable, restore_previous, select  # noqa: F401
from tools.mobile.ime_send import (  # noqa: F401
    SendReq,
    _broadcast_line,
    _send,
    broadcast,
    send,
)
from tools.mobile.ime_status import manifest_status  # noqa: F401
from tools.mobile.ime_type import (  # noqa: F401
    _commit_and_verify,
    _length_refusal,
    _TypeJob,
    type_text,
)
from tools.mobile.ime_verify import (  # noqa: F401
    _installed_path,
    _shell_out,
    _verdict,
    is_foreign_build,
    verify_pinned,
)
from tools.untrusted import wrap_untrusted  # noqa: F401

ime_host.bind(sys.modules[__name__])

logger = logging.getLogger("tools.mobile.ime")
