"""The failing ``device_dns`` row: what the tester reads and the remedy chosen."""

from __future__ import annotations

import logging

from tools.mobile import adb
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_dns_consts import (
    DNS_DEVICE_FIX,
    DNS_EMULATOR_FIX,
    DNS_NO_ROUTE_FIX,
    DNS_PING_WAIT_S,
    DNS_PROBE_HOST,
    DNS_PROBE_IP,
    DNS_PROBE_TIMEOUT_S,
    DNS_SLOW_NOTE,
)
from tools.mobile.preflight_rows import _exit_code

logger = logging.getLogger("tools.mobile.preflight")


async def _can_route(resolved_serial: str) -> bool:
    """True when the device answers a ping to the literal address."""
    routed = await adb.shell(
        resolved_serial,
        ["ping", "-c", "1", "-W", str(DNS_PING_WAIT_S), DNS_PROBE_IP],
        timeout=DNS_PROBE_TIMEOUT_S,
    )
    route_body = routed.get("content") or {}
    return not routed.get("error") and _exit_code(route_body) == 0


async def _dns_fix(resolved_serial: str, can_route: bool) -> str:
    """The remedy sentence; it follows the route probe alone."""
    facts = await adb.device_facts(resolved_serial)
    kind = str((facts.get("content") or {}).get("kind") or "")
    # The REMEDY follows the route probe alone -- what the tester must change
    # is the same whether the name failed fast or hung. The DETAIL is what
    # carries the difference, because a tester who is told "cannot resolve"
    # about a lookup that actually took 30s will not recognise the stall they
    # are about to sit through.
    if not can_route:
        return DNS_NO_ROUTE_FIX
    return DNS_EMULATOR_FIX if kind == "emulator" else DNS_DEVICE_FIX


async def _dns_failure_record(resolved_serial: str, overran: bool) -> dict:
    """The failing ``device_dns`` row, with the remedy chosen by a route probe.

    WHICH failure this is decides the remedy, so it is measured rather than
    assumed: a device with no route at all is a different problem from one
    whose resolver is stale, and telling a tester to restart the emulator when
    the machine is simply offline wastes their time.
    """
    can_route = await _can_route(resolved_serial)
    fix = await _dns_fix(resolved_serial, can_route)
    detail = _dns_failure_detail(resolved_serial, overran, can_route)
    return rows.record_row("device_dns", rows._Row(False, detail, fix))


def _overran_detail(resolved_serial: str, can_route: bool) -> str:
    """The detail when the lookup outlasted its budget."""
    head = (
        resolved_serial
        + " did not resolve "
        + DNS_PROBE_HOST
        + " within "
        + str(DNS_PROBE_TIMEOUT_S)
    )
    if can_route:
        return head + "s, while " + DNS_PROBE_IP + " answered: " + DNS_SLOW_NOTE
    return head + "s and did not reach " + DNS_PROBE_IP + " either"


def _failed_detail(resolved_serial: str, can_route: bool) -> str:
    """The detail when the lookup failed outright."""
    if can_route:
        return (
            resolved_serial
            + " pings "
            + DNS_PROBE_IP
            + " but cannot resolve "
            + DNS_PROBE_HOST
            + ": packets route and names do not"
        )
    return (
        resolved_serial + " reached neither " + DNS_PROBE_HOST + " nor " + DNS_PROBE_IP
    )


def _dns_failure_detail(resolved_serial: str, overran: bool, can_route: bool) -> str:
    """What the tester reads: the lookup's outcome combined with the route's."""
    if overran:
        return _overran_detail(resolved_serial, can_route)
    return _failed_detail(resolved_serial, can_route)
