"""Install failures in plain words: what failed, what it means, what to do.

``adb install`` and the on-device installer both report a machine code
(``INSTALL_FAILED_*``) buried in a longer line. This module pulls the code out,
keeps the whole cleaned message, and maps each known code to guidance a tester
can act on.

It only DESCRIBES. A failure that would need an uninstall (signing mismatch)
sets ``offers_uninstall``; the CALLER asks the tester through
``user_confirm.confirm`` and only a ``UserConsent`` lets anything uninstall. The
model-supplied ``confirm_destructive`` argument is not consent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.mobile.install_source import InstallOutcome

#: Longest failure text kept. adb prints one ``Failure [...]`` line; the old cut
#: at 400 characters could land inside the code itself, so the code is read from
#: the whole text and only the message is clipped.
MAX_INSTALL_MESSAGE_CHARS = 600

UPDATE_INCOMPATIBLE = "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
VERSION_DOWNGRADE = "INSTALL_FAILED_VERSION_DOWNGRADE"
INSUFFICIENT_STORAGE = "INSTALL_FAILED_INSUFFICIENT_STORAGE"
ALREADY_EXISTS = "INSTALL_FAILED_ALREADY_EXISTS"
USER_RESTRICTED = "INSTALL_FAILED_USER_RESTRICTED"
TEST_ONLY = "INSTALL_FAILED_TEST_ONLY"
OLDER_SDK = "INSTALL_FAILED_OLDER_SDK"
MISSING_SHARED_LIBRARY = "INSTALL_FAILED_MISSING_SHARED_LIBRARY"

_CODE_RE = re.compile(r"\bINSTALL_[A-Z_]*FAILED[A-Z0-9_]*\b")
_NOISE = (
    "performing streamed install",
    "performing incremental install",
    "performing push install",
)
_PREFIX = "adb install failed: "

_GUIDANCE = {
    UPDATE_INCOMPATIBLE: (
        "The installed copy was signed with a different key than this build, so it "
        "cannot be updated in place. Uninstalling it would delete its data: do not "
        "do it yourself; ask the tester whether to uninstall and reinstall."
    ),
    VERSION_DOWNGRADE: (
        "The device already has a newer build than the one offered. Ask the tester "
        "which build to install; a downgrade needs the app uninstalled first."
    ),
    INSUFFICIENT_STORAGE: (
        "The device is out of storage. Ask the tester to free some space, then retry."
    ),
    ALREADY_EXISTS: (
        "A copy of this package is already installed and could not be replaced."
    ),
    USER_RESTRICTED: (
        "This device user is not allowed to install apps (a restriction or a work "
        "profile policy). Ask the tester to lift it or use another user."
    ),
    TEST_ONLY: (
        "This build is marked test-only and the installer refused it. A debug-"
        "installable build is needed."
    ),
    OLDER_SDK: (
        "This build needs a newer Android version than the device runs. Use a "
        "build that supports this device or a newer device."
    ),
    MISSING_SHARED_LIBRARY: (
        "This build needs a shared library the device does not have. Use a build "
        "made for this device."
    ),
}

#: Phrases the on-device installer shows instead of the code.
_DIALOG_PHRASES = (
    ("conflicts with an existing package", UPDATE_INCOMPATIBLE),
    ("signatures do not match", UPDATE_INCOMPATIBLE),
    ("newer version", VERSION_DOWNGRADE),
    ("not enough storage", INSUFFICIENT_STORAGE),
    ("insufficient storage", INSUFFICIENT_STORAGE),
)


@dataclass(frozen=True)
class InstallFailure:
    code: str
    guidance: str
    offers_uninstall: bool = False


@dataclass(frozen=True)
class ParsedFailure:
    code: str
    message: str


def clean_failure_text(text: object) -> str:
    """The failure text on one line, without adb progress noise."""
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    kept = [line for line in lines if line and not line.lower().startswith(_NOISE)]
    return " ".join(kept)


def parse_failure(text: object) -> ParsedFailure:
    """``(code, message)`` from ``adb install`` output; the code may be empty."""
    cleaned = clean_failure_text(text)
    found = _CODE_RE.search(cleaned)
    body = cleaned[:MAX_INSTALL_MESSAGE_CHARS] or "no output from adb"
    return ParsedFailure(found.group(0) if found else "", _PREFIX + body)


def _known_code(code: object) -> str:
    key = str(code or "").strip().upper()
    return key if _CODE_RE.fullmatch(key) else ""


def _offers_uninstall(code: str, message: str) -> bool:
    if code == UPDATE_INCOMPATIBLE:
        return True
    return code == ALREADY_EXISTS and "signature" in message.lower()


def explain(code: object, message: str = "") -> InstallFailure:
    """Guidance for ``code``. Text that is not a code is never quoted back."""
    key = _known_code(code)
    guidance = _GUIDANCE.get(key)
    if guidance is None:
        guidance = (
            "The install failed with "
            + (key or "an unknown error")
            + ". Nothing was changed; ask the tester how to proceed."
        )
    return InstallFailure(key, guidance, _offers_uninstall(key, message))


def code_from_dialog_text(text: object) -> str:
    """The code behind an installer dialog message, or an empty string."""
    lowered = " ".join(str(text or "").lower().split())
    for phrase, code in _DIALOG_PHRASES:
        if phrase in lowered:
            return code
    return ""


def to_outcome(failure: InstallFailure, message: str = "") -> InstallOutcome:
    """An InstallOutcome; ``needs_confirmation`` is set only for a signing mismatch."""
    note = (message + " " if message else "") + failure.guidance
    confirm = "uninstall" if failure.offers_uninstall else ""
    return InstallOutcome(False, failure.code, note, None, confirm)
