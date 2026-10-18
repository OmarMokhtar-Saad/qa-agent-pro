"""Reading a decoded reply: the declared apps, the finding and the one-time decode."""

from __future__ import annotations

import json
import logging

from tools.mobile.action_consts import MAX_EXPECT_APPS, is_package_id
from tools.mobile.action_schema import TURN_FIELD_SCHEMA
from tools.mobile.app_label import package_for_label

logger = logging.getLogger("tools.mobile.actions")


def expect_apps_from(payload: object) -> list[str]:
    """The package ids a decoded turn declared in ``expect_apps``, validated.

    An id that is not a well-formed package id is DROPPED, not refused: a shorter
    list only narrows what the executor lets through, so it fails closed. Duplicates
    collapse and the list is cut at :data:`MAX_EXPECT_APPS`.
    """
    listed = payload.get("expect_apps") if isinstance(payload, dict) else None
    out: list[str] = []
    for item in (listed if isinstance(listed, list) else [])[:MAX_EXPECT_APPS]:
        if is_package_id(item) and item not in out:
            out.append(item)
    declared = declared_app_package(payload)
    if declared and declared not in out and len(out) < MAX_EXPECT_APPS:
        out.append(declared)
    return out


def declared_app_package(payload: object) -> str:
    """The package the turn's ``app`` names (a package id or a known label), else ``""``.

    An ``app`` that cannot be resolved yields ``""`` so the cut keeps its current,
    stricter behaviour.
    """
    said = payload.get("app") if isinstance(payload, dict) else None
    text = said.strip() if isinstance(said, str) else ""
    return text if is_package_id(text) else package_for_label(text)


def script_finding(payload: object) -> str:
    """The ``finding`` a decoded turn (or a tool kwarg) carried, whitespace-folded
    and cut at the advertised cap; empty when there is none or it is not text."""
    raw = payload.get("finding") if isinstance(payload, dict) else None
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[: TURN_FIELD_SCHEMA["finding"]["maxLength"]]


# A model's reply, decoded ONCE into a dict at the transport boundary.
#
# The MCP tool declares ``script: str``, so on every real client path this
# function receives TEXT, and a caller that tested ``isinstance(raw, dict)``
# saw ``{}`` -- which is how the explore lane advertised ``finding`` in its
# ``response_schema``, asked for one every turn in ``worker_instructions``,
# and then refused it at the transport (v1.79.0, found on a live run; every
# test passed a dict, so nothing caught it).
#
# Accepts the same three shapes ``parse_script`` promises -- ``{"actions":
# [...]}``, a bare list, or a JSON string of either -- and always returns a
# dict with an ``actions`` key. Undecodable input is passed THROUGH under
# ``actions`` rather than rejected here, so the caller still gets
# ``parse_script``'s own wording rather than a second, competing error.
def decode_reply(raw: object) -> dict:
    """A model's reply, decoded ONCE into a dict at the transport boundary."""
    try:
        return _shape_reply(raw)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.actions.decode_reply failed")
        return {"actions": raw}


def _shape_reply(raw: object) -> dict:
    """The reply as a dict with an ``actions`` key; undecodable input rides through."""
    payload = raw
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {"actions": raw}
        try:
            payload = json.loads(text)
        except ValueError:
            return {"actions": raw}
    if isinstance(payload, list):
        return {"actions": payload}
    if isinstance(payload, dict):
        out = dict(payload)
        out.setdefault("actions", [])
        return out
    return {"actions": raw}
