"""Parameter objects for building a mobile session's next packet (cut from session_packets.py, C5 w17)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile.replay.context import Context
from tools.mobile.session_core import Budget

logger = logging.getLogger("tools.mobile.session")


class PacketOpts(NamedTuple):
    """What the caller of ``next_packet`` may tune."""

    past_gate: bool = False
    budget: Budget | None = None


class PacketEnv(NamedTuple):
    """The device context and the limits one packet build runs under."""

    ctx: Context
    budget: Budget | None = None
    past_gate: bool = False


class Pick(NamedTuple):
    """The packet a reply carries and the case it belongs to."""

    packet: object
    tc_id: str
