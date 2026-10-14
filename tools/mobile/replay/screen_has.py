"""Replay screen predicates and the evidence a pass verdict needs."""

from __future__ import annotations

import re

from tools.mobile.replay.context_ctx import Context


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


# True when *trace* holds evidence a ``done verdict=pass`` can stand on.
#
# ONE thing counts: an ``assert_pass``. Something has to have been CHECKED.
#
# **2026-09-04 -- a screen change used to count too, and that was wrong.**
# The rule read "an ``assert_pass``, or a mutating op whose screen changed",
# on the reasoning that a screen that moved is evidence the app did
# something. It is evidence of ACTIVITY, not of verification, and any
# navigation satisfies it. Measured on the released build, both of these
# were recorded as ``pass``:
#
# * ``mrun-20260904-181558-ae196e`` -- a single ``tap``. One op, no assert.
# * ``mrun-20260904-181254-98dfbe`` -- ``[tap, back, wait]``. It pressed
#   BACK and passed.
#
# Neither did the work its case described (send two Arabic questions, check
# the assistant answered), and a tester reading either report would have
# been told the app behaved. That is worse than no report. The original D3
# defect was ``[wait, done pass]``; closing it while leaving this branch
# open just moved the bar from "nothing at all" to "any tap".
#
# The cost is deliberate: a script that drives the app correctly but asserts
# nothing is now ``unverified`` rather than ``pass``. That is the honest
# answer -- nothing was verified -- and the vocabulary already carries
# ``assert`` for saying so.
def _has_verification(trace: list[dict]) -> bool:
    """True when *trace* holds evidence a ``done verdict=pass`` can stand on."""
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
