"""The request objects of the network capture: one per call, built by the caller.

``begin_network``, ``finish_network`` and ``abort_network`` each take ONE of
these, so a case's identity travels as a unit and no signature grows a fourth
parameter. ``capture`` re-exports all three.
"""

from __future__ import annotations

from typing import NamedTuple


class BeginReq(NamedTuple):
    serial: str
    package: str
    run_id: str
    tc_id: str
    case_index: object = 0


class FinishReq(NamedTuple):
    serial: str
    package: str
    run_id: str
    tc_id: str
    case_index: object
    begin_net: object


class AbortReq(NamedTuple):
    serial: str
    run_id: str
    tc_id: str
    begin_net: object
