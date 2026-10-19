"""The step-format text for the tool description (leaf of script_help)."""

from __future__ import annotations

from tools.mobile import action_consts, action_schema, launch_guard
from tools.mobile.script_help_ex import MULTI_ACTION_EXAMPLE, TARGET_KEYS, _dumps


def _script_line() -> str:
    return (
        'Script: {"actions":[...]}, at most '
        + str(action_consts.MAX_MODEL_ACTIONS)
        + " actions per step: plan the whole likely path (about 3-4 per screen), "
        "tap_text_if_present for screens that may not show, end with an assert."
    )


def _limit_lines() -> list:
    return [
        _script_line(),
        "Ops: " + ", ".join(action_schema.OPS) + ".",
        "Selectors (" + ", ".join(TARGET_KEYS) + ") go inside a target object, never "
        "beside op. fill takes a label instead of a target.",
        "wait ms <= "
        + str(action_consts.MAX_WAIT_MS)
        + "; "
        + ", ".join(action_consts.WAIT_UNTIL_OPS)
        + " take max_s <= "
        + str(action_consts.WAIT_UNTIL_MAX_S)
        + " (default "
        + str(action_consts.WAIT_UNTIL_DEFAULT_S)
        + "). One step waits at most "
        + str(action_consts.MAX_TOTAL_WAIT_MS)
        + " ms in total.",
    ]


def _scroll_lines() -> list:
    return [
        "scroll dir: "
        + "|".join(action_consts.SCROLL_DIRECTIONS)
        + " (the way the content moves: down shows what is below, up what is above)"
        + ". assert kind: "
        + "|".join(action_consts.ASSERT_KINDS)
        + " (visual must be last). done verdict: "
        + "|".join(action_consts.VERDICTS)
        + ". open_url schemes: "
        + ", ".join(action_consts.URL_SCHEMES)
        + ".",
        'launch reopens the app under test; {"op":"launch","package":"<id>"} '
        "opens ONE other app: only the App Tester ("
        + launch_guard.APP_TESTER_PACKAGE
        + ") or a package id listed in this "
        "reply expect_apps (at most " + str(action_consts.MAX_EXPECT_APPS) + " ids).",
    ]


def _reply_lines() -> list:
    finding_cap = action_schema.TURN_FIELD_SCHEMA["finding"]["maxLength"]
    return [
        "Reply fields beside actions: "
        + ", ".join(action_schema.TURN_FIELDS)
        + ". finding <= "
        + str(finding_cap)
        + " chars; typed text <= "
        + str(action_consts.MAX_TEXT_CHARS)
        + " chars.",
        "Example: " + _dumps(MULTI_ACTION_EXAMPLE),
    ]


def doc_lines() -> list:
    """The format-doc lines, in print order, before the length cap."""
    return _limit_lines() + _scroll_lines() + _reply_lines()
