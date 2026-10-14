"""The sequence primitives for what the user and the app said and what the driver did."""

from __future__ import annotations

from tools.mobile_evidence.exch_seqargs import PromptNote
from tools.mobile_evidence.exch_seqk import SEQ_CUT
from tools.mobile_shared.scrub import e, scrub_text


def seq_user(text: str, pill_html: str = "") -> str:
    """The conversation is a render path too: scrubbed HERE, at the primitive."""
    text = scrub_text(text or "")
    full = (
        (SEQ_CUT + '<p class="seqfull" dir="auto">' + e(text) + "</p>")
        if len(text) > 160
        else ""
    )
    return (
        '<span class="seqk">user</span><span class="seqtx" dir="auto">'
        + e(text)
        + "</span>"
        + pill_html
        + full
    )


def seq_reply(said: str, extra: str = "") -> str:
    """The app's reply. *said* arrives already escaped (or as an em-dash marker); *extra* is
    any block the caller attaches, and it belongs in the fold with a long reply's full text."""
    said = scrub_text(said or "")
    extra = scrub_text(extra or "")
    detail = (
        '<p class="seqfull" dir="auto">' + said + "</p>" if len(said) > 160 else ""
    ) + (extra or "")
    return (
        '<span class="seqk">reply</span><span class="seqtx" dir="auto">'
        + said
        + "</span>"
        + ((SEQ_CUT + detail) if detail else "")
    )


def seq_prompt(chars: int, body: str, extra: PromptNote | None = None) -> str:
    extra = extra or PromptNote()
    note, head, meta = extra.note, extra.head, extra.meta
    mt = '<span class="mt">' + e(note) + "</span>" if note else ""
    ex = '<span class="mt">' + e(meta) + "</span>" if meta else ""
    return (
        '<span class="seqk">prompt</span><span class="seqtx">'
        + e(head or "what the model was given")
        + '</span><span class="mt">'
        + format(int(chars), ",")
        + " ch</span>"
        + ex
        + mt
        + SEQ_CUT
        + '<div class="pane">'
        + body
        + "</div>"
    )


def seq_step(label: str, value: object) -> str:
    """What the DRIVER did -- typed, sent, waited -- in the same stream as what the app did."""
    return (
        '<span class="seqk">'
        + e(label)
        + '</span><span class="seqtx" dir="auto">'
        + e(str(value))
        + "</span>"
    )


def seq_card(what: str, replaced: bool = False) -> str:
    return (
        '<span class="seqk">card</span><span class="seqtx">'
        + e(what)
        + (" (replaced the one before it)" if replaced else "")
        + "</span>"
    )


def seq_note(text: str) -> str:
    return (
        '<span class="seqk">note</span><span class="seqtx" dir="auto">'
        + e(text)
        + "</span>"
    )


def seq_turn(n: int, said: str = "") -> str:
    return (
        '<span class="seqk">turn '
        + str(int(n))
        + "</span>"
        + ('<span class="seqtx" dir="auto">' + e(said) + "</span>" if said else "")
    )
