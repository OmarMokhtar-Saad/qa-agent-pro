"""Sequence items for the agent's own cards, notes, failures and answers."""

from __future__ import annotations

from tools.mobile_evidence import exchanges
from tools.mobile_evidence.render_esc import MAX_REPLY_TEXT, ev_esc, guard
from tools.mobile_evidence.render_seq_util import seq_key


def _card_items(turn: dict) -> list:
    items: list = []
    for rec in turn.get("cards") or []:
        key = seq_key(rec)
        if key is not None:
            html = exchanges.seq_card(
                str(rec.get("kind") or "card"), bool(rec.get("replaced"))
            )
            items.append((key, exchanges.SEQ_RANK["card"], "card", guard(html)))
    return items


def _note_items(turn: dict) -> list:
    items: list = []
    for rec in turn.get("notes") or []:
        key = seq_key(rec)
        if key is not None:
            html = exchanges.seq_note(str(rec.get("text") or ""))
            items.append((key, exchanges.SEQ_RANK["log"], "note", guard(html)))
    return items


def _agent_error_item(rec: dict, key: float) -> tuple:
    detail = rec.get("detail") or {}
    message = str(detail.get("message") or detail.get("class") or "failed")
    html = guard(exchanges.seq_log("agent", message, exchanges.LogParts(bad=True)))
    return (key, exchanges.SEQ_RANK["agent"], "agent", html)


def _agent_error_items(turn: dict) -> list:
    items: list = []
    for rec in turn.get("errors") or []:
        key = seq_key(rec)
        if key is None or rec.get("kind") in ("binding", "llm", "tool"):
            continue  # already on their own rows
        items.append(_agent_error_item(rec, key))
    return items


def _answer_items(turn: dict) -> list:
    items: list = []
    for rec in turn.get("answers") or []:
        key = seq_key(rec)
        if key is not None:
            html = exchanges.seq_reply(ev_esc(rec.get("text"), MAX_REPLY_TEXT))
            items.append((key, exchanges.SEQ_RANK["reply"], "reply", guard(html)))
    return items


def agent_items(turn: dict) -> list:
    """Cards, notes, the agent's own failures and its final answers."""
    return (
        _card_items(turn)
        + _note_items(turn)
        + _agent_error_items(turn)
        + _answer_items(turn)
    )
