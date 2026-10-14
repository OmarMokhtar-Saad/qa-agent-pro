"""The per-case "APIs the app called" section."""

from __future__ import annotations

from tools.mobile_evidence.render_block import SecHead, sec_block
from tools.mobile_evidence.render_html import empty
from tools.mobile_evidence.render_net import NO_RECORD, NOT_STOPPED, net
from tools.mobile_evidence.render_net_rows import net_notes, net_table

_TITLE = "APIs the app called"


def _bare(why: str, note: str) -> str:
    return sec_block(_TITLE, empty(why), SecHead(note=note))


def _no_rows(content: dict) -> str:
    """The honest sentence for a case whose capture produced no rows."""
    if not content["present"]:
        return _bare(NO_RECORD, "no capture was taken")
    if content["skipped"]:
        return _bare(content["skipped"], "nothing was captured for this case")
    if content["stage"] == "started":
        return _bare(NOT_STOPPED, "the capture was never stopped")
    return _bare(
        "the capture ran and caught no traffic this server could name "
        "while this case replayed",
        "captured, and empty",
    )


def case_network(record: object) -> str:
    """Always a section, never a blank: a case with no capture says WHY."""
    content = net(record)
    if not content["present"] or content["skipped"] or not content["rows"]:
        return _no_rows(content)
    rows = content["rows"]
    packets = content["packets"]
    return sec_block(
        _TITLE,
        net_notes(content, rows) + net_table(rows),
        SecHead(
            total=len(rows),
            note="every host reached while the case ran, from "
            + (str(packets) if isinstance(packets, int) else "the")
            + " captured packets",
        ),
    )
