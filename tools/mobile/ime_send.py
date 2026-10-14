"""Send one broadcast to the QA input method (payload and nonce on stdin)."""

from __future__ import annotations

import re
from typing import NamedTuple

from tools.mobile import adb, ime_nonce
from tools.mobile.ime_consts import _B64_RE
from tools.mobile.ime_host import host
from tools.mobile.ime_reply import _refusal_of


def _payload_refusal(b64: str) -> dict | None:
    """The refusal answer for a payload that is not base64, else None."""
    if b64 and not _B64_RE.match(b64):
        return {
            "error": (
                "Refusing to broadcast a payload that is not valid base64; "
                "nothing was sent."
            ),
            "content": None,
        }
    return None


def _broadcast_line(action: str, b64: str = "") -> dict:
    """Build the ``am broadcast`` shell line, or refuse.

    Returns ``{"error", "content": <line>}``. The payload is validated against
    the base64 alphabet HERE, before it is placed inside single quotes, so the
    quoting cannot be broken by any input.
    """
    if not re.match(r"^[A-Za-z0-9._]{1,120}$", str(action or "")):
        return {
            "error": "Refusing to broadcast action " + repr(str(action)[:60]),
            "content": None,
        }
    refusal = _payload_refusal(b64)
    if refusal:
        return refusal
    if b64:
        line = "am broadcast -a " + action + " --es msg '" + b64 + "'\n"
    else:
        line = "am broadcast -a " + action + "\n"
    return {"error": None, "content": line}


class SendReq(NamedTuple):
    """One broadcast to send: the action, its base64 payload and the nonce."""

    action: str
    b64: str = ""
    nonce: str = ""


async def _send(serial: str, req: SendReq) -> dict:
    nonce = req.nonce
    built = _broadcast_line(req.action, req.b64)
    if built.get("error"):
        return built
    line = str(built["content"])
    if nonce:
        # Validated BEFORE it is quoted into the shell line, like the payload.
        if not ime_nonce.valid_nonce(nonce):
            return {
                "error": "Refusing to broadcast a malformed nonce; nothing was sent.",
                "content": None,
            }
        line = line.rstrip("\n") + " --es nonce '" + nonce + "'\n"
    # THE point of this module: an EMPTY argv plus stdin. The payload and the
    # nonce are never an element of the host command line.
    return await adb.shell_in(serial, [], adb.StdinFeed(line.encode("utf-8")))


async def broadcast(serial: str, action: str, b64: str = "") -> dict:
    """Send one broadcast with this serial's nonce, if it holds one.

    ``not_armed`` (idle timeout, restart) re-arms with a FRESH nonce and retries
    ``REARM_MAX_RETRIES`` times; ``bad_nonce`` is never retried.
    """
    nonce = ime_nonce.get(serial)
    sent = await _send(serial, SendReq(action, b64, nonce))
    for _ in range(ime_nonce.REARM_MAX_RETRIES):
        if not nonce or sent.get("error") or _refusal_of(sent) != "not_armed":
            break
        if (await host().arm(serial, fresh=True)).get("error"):
            return sent
        nonce = ime_nonce.get(serial)
        sent = await _send(serial, SendReq(action, b64, nonce))
    return sent


async def send(serial: str, action: str) -> dict:
    """Public seam for the a11y module: one authorised broadcast."""
    return await host().broadcast(serial, action)
