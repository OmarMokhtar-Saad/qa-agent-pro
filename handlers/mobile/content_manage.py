"""List, show and delete the flows and routes saved for one app."""

from __future__ import annotations

import asyncio
import json

from handlers.mobile.content_args import _FLOW_WARN
from tools.mobile import app_flows, app_routes
from tools.untrusted import single_line


def _row_line(row: dict) -> str:
    """One saved flow or route in a listing."""
    return "- `%s`: %d steps; params: %s; secret fields: %s" % (
        row["name"],
        row["steps"],
        ", ".join(row["params"]) or "none",
        ", ".join(row["fields"]) or "none",
    )


def _saved_listing(noun: str, pkg: str, rows: list) -> str:
    """The reply listing the saved flows or routes (*noun*) of *pkg*."""
    if not rows:
        return "No saved %ss for `%s`." % (noun, pkg)
    lines = ["Saved %ss for `%s`:" % (noun, pkg)]
    lines.extend(_row_line(row) for row in rows)
    return "\n".join(lines)


def _failure(got: dict) -> str:
    """The warning line of a store reply that carries an error."""
    return _FLOW_WARN + single_line(got["error"], 300) + "."


def _route_show(got: dict, target: tuple) -> str:
    """The reply showing one saved route, step by step, with its screens."""
    if got.get("error"):
        return _failure(got)
    pkg, name = target
    route = got["content"]
    lines = ["Route `%s` for `%s`:" % (name, pkg)]
    for index, step in enumerate(route["steps"], 1):
        lines.append(
            "%d. %s (starts on screen %s)"
            % (
                index,
                single_line(json.dumps(step, sort_keys=True), 300),
                route["screens"][index - 1],
            )
        )
    lines.append("Ends on screen %s." % route["end"])
    return "\n".join(lines)


def _flow_show(got: dict, target: tuple) -> str:
    """The reply showing one saved flow, step by step."""
    if got.get("error"):
        return _failure(got)
    pkg, name = target
    lines = ["Flow `%s` for `%s`:" % (name, pkg)]
    for index, step in enumerate(got["content"]["steps"], 1):
        lines.append(
            "%d. %s" % (index, single_line(json.dumps(step, sort_keys=True), 300))
        )
    return "\n".join(lines)


def _deleted(noun: str, gone: dict, target: tuple) -> str:
    """The reply to a delete: the warning, or the confirmation."""
    if gone.get("error"):
        return _failure(gone)
    pkg, name = target
    return "Deleted %s `%s` for `%s`." % (noun, name, pkg)


async def _mobile_routes_manage(verb: str, pkg: str, name: str) -> str:
    """``qa_mobile_flows`` with ``kind='route'``: list, show or delete one app's routes."""

    if verb == "list":
        listed = await asyncio.to_thread(app_routes.list_routes, pkg)
        return _saved_listing("route", pkg, listed.get("content") or [])
    if verb == "show":
        got = await asyncio.to_thread(app_routes.get_route, pkg, name)
        return _route_show(got, (pkg, name))
    gone = await asyncio.to_thread(app_routes.delete_route, pkg, name)
    return _deleted("route", gone, (pkg, name))


async def _mobile_flows_action(verb: str, pkg: str, name: str) -> str:
    """Run one validated ``qa_mobile_flows`` flow action: list, show or delete."""

    if verb == "list":
        listed = await asyncio.to_thread(app_flows.list_flows, pkg)
        return _saved_listing("flow", pkg, listed.get("content") or [])
    if verb == "show":
        got = await asyncio.to_thread(app_flows.get_flow, pkg, name)
        return _flow_show(got, (pkg, name))
    gone = await asyncio.to_thread(app_flows.delete_flow, pkg, name)
    return _deleted("flow", gone, (pkg, name))
