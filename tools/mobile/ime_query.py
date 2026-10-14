"""Ask the oracle what it sees; clear the focused field."""

from __future__ import annotations

from tools.mobile import ime_nonce
from tools.mobile.ime_consts import NO_FOCUSED_FIELD, NO_RECEIVER
from tools.mobile.ime_host import host
from tools.mobile.ime_reply import _parse_reply, _refusal_of, _reply_code


# ``reveal_text=False`` returns ``text_len`` instead of ``text``; see
# ``_parse_reply``.
async def query(serial: str, reveal_text: bool = True) -> dict:
    """Ask the oracle what it can see. ``result=0`` means NO input connection."""
    resolved = host().manifest()
    if resolved.get("error"):
        return resolved
    actions = (resolved["content"] or {})["actions"]
    sent = await host().broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return sent
    # A refusal still standing after the re-arm retries is not an answer: parsed as
    # one, its missing field would read as empty and the probe would report OK.
    refused = _refusal_of(sent)
    if refused:
        return {"error": ime_nonce.refusal_text(refused), "content": None}
    payload = sent["content"] or {}
    raw_text = str(payload.get("out") or "") + str(payload.get("err") or "")
    return {"error": None, "content": _parse_reply(raw_text, reveal_text=reveal_text)}


async def probe(serial: str) -> dict:
    """``{ok, result, field, ime_visible}`` -- the preflight's oracle check.

    ``ok`` is True for result 0 AND 1: both are ANSWERS. Only ``-1`` (no
    ``result=`` in the reply at all) means the service did not respond, which
    is the failure a preflight must catch.
    """
    answered = await query(serial)
    if answered.get("error"):
        return answered
    content = answered["content"] or {}
    return {
        "error": None,
        "content": {
            "ok": int(content.get("result", -1)) >= 0,
            "result": int(content.get("result", -1)),
            "field": str(content.get("field") or ""),
            "ime_visible": bool(content.get("ime_visible")),
        },
    }


async def _field_snapshot(serial: str, actions: dict, secret: bool):
    """What the focused field holds, or ``None`` when the keyboard cannot say.

    ``None`` for every unanswerable case -- broadcast error, no ``result=``,
    ``result=0`` (no input connection) -- so one sentinel means "no reading",
    and the verdict never has to distinguish a failure from an empty field.

    For a secret this returns an INT (the length) and never the text; see
    ``_parse_reply``'s ``reveal_text``.
    """
    sent = await host().broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return None
    payload = sent.get("content") or {}
    raw = str(payload.get("out") or "") + str(payload.get("err") or "")
    reply = _parse_reply(raw, reveal_text=not secret)
    if int(reply.get("result", -1)) != 1:
        return None
    return reply["text_len"] if secret else reply["text"]


async def _focused_field(serial: str, actions: dict) -> str:
    """Empty when the keyboard answers with a focused field, else why not."""
    sent = await host().broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return str(sent["error"])
    code = _reply_code(sent)
    if code < 0:
        return NO_RECEIVER
    if code == ime_nonce.REPLY_REFUSED:
        return ime_nonce.refusal_text(_refusal_of(sent))
    if code == 0:
        return NO_FOCUSED_FIELD
    return ""


async def _undelivered(serial: str, actions: dict, sent: dict) -> str:
    """Why an INPUT/CLEAR reply did not land, or empty when it did.

    Only a reply with NO ``result=`` is a failure; its VALUE is not read,
    because QaImeService.onInput/onClear set no result code and ``am
    broadcast`` reports its default on every delivery (whether the text
    LANDED is the open item in docs/DECISIONS.md). On that path ONE query
    tells a dead receiver from a field that lost focus.
    """
    code = _reply_code(sent)
    if code == ime_nonce.REPLY_REFUSED:
        # The new APK refused (not armed / wrong nonce): NOT a delivery, even
        # though 4 >= 0. Named, so the tester is told why nothing was typed.
        return ime_nonce.refusal_text(_refusal_of(sent))
    if code >= 0:
        return ""
    return (await _focused_field(serial, actions)) or NO_RECEIVER


async def clear(serial: str, receiver_known: bool = False) -> dict:
    """Clear the focused field through the IME."""
    resolved = host().manifest()
    if resolved.get("error"):
        return resolved
    actions = (resolved["content"] or {})["actions"]
    if not receiver_known:
        refused = await _focused_field(serial, actions)
        if refused:
            return {"error": refused, "content": None}
    sent = await host().broadcast(serial, str(actions["clear"]))
    if sent.get("error"):
        return sent
    refused = await _undelivered(serial, actions, sent)
    if refused:
        return {"error": refused, "content": None}
    return {"error": None, "content": {"cleared": True}}
