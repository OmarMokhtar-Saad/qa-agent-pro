"""Mobile session: states, call budget, run-id minting and the device-claim group. Cut verbatim from tools/mobile/session.py (P4.4).

Facade over ``session_core_state``, ``session_core_budget`` and ``session_core_claim``:
every old name is re-exported here so a patch on this module still bites its callers.
"""

from __future__ import annotations

import logging
import secrets  # noqa: F401
import time  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import (  # noqa: F401
    run_finalize,
    run_store,
)
from tools.mobile.session_core_budget import (  # noqa: F401
    Budget,
    _busy,
    _in_reserve,
    new_budget,
)
from tools.mobile.session_core_claim import (  # noqa: F401
    ClaimView,
    _claim_refusal,
    _claim_reply,
    claim,
    mint_run_id,
    mint_session,
)
from tools.mobile.session_core_state import (  # noqa: F401
    _CASE_STORE_ERRORS,
    _RUN_PREFIX,
    CALL_BUDGET_S,
    LANE_EXPLORE,
    LANE_SUITE,
    PACKET_RESERVE_S,
    STATE_ABANDONED,
    STATE_BOOTING,
    STATE_BUSY,
    STATE_GATE,
    STATE_MENU,
    STATE_NEEDS_APP,
    STATE_NEEDS_FLAG,
    STATE_NO_AVD,
    STATE_PREFLIGHT,
    STATE_PROVISIONING,
    STATE_REPORT,
    STATE_RUNNING,
    STATE_TAKEN_OVER,
    STATES,
)

logger = logging.getLogger("tools.mobile.session")
