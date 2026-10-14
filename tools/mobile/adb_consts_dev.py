"""Device-facing regexes and tuning constants for the adb leaves (moved from adb.py)."""

from __future__ import annotations

import re

#: A locale tag this lane will SET on a device: a language subtag, optionally a
#: region. Narrow on purpose -- ``setprop`` accepts any string, and a device given
#: a tag no resources exist for renders in its default language while every
#: reader believes it was configured.
LOCALE_TAG = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

#: The property that survives a reboot. NOT the property that says what the
#: screen is rendering in right now -- see :func:`runtime_locale`.
PERSIST_LOCALE_PROP = "persist.sys.locale"

#: A resolved launcher component, ``<package>/<class>``. The class may be
#: dotted, relative (``.Main``) or an inner class (``Outer$Inner``); nothing
#: else, because the string goes straight into an ``am start -n`` argument.
_COMPONENT_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)/([A-Za-z0-9_.$]{1,200})$"
)

#: Timeout for the ``am start`` / ``monkey`` that starts an app. Without
#: ``-W`` the command returns once the intent is delivered -- well under a
#: second on a healthy device -- so this bounds a wedged adbd, not a slow app.
#: It was 60 with ``-W``, which on the Air's overloaded s25_ultra AVD was the
#: 60 s stall in the run start (fix round 2, item 2). Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
LAUNCH_START_TIMEOUT_S = 10

#: How long ``launch`` then polls the focused window for the app. NON-GATING:
#: running out is not an error, because callers that need the app up
#: (``case_runner.start_case``) already poll with their own bound, and the rest
#: settle on the next dump. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
LAUNCH_FOREGROUND_TIMEOUT_S = 8.0

#: Gap between two focus reads in that poll. Matches
#: ``case_runner.FOREGROUND_POLL_S``.
LAUNCH_FOREGROUND_POLL_S = 0.5

#: Timeout for ``pm list packages``: ~0.2 s on the Air's device, so 60 was a
#: minute of silence on a wedged device rather than a bound. Bounded from above
#: in ``tests/mobile/test_mobile_bounds_upper.py``.
PM_LIST_TIMEOUT_S = 30

#: RAW frame bytes one ``screencap`` without ``-p`` may return. A raw frame
#: is 4 bytes per pixel, uncompressed, so it is far larger than the PNG
#: :data:`MAX_SCREENSHOT_BYTES` bounds: a 1440x3120 panel is 17.9 MB. Read only
#: on a heavy panel (``screenshot.capture``). Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_RAW_SCREENSHOT_BYTES = 64 * 1024 * 1024

#: Bit rate for ``screenrecord``. Not a cap and not a bound: it is a quality
#: choice, and the SIZE of what it produces is bounded by
#: ``media.MAX_CLIP_BYTES`` where the file is kept or discarded.
SCREENRECORD_BITRATE = "2000000"

#: The recording SIZE, passed explicitly because the encoder refuses the
#: device's own. MEASURED on emulator-5554 (sdk_gphone64_arm64, API 35,
#: `wm size` 1440x3120) on 2026-09-10: every default-size recording printed
#: "ERROR: unable to configure video/avc codec at 1440x3120 (err=-22)" and
#: then "WARNING: failed at 1440x3120, retrying at 720x1280", so the run
#: shipped an ERROR line in its log and a resolution nobody chose. Passing
#: this size explicitly produced no warning at all.
#:
#: It is a CHOSEN DOWNSCALE, not a cap and not the device's resolution: the
#: clip shows what happened, not how sharp the app was, and the report says
#: so beside every clip (``report.CLIP_SCALE_NOTE``). One producer, one
#: meaning: this constant is the only place the recorded size is decided,
#: and the note names it rather than restating a number.
SCREENRECORD_SIZE = "540x1170"

#: Android's ``ime`` command can answer a refusal on stdout at rc 0 ("Unknown
#: input method <id>"), so an rc check alone is not enough -- mirrors
#: `adb.install`'s established rc-plus-text pattern.
_IME_REFUSAL_RE = re.compile(
    r"unknown input method|cannot be (?:enabled|set)|error", re.IGNORECASE
)

#: The shape ``ime set`` accepts from this lane: ``package/Class``.
IME_ID_SHAPE = re.compile(r"^[A-Za-z0-9._]{1,120}/[A-Za-z0-9._$]{1,120}$")

#: An on-device path this module will read for the run's own package: absolute,
#: a bounded charset, and no `..` segment. A profile or a listing can only ever
#: hand this module something that matches.
_DEVICE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]{1,200}$")

#: A logcat tag as `-s` accepts it.
_LOGCAT_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

_EPOCH_MS_RE = re.compile(r"^\d{13}$")

#: What a run on a physical device is told, BY NAME. The lane supports a real
#: phone; the emulator console does not exist there, so a capture is IMPOSSIBLE
#: rather than empty -- and an empty section reads to a tester as "the app
#: called nothing", which is the false claim this constant exists to prevent.
NOT_AN_EMULATOR = (
    "network capture needs an emulator; this run is on a physical device, so "
    "the app network traffic was not recorded (its logcat still was)"
)

#: One word of a console command line. Deliberately narrower than the console
#: itself accepts: every argument sent here is a literal in this tree or a file
#: name this tree composed, so nothing needs a space, a quote or a newline.
_EMU_WORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

_BARE_PCAP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.pcap$")

#: `adb reverse`'s own `<protocol>:<port>` shape. Bounded to five digits so a
#: caller cannot hand this an arbitrarily long numeral; the callers in this
#: tree only ever compose this from a port `proxy.py` chose itself, never
#: from anything device-supplied.
_REVERSE_SPEC_RE = re.compile(r"^tcp:[0-9]{1,5}$")

#: A `settings` namespace's property name. Same discipline as `getprop`'s.
_SETTING_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

#: A `settings put` value. Narrow on purpose: every caller in this tree sends
#: either a `host:port` pair or `:0`, never free text, so a bounded charset
#: with no shell-significant character is both sufficient and safe.
_SETTING_VALUE_RE = re.compile(r"^[A-Za-z0-9:._+-]{1,120}$")
