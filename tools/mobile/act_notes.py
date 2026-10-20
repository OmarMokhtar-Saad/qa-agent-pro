"""The vocabulary notes the model is shown: targets, waits, device-time budget.

Compact on purpose (B18): terse op lines, and nothing that the packet's
``response_schema`` already says (field names, enums, caps).
"""

from __future__ import annotations

from tools.mobile.action_consts import (
    ASSERT_KINDS,
    MAX_TOTAL_WAIT_MS,
    MAX_WAIT_MS,
    PRESS_KEYS,
    SUBMIT_BUDGET_MS,
    VERDICTS,
    WAIT_UNTIL_DEFAULT_S,
    WAIT_UNTIL_MAX_S,
)


def _action_notes() -> list[str]:
    """The vocabulary notes on targets, tapping, pressing and typing."""
    return [
        *_target_notes(),
        *_press_notes(),
    ]


def _target_notes() -> list[str]:
    """The vocabulary notes on targets and tapping."""
    return [
        "tap/type/clear/scroll/press act on a target; back/home/launch take none.",
        "clear_app_data: no target, no package; wipes THIS run's app (pm clear) "
        "and relaunches it; the destructive guard judges it.",
        "tap_text(text): taps the ONE control with that visible text; REFUSES "
        "when several carry it (then use tap with a narrower target).",
        "tap_text_if_present(text, max_s): tap_text for an OPTIONAL control "
        "(consent, promo); polls up to max_s (default 2, max 5); absent = no-op, "
        "never fails the script.",
    ]


def _press_notes() -> list[str]:
    """The vocabulary notes on pressing and typing."""
    return [
        "press(key, target): focuses the field, sends key; key in "
        + ", ".join(sorted(PRESS_KEYS))
        + ". `enter` submits whatever the APP decides, so a press is judged "
        "against EVERY element on the screen (text and widget names too); on a "
        "screen holding anything irreversible (confirm, pay, delete) it is "
        "handed to the tester -- tap the button you mean instead.",
        "type: secret=true ONLY for a tester-supplied value; never invent a credential"
        " and never put one in a plan.",
    ]


def _wait_notes() -> list[str]:
    """The vocabulary notes on waits and asserts."""
    return [
        _wait_until_head() + _wait_until_mid() + _wait_until_tail(),
        *_assert_notes(),
    ]


def _wait_until_head() -> str:
    """The first part of the wait note, up to the ms bound."""
    return (
        "Every wait returns the moment its condition holds: "
        "wait_until_text(text) text on screen; wait_until_gone(text) text "
        "gone (spinner); wait_until_changed screen differs; wait_until_idle "
        "two reads equal. max_s (default "
        + str(WAIT_UNTIL_DEFAULT_S)
        + ", max "
        + str(WAIT_UNTIL_MAX_S)
        + ") only bounds a condition that never comes; a dialog, call, other "
        "app or error text stops a wait early and is named. There is no fixed wait: "
        "the older wait takes until_text, until_rid (present, or gone with "
        "until_gone=true) or ms (<= "
    )


def _wait_until_mid() -> str:
    """The middle of the wait note: the ms bound, no shell sleeps and the assert lead-in."""
    return (
        str(MAX_WAIT_MS)
        + "), run as wait_until_changed bounded by that ms. Never sleep in a "
        "shell between calls: the phone is not watched meanwhile. assert kinds: "
    )


def _wait_until_tail() -> str:
    """The second half of the wait note: the assert kinds and what they take."""
    return (
        ", ".join(ASSERT_KINDS)
        + ". text_present/text_absent: the string in `text` OR `target.text` "
        "(`text` wins). assert visual takes `note` (what to judge), must be "
        "the LAST action, and your next reply carries the screenshot; use it "
        "only for what the element list cannot say (layout, colour, image, chart)."
    )


def _assert_notes() -> list[str]:
    """The vocabulary note on how to check that the app replied."""
    return [
        "To check the app REPLIED use assert new_text (optionally contains): "
        "it passes only on text absent from the previous screen. "
        "screen_changed is WEAK (any navigation) and never proves an answer.",
    ]


def _budget_notes() -> list[str]:
    """The vocabulary notes on the device-time budget, ask_tester, done and ids."""
    return [
        *_device_time_notes(),
        *_selector_notes(),
    ]


def _device_time_notes() -> list[str]:
    """The vocabulary notes on device time, ask_tester and done."""
    return [
        "One submit replays at most "
        + str(SUBMIT_BUDGET_MS)
        + " ms of device time; waits in one script total at most "
        + str(MAX_TOTAL_WAIT_MS)
        + " ms (over: REFUSED). Over the submit budget the replay stops and "
        "hands you the screen. The SCREEN RE-READ after each changing action "
        "spends the budget, so plan short scripts.",
        "ask_tester(prompt, field): stops the replay, asks the tester for that "
        "one field; the value is typed, never stored.",
        "end with done(verdict, reason); verdict in " + ", ".join(VERDICTS) + ".",
    ]


def _selector_notes() -> list[str]:
    """The vocabulary notes on selector hygiene and planning."""
    return [
        "Never send two selectors for different elements (id+text, rid+text, "
        "any pair), nor one that matches nothing beside one that does: the "
        "action is handed back. Never reuse an `id` after a `type` in the "
        "same script: the keyboard re-lays out the screen and every id goes "
        "stale; `rid` and on-screen text survive.",
        "Plan from the screen given. If a needed element is not on it, stop "
        "the script there; you get the next screen. No guessed coordinates.",
    ]
