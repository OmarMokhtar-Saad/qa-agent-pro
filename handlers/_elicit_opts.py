"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from handlers import _common
from handlers._elicit_gate import elicit_client_gated

# The free-text sibling of ChooseCb: ``ask_text(message) -> ChoiceResult`` where
# CHOSEN carries the typed text. Provided by the transport adapter
# (mcp_server._make_asker); None when elicitation is off/unsupported.
AskCb = Optional[Callable[[str], Awaitable["_common.ChoiceResult"]]]


def _device_options(devices: list):
    """Build (labels, label->id) for a device elicitation. The label leads with
    the device name and carries the id so distinct devices sharing a name stay
    unambiguous when the selected label is mapped back to an id."""
    labels: list = []
    by_label: dict = {}
    for dev in devices:
        did = dev.get("id")
        label = f"{dev.get('name') or did} ({did})"
        labels.append(label)
        by_label[label] = did
    return labels, by_label


def _suite_options(rows: list):
    """Build (labels, label->suite_id) for a recent-suites elicitation."""
    labels: list = []
    by_label: dict = {}
    for row in rows:
        feature = " ".join((row.get("feature_text") or "untitled").split())[:60]
        label = (
            f"{feature} — {row.get('case_count', 0)} cases "
            f"({str(row.get('suite_id'))[:8]}…)"
        )
        labels.append(label)
        by_label[label] = row.get("suite_id")
    return labels, by_label


def _elicitation_disabled(choose: _common.ChooseCb, ask_text: AskCb) -> bool:
    """True when beat 1 elicitation is switched off, not merely unsupported."""
    # 2026-08-21: a client GATED by elicit_client_gated() also arrives with both
    # callbacks None, but that is a capability OBSERVATION about the client, not
    # an operator disabling the feature -- and this label is stamped into the
    # mcp_image_gate_beat1 audit row. It therefore keeps the byte-identical
    # "unavailable/unavailable" form, which is exactly what a client that cannot
    # show dialogs already reports; "disabled" stays reserved for the
    # (now-deleted) elicit seam it was written for.
    return bool(choose is None and ask_text is None and not elicit_client_gated())
