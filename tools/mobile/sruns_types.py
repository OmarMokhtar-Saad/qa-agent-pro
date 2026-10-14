"""Parameter objects for planning a mobile run (cut from session_runs.py, C5 w17)."""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger("tools.mobile.session")


class RunEnv(NamedTuple):
    """The four environment answers both lanes record on a new run's manifest."""

    device: object = None
    locale: object = None
    capture: object = None
    reset_app: object = None


class RunTarget(NamedTuple):
    """Which app, on which device, a run is planned for."""

    package: str
    serial: str
    avd: str = ""
    env: RunEnv = RunEnv()


class SuiteOpts(NamedTuple):
    """The suite lane's own planning options."""

    source: str = ""
    filters: object = None


class ExploreOpts(NamedTuple):
    """The exploratory lane's own planning options."""

    charter: object = None
    watch_for: object = ()


class CaseSets(NamedTuple):
    """The cases the tester supplied, and the ones that survived the filters."""

    submitted: object
    kept: list
