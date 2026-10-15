"""The vocabulary notes the model is shown: targets, waits, device-time budget."""

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
        "clear_app_data takes no target and no package -- it wipes THIS "
        "run's own app's data (pm clear) and relaunches it, judged by the "
        "destructive guard like every other irreversible action.",
        "tap_text(text) is the short form of tap: it taps the ONE control carrying that visible text, and REFUSES when more than one control carries it rather than choosing between them. On a screen with the same word on several rows, use tap with a narrower target instead. It takes no target of its own.",
        "tap_text_if_present(text, max_s) is tap_text for an OPTIONAL control (a consent prompt, a promo): it polls up to max_s (default 2, at most 5) for the text and taps it when it shows; when it never does the step is a no-op recorded as absent and the script goes on. It never fails the script.",
    ]


def _press_notes() -> list[str]:
    """The vocabulary notes on pressing and typing."""
    return [
        "press(key, target) taps the field to focus it and then sends that "
        "key; key is one of "
        + ", ".join(sorted(PRESS_KEYS))
        + ". `enter` is the keyboard's ACTION key and WHAT IT SUBMITS IS "
        "DECIDED BY THE APP, not by the field you name -- so a press is "
        "judged against every element on the screen, ordinary text and "
        "widget names included (a message reading 'did you transfer it?' "
        "counts, and so does a control whose class or resource id contains "
        "one of these words), and "
        "on a screen holding anything irreversible (confirm, pay, delete, "
        "...) it is handed to the tester. On such a screen tap the button "
        "you mean instead; that is judged by its own label.",
        "type carries secret=true ONLY for a value the tester supplied; never "
        "invent a credential and never put one in a plan.",
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
        "Every wait waits FOR something and comes back the moment it "
        "happens: wait_until_text(text) until that text is on the screen, "
        "wait_until_gone(text) until it is not (a spinner, 'Loading'), "
        "wait_until_changed until the screen is a different screen, "
        "wait_until_idle until two reads in a row are the same. Each takes "
        "max_s (default "
        + str(WAIT_UNTIL_DEFAULT_S)
        + ", at most "
        + str(WAIT_UNTIL_MAX_S)
        + "), which is only the bound on a condition that never comes, and "
        "each stops early and names what appeared instead when a dialog, a "
        "call, another app or an error text takes the screen. There is no "
        "fixed wait: the older wait takes until_text, until_rid (an element "
        "by its resource id: present, or gone with until_gone=true) or ms (<= "
    )


def _wait_until_mid() -> str:
    """The middle of the wait note: the ms bound, no shell sleeps and the assert lead-in."""
    return (
        str(MAX_WAIT_MS)
        + "), and wait ms now runs as wait_until_changed bounded by that ms. "
        "Never sleep in a shell (sleep, Start-Sleep, timeout) between "
        "calls: the phone is not watched while you do. assert kinds are "
    )


def _wait_until_tail() -> str:
    """The second half of the wait note: the assert kinds and what they take."""
    return (
        ", ".join(ASSERT_KINDS)
        + ". text_present/text_absent take the string to look for in "
        "`text` OR in `target.text` -- either is accepted, and `text` "
        "wins if you send both. assert visual takes a `note` (what to "
        "judge) and must be the LAST action: the replay stops there and "
        "your next reply carries the screenshot, so judge it and send done "
        "or the next script. Use it only for what the element list cannot "
        "say (layout, colour, an image, a chart)."
    )


def _assert_notes() -> list[str]:
    """The vocabulary note on how to check that the app replied."""
    return [
        "To check that the app REPLIED, use assert new_text (optionally with "
        "contains): it passes only when text appeared that was not on the "
        "previous screen. screen_changed is WEAK -- any navigation "
        "satisfies it -- so it is never evidence that an answer arrived.",
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
        "One submit replays for at most "
        + str(SUBMIT_BUDGET_MS)
        + " ms of device time and the waits in one script may total at most "
        + str(MAX_TOTAL_WAIT_MS)
        + " ms. Over the wait total the script is REFUSED; over the submit "
        "budget the replay stops before the next action and hands you the "
        "screen, so plan short scripts rather than one long one. What spends "
        "that budget is the SCREEN RE-READ after every action that can "
        "change the screen, not the tap itself -- so a script is bounded by "
        "device time long before it is bounded by the action count above.",
        "ask_tester(prompt, field) stops the replay and asks the tester for "
        "that one field. The value is typed and never stored.",
        "end with done(verdict, reason); verdict is one of "
        + ", ".join(VERDICTS)
        + ".",
    ]


def _selector_notes() -> list[str]:
    """The vocabulary notes on selector hygiene and planning."""
    return [
        "Never send two selectors that point at different elements -- an "
        "`id` and a `text`, a `rid` and a `text`, any pair: the action is "
        "handed back rather than guessed, and so is a target one of whose "
        "selectors matches nothing while another matches. Never reuse an "
        "`id` for an action you plan AFTER a "
        "`type` in the same script: typing opens the keyboard and re-lays "
        "out the screen, so every id from the previous screen goes stale "
        "at once. `rid` and the on-screen text survive that; ids do not.",
        "Plan from the screen you were given. If an element you need is not "
        "on it, stop the script there -- the server hands you the next "
        "screen and you continue. Do not guess coordinates.",
    ]
