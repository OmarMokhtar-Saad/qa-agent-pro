"""Constants shared by the adb leaves, one home each (moved from adb.py)."""

from __future__ import annotations

import re

#: Default per-call timeout. Deliberately short: a hung adb call must surface
#: as a failed check, not as a client timeout on the MCP boundary.
DEFAULT_TIMEOUT_S = 30

#: Above this, a single adb call's duration line becomes a WARNing instead of
#: a DEBUG one. The audit that motivated this module found a call buried
#: inside a 60s timeout with no signal until the whole thing finally failed
#: (qa_list_devices measured at 5515 ms); this is the line that would have
#: named it. The harmful direction is UPWARD, so this ceiling binds and no
#: floor test is owed.
SLOW_CALL_CEILING_S = 5.0

#: ``uiautomator dump`` writes a file and we then read it back; both halves are
#: slow on a cold emulator.
DUMP_TIMEOUT_S = 60

#: First attempt of a fallback dump (uiautomator waits for the screen to idle);
#: a timeout is retried once with DUMP_TIMEOUT_S.
FIRST_DUMP_TIMEOUT_S = 20

#: The dump is attacker-influenced content. Cap it here, at the transport, as
#: well as in the Phase-2 parser: a 400MB "screen" must never reach memory.
MAX_DUMP_BYTES = 4 * 1024 * 1024

#: The PNG one screen capture may occupy. TWICE ``MAX_DUMP_BYTES``, because a
#: lossless full-resolution screen is bulkier than the XML that describes it,
#: and the constraint is a different one: this payload is base64-encoded into a single
#: MCP reply the client must hold in one message, so its real cost is ~4/3 of
#: this number, and it is paid on EVERY turn of a run rather than once. A cap
#: here is also the only thing between a device that answers `screencap` with
#: something enormous and this process's memory.
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024

#: The largest file ``install`` will hand to a device. A real APK is tens of
#: megabytes; the cap exists so a tester-supplied path to something enormous is
#: refused on the host rather than pushed at the device (and, for
#: ``session.start_install``, into a detached process that outlives the call).
MAX_APK_BYTES = 512 * 1024 * 1024

#: The ZIP local-file-header signature. An APK is a ZIP archive and its first
#: entry starts with these four bytes, so a renamed text file or image does not.
APK_MAGIC = b"PK\x03\x04"

#: PNG's own eight-byte signature. ``exec-out screencap -p`` writes the image to
#: STDOUT, so a device that refused instead writes TEXT there -- and the same
#: "quote the device rather than guess" rule ``uiautomator_dump`` follows applies:
#: without this check an error string would be attached to the tester's chat as a
#: broken image while the packet's note claimed a picture was there.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: Where the dump is staged on the device.
DUMP_REMOTE_PATH = "/sdcard/qa-agents-dump.xml"

#: Android keyevent names/numbers we allow. An allowlist rather than a regex
#: over anything, because a keyevent argument reaches ``input`` unquoted.
KEYEVENT_RE = re.compile(r"^(?:[0-9]{1,3}|KEYCODE_[A-Z0-9_]{1,40})$")

#: Schemes ``open_url`` may hand to the device's VIEW intent. ``market:`` is
#: how the Play Store (and therefore Firebase App Tester) is opened.
ALLOWED_URL_SCHEMES = ("https", "http", "market")

#: The only characters a URL handed to ``open_url`` may contain: no whitespace
#: of any kind and nothing the device shell reads as syntax (quotes, < > \ ( )
#: * { } ; & | $ and backtick). An allowlist, because ``am start -d`` text goes
#: through the device's ``sh`` and a blacklist always misses one.
SAFE_URL_RE = re.compile(r"^[A-Za-z0-9._~:/?#=%+@,-]{1,2000}$")

_SWIPE_MAX = 20000
