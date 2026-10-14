"""Sequence items for a turn: the opening marker, the LLM exchanges, the calls."""

from __future__ import annotations

from tools.mobile_evidence import evidence, exchanges
from tools.mobile_evidence.render_esc import guard
from tools.mobile_evidence.render_seq_util import bar, seq_key


def _first_key(turn: dict) -> float | None:
    first_key = None
    for stream in evidence.STREAMS:
        for rec in turn.get(stream) or []:
            key = seq_key(rec)
            if key is not None and (first_key is None or key < first_key):
                first_key = key
    return first_key


def _turn_marker(turn: dict, n: int, first_key: float) -> tuple:
    utt = (turn.get("utterances") or [None])[0]
    html = guard(exchanges.seq_turn(n, (utt or {}).get("text") or ""))
    return (first_key, exchanges.SEQ_RANK["turn"], "turn", html)


def _user_item(rec: dict, key: float) -> tuple:
    html = guard(exchanges.seq_user(str(rec.get("text") or "")))
    return (key, exchanges.SEQ_RANK["user"], "user", html)


def turn_opening_items(turn: dict, n: int, multi: bool) -> list:
    """The turn marker (when the case has several turns), then what the user said."""
    items: list = []
    first_key = _first_key(turn)
    if multi and first_key is not None:
        items.append(_turn_marker(turn, n, first_key))
    for rec in turn.get("utterances") or []:
        key = seq_key(rec)
        if key is not None:
            items.append(_user_item(rec, key))
    return items


def _prompt_note(x: dict, msgs: list) -> exchanges.PromptNote:
    """The head and the message count chip of a prompt row."""
    return exchanges.PromptNote(
        head="request to " + str(x.get("path") or ""),
        meta=str(len(msgs)) + " msgs",
    )


def _prompt_item(rec: dict, x: dict, key: float) -> tuple | None:
    msgs = rec.get("promptMessages") or []
    prompts = "".join(exchanges.codebox(exchanges.body_text(p)) or "" for p in msgs)
    if not prompts:
        return None
    chars = sum(len(exchanges.body_text(p)) for p in msgs)
    return (
        key,
        exchanges.SEQ_RANK["prompt"],
        "prompt",
        guard(exchanges.seq_prompt(chars, prompts, _prompt_note(x, msgs))),
    )


def _llm_pair(rec: dict, key: float, longest: float) -> list:
    x = exchanges.llm_exchange(rec)
    prompt = _prompt_item(rec, x, key)
    llm = (
        key,
        exchanges.SEQ_RANK["llm"],
        "llm",
        guard(exchanges.seq_exchange(x)),
        None,
        bar(rec.get("derivedMs"), longest, "llm"),
    )
    return [prompt, llm] if prompt else [llm]


def llm_items(turn: dict, longest: float) -> list:
    items: list = []
    for rec in turn.get("llm") or []:
        key = seq_key(rec)
        if key is not None:
            items += _llm_pair(rec, key, longest)
    return items


def _call_item(found: tuple, spec: tuple, longest: float) -> tuple:
    rec, key = found
    rank, kind, bar_kind, build = spec
    return (
        key,
        exchanges.SEQ_RANK[rank],
        kind,
        guard(exchanges.seq_exchange(build(rec))),
        None,
        bar(rec.get("derivedMs"), longest, bar_kind),
    )


def call_items(turn: dict, longest: float) -> list:
    """The tool calls, then the API bindings."""
    items: list = []
    for stream, spec in (
        ("tools", ("call", "call", "tool", exchanges.tool_exchange)),
        ("bindings", ("api", "api", "api", exchanges.binding_exchange)),
    ):
        for rec in turn.get(stream) or []:
            key = seq_key(rec)
            if key is not None:
                items.append(_call_item((rec, key), spec, longest))
    return items
