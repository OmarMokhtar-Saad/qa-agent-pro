"""Part of the mobile_run split (see handlers/mobile/run.py): request."""

from __future__ import annotations

from dataclasses import dataclass

from handlers import _common, _elicit
from tools.mobile import mobile_elicit
from tools.mobile import render as mobile_render


@dataclass(frozen=True)
class _MobileTestRequest:
    """Every start argument of ``handle_mobile_test``, for the new-run helpers."""

    source: str
    suite_id: str
    goal: str
    cases: str
    app: str
    package: str
    apply: bool
    serial: str
    avd: str
    new_run: bool
    locale: str
    capture: str
    capture_ack: bool
    reset_app: bool
    charter: str
    choose: _common.ChooseCb
    progress: _common.ProgressCb

    @classmethod
    def from_call(cls, params: dict) -> "_MobileTestRequest":
        """Build from a handler's ``locals()``; names that are no field are ignored."""
        return cls(**{k: v for k, v in params.items() if k in cls.__dataclass_fields__})

    def source_menu(self) -> str:
        """The source menu (or the narrow conflict question) for these arguments."""

        return mobile_render.source_menu_markdown(
            conflict=mobile_render.implied_sources(
                suite_id=self.suite_id, goal=self.goal, cases=self.cases
            ),
            unmatched=self.source,
        )

    def start_kwargs(self) -> dict:
        """The request-owned keyword arguments ``_mobile_start`` takes."""
        return {
            "suite_id": self.suite_id,
            "goal": self.goal,
            "cases": self.cases,
            "charter": self.charter,
            "progress": self.progress,
            "new_run": self.new_run,
        }


def _mobile_ask(choose: _common.ChooseCb):
    """The tester-question callback the mobile resolve-or-ask helpers take, or
    None when this client cannot elicit. After one timed-out question the rest
    of that client's questions skip the wait (``tools/mobile/mobile_elicit``)."""

    return mobile_elicit.make_mobile_ask(
        choose, _elicit.elicit_choice, client_key=_elicit._elicit_client_key
    )


@dataclass(frozen=True)
class _MobileStartContext:
    """What `_mobile_start` has already resolved by the time a path runs."""

    package: str
    serial: str
    avd: str
    device_facts: dict
    locale: object
    capture: object
    reset_app: object
    owner: str
    progress: _common.ProgressCb = None


@dataclass(frozen=True)
class _AppCall:
    """The arguments of ``_mobile_app_stage``, packed for its helpers."""

    serial: str
    package: str
    source: str
    app: str
    apply: bool
    progress: _common.ProgressCb = None
    choose: _common.ChooseCb = None

    @property
    def value(self) -> str:
        """The app hint, stripped."""
        return str(self.app or "").strip()

    @property
    def typed_package(self) -> str:
        """The package the tester gave, stripped."""
        return str(self.package or "").strip()


def _app_call(req: _MobileTestRequest, serial: str) -> _AppCall:
    """The app-stage call for one request on one device."""
    return _AppCall(
        serial, req.package, req.source, req.app, req.apply, req.progress, req.choose
    )
