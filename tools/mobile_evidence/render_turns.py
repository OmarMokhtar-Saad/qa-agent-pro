"""The per-case turns table: what the app heard, took, and said."""

from __future__ import annotations

from tools.mobile_evidence import render_esc as esc
from tools.mobile_evidence.render_block import SecHead, sec_block
from tools.mobile_evidence.render_html import empty, pill
from tools.mobile_evidence.render_view import (
    content_of,
    has_evidence,
    no_log_lead,
    view_of,
)

_TABLE_HEAD = (
    '<div class="tablewrap"><table class="cov turns"><thead><tr><th scope="col">#</th>'
    '<th scope="col">The app heard</th><th scope="col" class="num">Reply in</th>'
    '<th scope="col">Outcome</th><th scope="col">The app said</th></tr></thead><tbody>'
)
_TABLE_FOOT = (
    "</tbody></table></div>"
    '<p class="hint">Reply in: the app’s own turn-start line to its answer line, from the timestamps it wrote itself.</p>'
)


def _reply_ms(start: object, end: object) -> object:
    if (
        isinstance(start, (int, float))
        and isinstance(end, (int, float))
        and end >= start
    ):
        return end - start
    return None


def _outcome(turn: dict, answers: list, errors: list) -> str:
    if answers:
        return pill("p-ok", "answered")
    if errors:
        label = (errors[0].get("detail") or {}).get("class") or errors[0].get("kind")
        return pill("p-def", str(label or "failed"))
    if turn.get("llm") or turn.get("bindings"):
        return pill("p-gap", "no reply recorded")
    return pill("p-void", "nothing recorded")


def _turn_row(n: int, turn: dict) -> tuple:
    utt = (turn.get("utterances") or [None])[0]
    said = (utt or {}).get("text") or ""
    answers = turn.get("answers") or []
    errors = turn.get("errors") or []
    end = answers[0].get("ts") if answers else None
    reply_ms = _reply_ms((utt or {}).get("ts"), end)
    outcome = _outcome(turn, answers, errors)
    return (n, said, reply_ms, outcome, answers[0].get("text") if answers else "")


def _turn_rows(view: dict, report: dict) -> list:
    """One row per app turn: (n, said, reply_ms, outcome_pill, answer)."""
    return [_turn_row(n, turn) for n, turn in enumerate(view["turns"], 1)]


def _quote(text: object, otherwise: str) -> str:
    if text:
        return "<q>" + esc.ev_esc(esc.clip(text), 80) + "</q>"
    return '<span class="mt">' + otherwise + "</span>"


def _turn_table_row(row: tuple) -> str:
    n, said, reply_ms, outcome, answer = row
    return (
        '<tr><td class="n">'
        + str(n)
        + '</td><td class="said" dir="auto">'
        + _quote(said, "no utterance logged")
        + '</td><td class="num">'
        + esc.ev_esc(esc.fmt(reply_ms) if reply_ms is not None else "—", 16)
        + '</td><td class="outc">'
        + outcome
        + '</td><td class="said" dir="auto">'
        + _quote(answer, "no reply text")
        + "</td></tr>"
    )


def _missing(why: str, note: str) -> str:
    return sec_block("Turns", empty(why), SecHead(note=note))


def turns_table(loaded: object, tc_id: object) -> str:
    content = content_of(loaded)
    if not has_evidence(loaded):
        why = (
            no_log_lead(content) + " for this run: " + str(content.get("reason") or "")
        )
        return _missing(why, "what the app did is not available")
    view = view_of(loaded, tc_id)
    if not view or not view["turns"]:
        return _missing(
            "no app turn fell inside this case's window",
            "nothing the app logged was attributed to this case",
        )
    rows = "".join(_turn_table_row(r) for r in _turn_rows(view, content["report"]))
    head = SecHead(
        total=len(view["turns"]),
        note="what the app heard, how long it took, what came back",
        open_=True,
    )
    return sec_block("Turns", _TABLE_HEAD + rows + _TABLE_FOOT, head)
