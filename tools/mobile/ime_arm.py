"""Arm and disarm the keyboard's nonce."""

from __future__ import annotations

import logging

from tools.mobile import ime_nonce
from tools.mobile.ime_consts import ALREADY_ARMED, UNEXPECTED_ARM_REPLY
from tools.mobile.ime_host import host
from tools.mobile.ime_reply import _reply_code, reply_of
from tools.mobile.ime_send import SendReq, _send

logger = logging.getLogger("tools.mobile.ime")


async def _still_ours(serial: str, actions: dict, held: str) -> bool:
    """Does one nonce-bearing QUERY show the keyboard still armed for *held*?"""
    asked = await _send(serial, SendReq(str(actions["query"]), "", held))
    return not asked.get("error") and _reply_code(asked) in (0, 1)


def _armed_outcome(serial: str, nonce: str, reply: dict) -> dict:
    """Hold the nonce: the keyboard armed with a nonce-bearing protocol."""
    ime_nonce.hold(serial, nonce)
    ime_nonce.set_protocol(serial, int(reply["protocol"]))
    return {
        "error": None,
        "content": {"armed": True, "protocol": int(reply["protocol"])},
    }


def _arm_outcome(serial: str, nonce: str, reply: dict) -> dict:
    """Interpret an ARM reply, holding the nonce when the keyboard armed."""
    code = int(reply.get("result", -1))
    if (
        code == ime_nonce.REPLY_OK
        and int(reply.get("protocol", 0)) >= ime_nonce.PROTOCOL_NONCE
    ):
        return _armed_outcome(serial, nonce, reply)
    if code == ime_nonce.REPLY_ALREADY_ARMED:
        return {"error": ALREADY_ARMED, "content": None}
    if code == 0:
        ime_nonce.set_protocol(serial, ime_nonce.PROTOCOL_LEGACY)
        return {
            "error": None,
            "content": {"armed": False, "protocol": ime_nonce.PROTOCOL_LEGACY},
        }
    return {
        "error": UNEXPECTED_ARM_REPLY + " (result " + str(code) + ").",
        "content": None,
    }


async def _arm_fresh(serial: str, actions: dict) -> dict:
    """Forget any nonce, ARM with a new one and interpret the reply."""
    ime_nonce.forget(serial)
    nonce = ime_nonce.new_nonce()
    sent = await _send(serial, SendReq(str(actions["arm"]), "", nonce))
    if sent.get("error"):
        return sent
    return _arm_outcome(serial, nonce, reply_of(sent))


# Arm the keyboard with a new nonce; detect the APK generation by the reply.
#
# ``content`` is ``{"armed", "protocol"}``. ONLY ``result=0`` (what 1.0.0 returns for ARM,
# an action it does not know) means legacy: nothing is held and the legacy path is used
# unchanged. ``result=1`` with ``p:2`` or more arms. ``result=2`` is the named
# already-armed error. EVERYTHING else (``result=-1`` unparseable or lost, ``result=1``
# without ``p:``, any other code) is an error, never legacy: the callers turn an error
# into the run's one sticky fallback to ``input text`` with a notice. With a nonce
# already held (and ``fresh`` false) ONE nonce-bearing QUERY decides whether it is still
# ours before any second ARM.
async def arm(serial: str, fresh: bool = False) -> dict:
    """Arm the keyboard with a new nonce; detect the APK generation by the reply."""
    try:
        resolved = host().manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        held = ime_nonce.get(serial)
        if held and not fresh and await _still_ours(serial, actions, held):
            return {
                "error": None,
                "content": {"armed": True, "protocol": ime_nonce.protocol(serial)},
            }
        return await _arm_fresh(serial, actions)
    except Exception as exc:
        logger.exception("mobile.ime.arm failed")
        return {"error": str(exc), "content": None}


async def disarm(serial: str) -> dict:
    """Disarm the keyboard. The nonce is forgotten FIRST: a disarm that cannot reach the
    device must not leave a usable secret here. Never raises."""
    try:
        held = ime_nonce.get(serial)
        ime_nonce.forget(serial)
        if not held:
            return {"error": None, "content": {"disarmed": False}}
        resolved = host().manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        sent = await _send(serial, SendReq(str(actions["disarm"]), "", held))
        if sent.get("error"):
            return sent
        return {"error": None, "content": {"disarmed": True}}
    except Exception as exc:
        logger.exception("mobile.ime.disarm failed")
        return {"error": str(exc), "content": None}
