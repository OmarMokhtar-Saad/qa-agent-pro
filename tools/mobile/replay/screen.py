"""Replay engine: screen identity, package and text predicates, verification stamps. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging
import re
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    perception,
)
from tools.mobile.replay.context import Context

logger = logging.getLogger("tools.mobile.executor")


#: Packages whose screen is an OVERLAY: the app under test is still running
#: underneath, so `back` dismisses this and the case continues. A permission
#: dialog is the one a mistaken tap produces most often -- on 2026-09-04 a tap
#: on a Voice mode control opened the microphone prompt, and the replay went on
#: tapping against it until the case ran out of escapes.
#: Re-exported from ``perception``, which is where the answer has to be taken:
#: the overlay's package is gone from the pruned elements by the time this
#: module sees a screen, because it is the same on every element of an ordinary
#: one. The set has ONE definition and this name is kept for its readers.
SYSTEM_DIALOG_PACKAGES: frozenset = perception.SYSTEM_DIALOG_PACKAGES

#: Launchers, named ONLY so the reason can say "the home screen" rather than a
#: package name a tester would have to look up. The left-the-app rule itself is
#: "a package that is not the one under test" and does not consult this set --
#: a list of launchers would be a list to keep up to date, and the rule must
#: hold for the ones nobody thought of.
LAUNCHER_PACKAGES: frozenset = frozenset(
    {
        "com.google.android.apps.nexuslauncher",
        "com.android.launcher",
        "com.android.launcher2",
        "com.android.launcher3",
        "com.sec.android.app.launcher",
    }
)

#: Ops that leave the app ON PURPOSE. Reporting one as an accident would stop a
#: script doing exactly what it asked to do.
LEAVES_ON_PURPOSE: frozenset = frozenset({"home", "open_url"})


#: The wall clock, bound at import. Tests replace `executor.time` with a fake
#: that carries only `monotonic` to drive the duration stamp; the absolute `at`
#: below must keep reading the REAL clock under that fake, and must stay the
#: same clock `run_store` stamps `started`/`updated` with.
_wall_clock = time.time


def _screen_hash(screen: object) -> str:
    """``perception.prune``'s CONTENT hash for a screen, or ``""``.

    A DIFFERENT value from ``screen_id`` and deliberately named apart: the id
    is package + activity + the top three texts, which is what makes a scrolled
    list "the same screen" -- and what made every chat reply ``no_change``. The
    hash is seeded from every element. Two questions, two names.
    """
    body = screen if isinstance(screen, dict) else {}
    return str(body.get("hash") or "")


def _stamp_after(entry: dict, screen: object) -> None:
    """Stamp BOTH halves of the after-observation, in one call.

    ``after_screen_id`` says WHICH screen the step ended on; ``after_screen_hash``
    says WHICH LOOK at it. The report joins a step to its stored picture on the
    PAIR -- `report._obs_key` needs both, and on a chat screen the id alone is
    ambiguous because one screen id covers every turn of the conversation.

    ONE function because the two fields are one fact recorded at one instant,
    and recording them separately is not a style difference: measured on the
    tree that shipped v1.87.0, the id was written at THIRTEEN sites and the hash
    at ONE, so twelve of thirteen steps rendered "not captured" while their PNG
    sat on disk. A caller that stamps only one half is the same defect returning,
    which `tests/mobile/test_mobile_after_stamp.py` now fails by name.
    """
    entry["after_screen_id"] = _screen_id(screen)
    entry["after_screen_hash"] = _screen_hash(screen)


def _entry(
    index: int, action: object, before: str, started: float, before_hash: str = ""
) -> dict:
    return {
        "index": int(index),
        "action": actions_mod.redact_action(action),
        "before_screen_id": str(before or ""),
        "after_screen_id": "",
        # The CONTENT hashes, beside the ids. ``after_screen_hash`` is stamped
        # only where the replay SETTLES, which is the one place that judges
        # ``no_change``; entries stamped elsewhere leave it empty, because
        # nothing reads it there and a half-filled field that looks general is
        # how a sentinel gets trusted for something it does not carry.
        "before_screen_hash": str(before_hash or ""),
        "after_screen_hash": "",
        # Stamped by ``_append``, AFTER the action ran. Computing it here read
        # the clock at creation, so every real run carried ``ms: 0`` -- a
        # 4000 ms wait included -- and the report could time nothing.
        "ms": 0,
        "_started": float(started),
        # Host wall clock at the moment the action BEGAN, the same clock the
        # case checkpoint's `started`/`updated` use, so app-log evidence can be
        # placed between actions by absolute time (plan mobile-app-evidence D6).
        "at": _wall_clock(),
        "outcome": "",
        "detail": "",
    }


#: The trace outcomes that mean THE DEVICE, not the app, ended the action.
#:
#: Defined here, beside the four sites that mint them, because this module invents
#: the vocabulary and ``report.error_classes`` only counts it. A set restated in the
#: report would be a second producer of the same meaning, and the drift would be
#: silent: a new outcome minted below would simply stop being counted, and the page
#: would show a smaller number with nothing to say it was smaller.
#:
#: ``tests/mobile/test_mobile_report_derivations.py`` scans THIS FILE for every
#: literal assigned to ``entry["outcome"]`` and requires the two to agree, so a name
#: added at a mint site and forgotten here fails by name on its first run.
DEVICE_ERROR_OUTCOMES: frozenset = frozenset(
    {"dump_failed", "device_error", "system_dialog", "left_app"}
)


def system_dialog_package(screen: object) -> str:
    """The system-dialog package on this screen, or ``""``.

    ANY element's, not the dominant one's. A permission prompt is a CARD over a
    full-screen app window, so it loses on area every time -- and keying the
    dialog rule on the screen's dominant package made it inert for exactly the
    packages it exists for. The dominant package answers a different question
    ("whose screen is this") and still answers it for the left-the-app rule.
    """
    if not isinstance(screen, dict):
        return ""
    named = str(screen.get("dialog_package") or "")
    if named in SYSTEM_DIALOG_PACKAGES:
        return named
    # The whole screen IS the dialog -- a full-screen permission prompt, or a
    # dump that held nothing else.
    whole = str(screen.get("package") or "")
    return whole if whole in SYSTEM_DIALOG_PACKAGES else ""


def _screen_package(screen: object) -> str:
    return str((screen or {}).get("package") or "") if isinstance(screen, dict) else ""


def system_dialog_detail(package: str) -> str:
    return (
        "a system dialog from "
        + str(package)[:80]
        + " is in front of the app, so nothing further was replayed against it. "
        "The app is still running underneath: answer the dialog, or send `back` "
        "to dismiss it, then continue the case."
    )


#: Windows that belong to the platform rather than to an app: an ANR ("X isn't
#: responding"), a crash dialog. They are `package="android"` and they are
#: DOMINANT, so they reach the left-app branch rather than the dialog one --
#: and `android` deliberately does not join SYSTEM_DIALOG_PACKAGES, because it
#: also appears on ordinary screens and the membership rule that keeps that set
#: safe is worth more than this one case.
#:
#: What has to change instead is the ADVICE. `launch` cannot clear an ANR.
PLATFORM_PACKAGES: frozenset = frozenset({"android"})


def left_app_detail(package: str, expected: str) -> str:
    if str(package) in PLATFORM_PACKAGES:
        return (
            "the screen now belongs to the Android platform ("
            + str(package)[:80]
            + ") rather than to "
            + str(expected)[:80]
            + ' -- this is usually a system dialog such as "isn\'t responding" '
            "in front of the app, so nothing further was replayed. Answer it "
            "using the buttons on the screen above, or send `back`. Do NOT send "
            "`launch`: it does not clear a dialog like this."
        )
    where = (
        "the home screen (" + str(package)[:80] + ")"
        if str(package) in LAUNCHER_PACKAGES
        else "another app (" + str(package)[:80] + ")"
    )
    return (
        "the screen now belongs to "
        + where
        + " and not to "
        + str(expected)[:80]
        + ", so nothing further was replayed. Send `launch` to bring the app "
        "under test back to the front, then continue the case."
    )


def _screen_id(screen: object) -> str:
    return (
        str((screen or {}).get("screen_id") or "") if isinstance(screen, dict) else ""
    )


def _screen_has(screen: object, needle: str) -> bool:
    want = " ".join(str(needle or "").split()).lower()
    if not want or not isinstance(screen, dict):
        return False
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            if want in " ".join(str(element.get(field) or "").split()).lower():
                return True
    return False


def _screen_has_element(screen: object, rid: str, text: str = "") -> bool:
    """True when an element on *screen* has resource id *rid* (equal, or *rid*
    is the id without its package prefix), and, when *text* is given, a label
    (text or desc) containing it. ``resend`` never matches ``send``."""
    want = " ".join(str(rid or "").split()).lower()
    needle = " ".join(str(text or "").split()).lower()
    if not want or not isinstance(screen, dict):
        return False
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        have = " ".join(str(element.get("rid") or "").split()).lower()
        if have != want and not have.endswith("/" + want):
            continue
        if not needle:
            return True
        for field in ("text", "desc"):
            if needle in " ".join(str(element.get(field) or "").split()).lower():
                return True
    return False


def _texts(screen: object) -> set:
    """Every visible string on *screen*, whitespace-normalised.

    The unit ``new_text`` diffs. Element ids are positional and change on every
    dump, and bounds move when a list scrolls, so CONTENT is the only stable
    thing to compare two screens by.
    """
    out: set = set()
    if not isinstance(screen, dict):
        return out
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            value = " ".join(str(element.get(field) or "").split())
            if value:
                out.add(value)
    return out


_DIGITS_RE = re.compile(r"\d")


def _is_furniture(text: object) -> bool:
    """True for a bare clock, counter or badge -- a number and its punctuation.

    The digit-shape rule is bounded by this, and the bound is the whole of it:
    without one, "Your appointment is 10:30" -> "Your appointment is 14:00" and
    "Order 1" -> "Order 2" were both dropped as furniture, so an app answering
    with a DIFFERENT NUMBER -- the most likely true positive an appointments
    app has -- reported that nothing new appeared.

    A string carrying a letter is something someone wrote. A string that is
    only digits and separators is something that ticks.
    """
    return not any(character.isalpha() for character in str(text or ""))


def _digit_shape(text: object) -> str:
    """``10:43`` -> ``##:##``. Two strings with the same shape are the same
    piece of furniture showing a different number."""
    return _DIGITS_RE.sub("#", str(text or ""))


def _typed_by_script(trace: list[dict]) -> set:
    """Every literal this script has typed so far.

    The app putting your own words back on the screen is not the app
    answering, and a chat thread does exactly that.
    """
    out: set = set()
    for item in trace or []:
        action = item.get("action")
        if not isinstance(action, dict) or str(action.get("op") or "") not in (
            "type",
            "fill",
        ):
            continue
        value = " ".join(str(action.get("text") or "").split())
        if value:
            out.add(value)
    return out


def _verification_summary(trace: list[dict]) -> str:
    """What this trace actually VERIFIED, in the asserts' own words.

    A pass whose reason describes an omission ("the script finished without a
    done() action") tells a tester nothing about the app. The asserts already
    recorded what they checked; this joins them so the verdict can say it.
    """
    parts: list[str] = []
    for item in trace:
        if str(item.get("outcome") or "") != "assert_pass":
            continue
        action = item.get("action")
        kind = str(action.get("kind") or "") if isinstance(action, dict) else ""
        detail = str(item.get("detail") or "")
        line = (kind + " -- " + detail) if (kind and detail) else (kind or detail)
        if line:
            parts.append(line)
    return "; ".join(parts)[:600]


def _has_verification(trace: list[dict]) -> bool:
    """True when *trace* holds evidence a ``done verdict=pass`` can stand on.

    ONE thing counts: an ``assert_pass``. Something has to have been CHECKED.

    **2026-09-04 -- a screen change used to count too, and that was wrong.**
    The rule read "an ``assert_pass``, or a mutating op whose screen changed",
    on the reasoning that a screen that moved is evidence the app did
    something. It is evidence of ACTIVITY, not of verification, and any
    navigation satisfies it. Measured on the released build, both of these
    were recorded as ``pass``:

    * ``mrun-20260904-181558-ae196e`` -- a single ``tap``. One op, no assert.
    * ``mrun-20260904-181254-98dfbe`` -- ``[tap, back, wait]``. It pressed
      BACK and passed.

    Neither did the work its case described (send two Arabic questions, check
    the assistant answered), and a tester reading either report would have
    been told the app behaved. That is worse than no report. The original D3
    defect was ``[wait, done pass]``; closing it while leaving this branch
    open just moved the bar from "nothing at all" to "any tap".

    The cost is deliberate: a script that drives the app correctly but asserts
    nothing is now ``unverified`` rather than ``pass``. That is the honest
    answer -- nothing was verified -- and the vocabulary already carries
    ``assert`` for saying so.
    """
    return any(str(item.get("outcome") or "") == "assert_pass" for item in trace)


def _verified(trace: list[dict], ctx: Context) -> bool:
    """Whether a ``done verdict=pass`` can stand for THIS unit of work.

    Two disjuncts, and each is the only evidence available in one lane:

    * this trace carries an ``assert_pass`` -- the scripted lane's rule,
      unchanged and unweakened, because ``prior_verified`` defaults to False;
    * an earlier turn of the same RUN did -- the explore lane, where one turn
      is one case and the goal is what earns the verdict. Measured:
      ``mrun-20260907-174354-758700`` recorded asserts on TC-016 and TC-018 and
      its closing turn TC-019 was still told nothing had been verified, which
      no script could have fixed.
    """
    return _has_verification(trace) or bool(ctx.prior_verified)
