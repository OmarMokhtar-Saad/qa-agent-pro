"""Parse the oracle's broadcast replies into plain dicts."""

from __future__ import annotations

import base64

from tools.mobile import ime_nonce
from tools.mobile.ime_consts import _DATA_RE, _RESULT_RE


def _decode_field_text(payload: str) -> str:
    """The base64 ``t:`` payload as text, or ``""`` when it does not decode."""
    try:
        return base64.b64decode(payload or "", validate=True).decode(
            "utf-8", errors="replace"
        )
    except (TypeError, ValueError):
        return ""


def _set_field(fields: dict, part: str) -> None:
    """Fold one ``k:value`` reply part into *fields* (unknown keys ignored)."""
    if part.startswith("t:"):
        fields["text"] = _decode_field_text(part[2:])
    elif part.startswith("f:"):
        fields["field"] = part[2:]
    elif part.startswith("k:"):
        fields["visible"] = part[2:].strip() == "1"
    elif part.startswith("e:"):
        fields["refusal"] = part[2:].strip()[:40]
    elif part.startswith("p:"):
        fields["protocol"] = int(part[2:]) if part[2:].strip().isdigit() else 0
    elif part.startswith("x:"):
        fields["dump"] = part[2:].strip()


def _reply_fields(data: str) -> dict:
    """The ``;``-separated ``k:value`` parts of a reply, with defaults."""
    fields: dict = {
        "field": "",
        "visible": False,
        "text": "",
        "refusal": "",
        "protocol": 0,
        "dump": "",
    }
    for part in data.split(";"):
        _set_field(fields, part.strip())
    return fields


def _reply_of_parts(result: int, parts: dict, reveal_text: bool) -> dict:
    """The reply dict for an already-split reply (see ``_parse_reply``)."""
    field_text = parts["text"]
    reply = {
        "result": result,
        "field": parts["field"],
        "ime_visible": parts["visible"],
    }
    # Present only when non-empty, so a legacy reply keeps its exact shape.
    for key in ("refusal", "protocol", "dump"):
        if parts[key]:
            reply[key] = parts[key]
    if reveal_text:
        reply["text"] = field_text
    else:
        reply["text_len"] = len(field_text)
    return reply


# ``{result, field, ime_visible, text}`` from a broadcast reply.
#
# **``reveal_text=False`` is the SECRET path, and it is structural.** Under
# it the ``t:`` payload is decoded, MEASURED, and the plaintext is bound to
# nothing that outlives this function: the returned dict carries ``text_len``
# and has **no ``text`` key at all**. Absent, not empty -- a consumer
# reaching for it raises ``KeyError`` rather than silently reading ``""`` and
# concluding the field was blank.
#
# The existing secret guarantee (module docstring, ``adb.py``) is OUTBOUND: a
# secret never reaches argv on the way TO the device. This mode is the
# INBOUND half, opened the moment ``type_text`` began querying a secret
# field to see whether its text landed.
def _parse_reply(text: str, reveal_text: bool = True) -> dict:
    """``{result, field, ime_visible, text}`` from a broadcast reply."""
    result_match = _RESULT_RE.search(text or "")
    result = int(result_match.group(1)) if result_match else -1
    data_match = _DATA_RE.search(text or "")
    data = data_match.group(1) if data_match else ""
    return _reply_of_parts(result, _reply_fields(str(data)), reveal_text)


def reply_of(sent: dict) -> dict:
    """The parsed reply of a broadcast envelope; no field text is revealed."""
    payload = sent.get("content") or {}
    if not isinstance(payload, dict):
        return _parse_reply("", reveal_text=False)
    raw = str(payload.get("out") or "") + str(payload.get("err") or "")
    return _parse_reply(raw, reveal_text=False)


def _refusal_of(sent: dict) -> str:
    reply = reply_of(sent)
    if int(reply.get("result", -1)) != ime_nonce.REPLY_REFUSED:
        return ""
    return str(reply.get("refusal") or "unknown")


def _reply_code(sent: dict) -> int:
    """The ``result=`` code of a broadcast envelope; -1 when there is none."""
    payload = sent.get("content") or {}
    if not isinstance(payload, dict):
        return -1
    raw = str(payload.get("out") or "") + str(payload.get("err") or "")
    return int(_parse_reply(raw)["result"])
