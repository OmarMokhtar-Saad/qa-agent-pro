"""The script schema: the action union, :class:`Script`, and the op vocabulary."""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tools.mobile import knowledge_limits
from tools.mobile.action_consts import (
    MAX_ACTIONS,
    MAX_EXPECT_APPS,
    MAX_TOTAL_WAIT_MS,
    WAIT_UNTIL_OPS,
)
from tools.mobile.action_models_assert import AskTesterAction, AssertAction, DoneAction
from tools.mobile.action_models_basic import (
    BackAction,
    ClearAction,
    FillAction,
    HomeAction,
    TapAction,
    TapTextAction,
    TypeAction,
)
from tools.mobile.action_models_nav import (
    ClearAppDataAction,
    LaunchAction,
    OpenUrlAction,
    PressAction,
    ScrollAction,
    WaitAction,
    WaitUntilAction,
)
from tools.mobile.action_waits import total_wait_ms

Action = Annotated[
    Union[
        TapAction,
        TapTextAction,
        TypeAction,
        FillAction,
        ClearAction,
        BackAction,
        HomeAction,
        PressAction,
        ScrollAction,
        WaitAction,
        WaitUntilAction,
        LaunchAction,
        ClearAppDataAction,
        OpenUrlAction,
        AssertAction,
        AskTesterAction,
        DoneAction,
    ],
    Field(discriminator="op"),
]


class Script(BaseModel):
    """One case's bounded plan."""

    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS)
    #: Optional host feedback on applied app notes; validated by knowledge_feedback.
    knowledge_feedback: list[dict] = Field(
        default_factory=list, max_length=knowledge_limits.MAX_FEEDBACK_ITEMS
    )

    @model_validator(mode="after")
    def waits_fit_one_submit(self) -> "Script":
        total = total_wait_ms(self.actions)
        if total > MAX_TOTAL_WAIT_MS:
            raise ValueError(
                "the waits in this script total "
                + str(total)
                + " ms, over the "
                + str(MAX_TOTAL_WAIT_MS)
                + " ms one submit allows; set ms per wait explicitly, or split "
                "the case into shorter scripts -- the server hands the screen "
                "back between them"
            )
        return self

    @model_validator(mode="after")
    def visual_is_last(self) -> "Script":
        # The screen a `visual` assert is judged on is the one attached to the
        # NEXT reply, so the replay stops there; an action after it would never
        # run, and a model reading its script back would believe it had.
        for action in self.actions[:-1]:
            if (
                getattr(action, "op", "") == "assert"
                and getattr(action, "kind", "") == "visual"
            ):
                raise ValueError(
                    "assert visual must be the LAST action of a script: the "
                    "replay stops there and hands you the screenshot to judge"
                )
        return self


OPS = (
    "tap",
    "tap_text",
    "type",
    "fill",
    "clear_app_data",
    "clear",
    "back",
    "home",
    "press",
    "scroll",
    "wait",
    *WAIT_UNTIL_OPS,
    "launch",
    "open_url",
    "assert",
    "ask_tester",
    "done",
)


#: The turn fields the EXPLORE packet advertises alongside ``actions``, WITH
#: their schema -- this table is the single source, and ``mobile_run`` builds
#: the packet's ``response_schema`` properties from it.
#:
#: They are not part of :class:`Script` (``extra="forbid"``, on purpose, so an
#: invented op cannot ride in on a typo), which is exactly why the reply has to
#: be split before it reaches ``parse_script`` rather than after.
#:
#: Single-sourced because the alternative was measured: v1.79.0 advertised
#: ``finding`` in one place and refused it in another for a whole release. A
#: field added here is advertised AND split; a field added to the packet alone
#: fails the schema test rather than reaching a tester as a refusal.
TURN_FIELD_SCHEMA: dict = {
    "finding": {"type": "string", "maxLength": 600},
    "goal_reached": {"type": "boolean"},
    "request_extension": {"type": "boolean"},
    "extension_reason": {"type": "string", "maxLength": 400},
    # Fix round 3, item 1. `app`: the package a multi-app goal has moved into,
    # declared once while that app is in front; the server accepts it only
    # when it matches the package it saw (explore_runner.handover). `sub_goal`:
    # the one step the model is on, which rides every packet in place of the
    # full goal. 200 = explore_runner.MAX_PACKAGE_CHARS / MAX_SUB_GOAL_CHARS.
    "app": {"type": "string", "maxLength": 200},
    "sub_goal": {"type": "string", "maxLength": 200},
    # Item 8: the packages THIS step may walk into (installer, App Tester, the
    # app under test). A foreign-app screen outside the list still cuts the script.
    "expect_apps": {
        "type": "array",
        "items": {"type": "string", "maxLength": 200},
        "maxItems": MAX_EXPECT_APPS,
    },
}

#: Just the names, for callers that only need to know what rides beside the
#: actions.
TURN_FIELDS: tuple[str, ...] = tuple(TURN_FIELD_SCHEMA)


#: The ops whose action can hold a value a tester typed. Everything else is
#: read-only as far as a credential is concerned, so the mask has no business
#: touching it.
VALUE_BEARING_OPS: frozenset = frozenset({"type", "fill", "ask_tester"})
