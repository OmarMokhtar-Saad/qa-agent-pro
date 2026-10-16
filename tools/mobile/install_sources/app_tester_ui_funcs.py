"""App Tester UI: the pure helpers that read screens and build outcomes."""

from __future__ import annotations

from typing import Optional

from tools.device_manager import valid_package_name
from tools.mobile.app_info import AppVersion
from tools.mobile.install_errors import code_from_dialog_text, explain, to_outcome
from tools.mobile.install_source import InstallOutcome, InstallRequest, Release
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import (
    _PROGRESS_STARTS,
    _VERSION_RE,
    OPEN_LABEL,
)
from tools.mobile.resolve import MAX_ASK_OPTIONS, Resolution
from tools.untrusted import wrap_untrusted

#: Characters of the App Tester row list quoted in a not-found reply.
MAX_ROWS_SHOWN_CHARS = 1500


def parse_releases(texts) -> list:
    """Releases named by version texts such as ``2.0 (9)``, oldest first."""
    found = []
    seen = set()
    for text in sorted(texts):
        match = _VERSION_RE.search(text)
        if match and text not in seen:
            seen.add(text)
            found.append(Release(text, "", match.group(1), int(match.group(2))))
    return sorted(found, key=lambda rel: rel.version_code or 0)


def _in_progress(texts) -> bool:
    return any(text.lower().startswith(_PROGRESS_STARTS) for text in texts)


def _improved(
    before: Optional[AppVersion], after: Optional[AppVersion], target: Optional[int]
) -> bool:
    code = after.version_code if after else None
    if code is None:
        return False
    if target is not None:
        return code >= target
    prior = before.version_code if before else None
    return prior is None or code > prior


def _already_current(
    before: Optional[AppVersion], request: InstallRequest
) -> Optional[InstallOutcome]:
    target = request.target_version_code
    if target is None and request.latest:  # the newest build the page last listed
        target = memory.known_latest(request.serial, request.package)
    code = before.version_code if before else None
    if target is None or code is None or code < target:
        return None
    message = "Already at versionCode " + str(code) + " (target " + str(target) + ")."
    return InstallOutcome(True, "", message + " Nothing to install.", code)


def _refuse_request(request: InstallRequest) -> Optional[InstallOutcome]:
    if not valid_package_name(request.package):
        return InstallOutcome(
            False,
            "bad_package",
            "Not a valid package id: "
            + repr(str(request.package)[:60])
            + ". Ask the tester for the exact package.",
        )
    return None


def _label_required() -> InstallOutcome:
    return InstallOutcome(
        False,
        "label_required",
        "App Tester does not show this package on screen and lists apps by display "
        "name. Ask the tester what the app is called in App Tester (for example "
        "Acme QA), then re-call with app=<name>.",
    )


def _quoted(texts) -> str:
    return wrap_untrusted("app tester screen", " | ".join(sorted(texts)), limit=300)


def _unresolved_message(got: Resolution) -> str:
    shown = "\n".join(cand.display() for cand in got.options[:MAX_ASK_OPTIONS])
    return (
        got.question
        + "\n"
        + wrap_untrusted("app tester options", shown)
        + "\nAsk the user which one they mean; do not guess."
    )


def _not_on_list() -> InstallOutcome:
    return InstallOutcome(
        False,
        "not_on_list",
        "App Tester did not reach its app list after going back. Ask the tester "
        "to open App Tester, sign in if asked, and leave it on the app list.",
    )


def _rows_not_found(label: str, rows) -> InstallOutcome:
    shown = "\n".join(row.name + " (" + row.bundle + ")" for row in rows)
    return InstallOutcome(
        False,
        "app_not_found",
        "No app in App Tester has every word of "
        + repr(label[:80])
        + " in its name or bundle id.\n"
        + wrap_untrusted("app tester options", shown, limit=MAX_ROWS_SHOWN_CHARS)
        + "\nAsk the user which one they mean; do not guess.",
    )


def _aab_setup() -> InstallOutcome:
    return InstallOutcome(
        False,
        "aab_setup_required",
        "App Tester asks for its one-time setup to download AAB releases (Get "
        "started). This is the tester's step: ask them to finish it on the "
        "phone, then call `qa_mobile_test` again. Nothing was installed.",
    )


def _tap_failed(text: str) -> InstallOutcome:
    return InstallOutcome(
        False,
        "tap_failed",
        "Could not tap " + repr(str(text)[:80]) + " on the App Tester screen.",
    )


def _explicit_release(request: InstallRequest, releases: list) -> Optional[str]:
    if request.release is not None:
        return request.release.key
    target = request.target_version_code
    if target is None:
        return None
    hits = [rel.key for rel in releases if rel.version_code == target]
    return hits[0] if len(hits) == 1 else None


def _no_action(texts) -> InstallOutcome:
    if OPEN_LABEL in texts:
        return InstallOutcome(
            False,
            "no_update_offered",
            "App Tester shows Open and no Update or Download: the installed "
            "build may already be the latest.",
        )
    return InstallOutcome(
        False,
        "unrecognised_screen",
        "The App Tester page has no Update, Download or Install button. "
        + _quoted(texts)
        + " Nothing was tapped.",
    )


def _foreign_stop(front: str, texts) -> InstallOutcome:
    joined = " ".join(sorted(texts))
    code = code_from_dialog_text(joined)
    if code:
        note = "The installer stopped: " + _quoted(texts)
        return to_outcome(explain(code, joined), note)
    return InstallOutcome(
        False,
        "unrecognised_screen",
        "Stopped on a screen of "
        + (front or "an app that could not be read")
        + " that no dialog handler knows. "
        + _quoted(texts)
        + " Nothing was tapped.",
    )
