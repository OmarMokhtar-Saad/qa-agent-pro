"""Constants shared by the ``ime_*`` leaves: one home for every scalar here.

Each leaf that logs binds ``logging.getLogger("tools.mobile.ime")`` itself (the
adb_* precedent): the facade's logger name is unchanged and ruff still sees the
logger, so ``logger.exception`` handlers are not counted as BLE001.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class TypeOpts(NamedTuple):
    """How one type op is delivered: secret text, and a known receiver."""

    secret: bool = False
    receiver_known: bool = False


#: Reason code callers (and preflight) branch on.
NOT_PINNED = "ime_not_pinned"

NOT_PINNED_FIX = (
    "Restore `tools/mobile/ime_manifest.py` from the release this install "
    "came from (it pins the qa-ime release asset URL and its SHA-256), or "
    "reinstall. No change is needed in tools/mobile/ime.py: "
    "it resolves that module by name at call time, so this check starts "
    "passing the moment the module lands."
)

#: Standard base64 alphabet, anchored. A payload that does not match is not sent.
_B64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")

#: Cap on a single typed string. Longer than any credential and short enough
#: that a broadcast command line stays inside every OS's argument limits.
MAX_TEXT_CHARS = 4000

#: ``result=`` / ``data="..."`` in an ``am broadcast`` reply.
_RESULT_RE = re.compile(r"result=(-?\d+)")
_DATA_RE = re.compile(r'data="(.*)"', re.DOTALL)

#: Android's ``ime`` command can answer a refusal on stdout at rc 0 ("Unknown
#: input method <id>"), so an rc check alone is not enough for `enable`
#: either -- same phrases as adb.pm_select's own copy, declared separately so
#: neither module imports the other's private regex.
_IME_REFUSAL_RE = re.compile(
    r"unknown input method|cannot be (?:enabled|set)|error", re.IGNORECASE
)

#: How much of an input-method id a REPORT prints. Ids are `package/.Class` and
#: the package is a reverse domain, so an untruncated one pushes the fact a
#: tester needs -- installed, selected -- off the end of a narrow table cell.
#: DISPLAY ONLY: nothing compares, stores or selects a truncated id, and
#: `same_component` is always handed the full string.
IME_ID_DISPLAY_CHARS = 60

# `pm path` answers `package:<absolute path>`. That path goes into a device shell
# argv, so a hostile answer must not reach it: only this shape is accepted.
_PM_PATH_RE = re.compile(r"package:(/[A-Za-z0-9_.=~+@/-]{1,300})")
_SHA256_TOKEN_RE = re.compile(r"[0-9a-fA-F]{64}")

#: One id per line from ``ime list -a -s``; ``ime list -a`` prints one block
#: per input method, headed ``pkg/.Class:`` and carrying ``mId=pkg/.Class``.
_IME_ID_RE = re.compile(
    r"^\s*(?:mId=)?([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+):?(?=\s|$)", re.MULTILINE
)

#: The QA keyboard is installed but the device does not list it under the
#: pinned component, so ``ime enable``/``ime set`` would only be refused.
IME_NOT_LISTED = (
    "The QA keyboard (%s) is not among the input methods this device lists "
    "(%s), so it cannot be enabled or selected and nothing can be typed. Start "
    "the run again with apply=true so the server reinstalls the pinned QA "
    "keyboard; if it is still not listed, report this message as a defect."
)

#: A broadcast reply with no ``result=`` line: nothing received it.
NO_RECEIVER = (
    "The QA keyboard did not answer (no result= in the broadcast reply), so "
    "nothing was typed or cleared: it is not installed, enabled or selected."
)


NO_FOCUSED_FIELD = (
    "The QA keyboard answered that no field has input focus (result=0), so "
    "nothing was typed or cleared. Target the field by its visible label."
)

#: The three values of the ``landed`` verdict. THREE, not a bool, and the
#: reason is the middle one: a masked or formatted field (a phone or card
#: formatter, a password mask) legitimately holds something other than what
#: was sent, and calling that ``LANDED_NO`` would fail real runs on working
#: apps. ``LANDED_UNKNOWN`` is the honest answer there -- and the executor
#: treats it as a non-success too, so the ambiguity reaches the tester instead
#: of being resolved silently in our favour.
LANDED_YES = "yes"
LANDED_NO = "no"
LANDED_UNKNOWN = "unknown"

#: What a field's own formatter adds or drops: a phone mask's dashes and
#: parentheses, a currency field's spaces, a trailing space a trim ate, an
#: underscore. Stripped from BOTH sides before comparing, so ``123-456`` and
#: ``123456`` are the same text.
_SEPARATORS = re.compile(r"[\W_]+")

_INPUT_UNSAFE_RE = re.compile(r"[^\x20-\x7e]|%")

#: Said when ARM finds the keyboard already armed and this process holds no nonce.
ALREADY_ARMED = (
    "The QA keyboard is already armed by another session (a run that crashed "
    "leaves it armed until its idle timeout), so typing falls back to `adb "
    "shell input text`. Wait about five minutes or reselect the keyboard, then "
    "start again."
)

#: Said when ARM answers with anything that is neither the new APK's arm nor the silence
#: of 1.0.0. Such a keyboard is NOT ready: no unnonced broadcast is ever sent to it.
UNEXPECTED_ARM_REPLY = (
    "The QA keyboard gave an unexpected reply to its arming request, so it is treated "
    "as not ready and typing falls back to `adb shell input text` for this run"
)
