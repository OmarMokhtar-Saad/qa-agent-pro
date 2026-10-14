"""Modal/dialog detection for tools/ui_extractor.py."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from config.settings_guard import never_raise

logger = logging.getLogger("tools.ui_extractor")


def _modal_ids(soup: BeautifulSoup) -> set[str]:
    """Ids of every ``.modal`` and ``role=dialog`` element."""
    modal_ids: set[str] = set()
    for m in soup.select(".modal"):
        if m.get("id"):
            modal_ids.add(m.get("id"))
    for m in soup.find_all(attrs={"role": "dialog"}):
        if m.get("id"):
            modal_ids.add(m.get("id"))
    return modal_ids


def _trigger_labels(soup: BeautifulSoup, modal_ids: set[str]) -> dict[str, str]:
    """``{modal_id: visible label}`` of the first control that targets it."""
    triggers: dict[str, str] = {}
    for attr in ("data-bs-target", "data-target", "href"):
        for el in soup.find_all(attrs={attr: True}):
            val = (el.get(attr) or "").strip()
            if val.startswith("#") and val[1:] in modal_ids:
                text = el.get_text(strip=True)
                if text and val[1:] not in triggers:
                    triggers[val[1:]] = text
    return triggers


# Map each modal/dialog element id to the visible label of the control that
# opens it.
#
# Many sites (e.g. instakidzapp.com) put their signup/demo form inside a
# Bootstrap modal that is hidden until the tester clicks a "Book Demo" /
# "Get Started Free" trigger (``data-bs-target="#id"`` / ``data-target="#id"``
# / ``href="#id"``). Fields inside such a modal ARE in the DOM (so they get
# extracted) but are NOT reachable until the modal is opened — the generated
# steps must click the trigger first. Returns (modal_ids, {modal_id: trigger}).
# Never raises.
def _find_modal_triggers(soup: BeautifulSoup) -> tuple[set[str], dict[str, str]]:
    """Map each modal id to the label of the control that opens it."""
    with never_raise(
        logger,
        "ui_extractor._find_modal_triggers failed",
        level=logging.ERROR,
        exc_info=True,
    ):
        modal_ids = _modal_ids(soup)
        return modal_ids, _trigger_labels(soup, modal_ids)
    return set(), {}


def _field_modal_trigger(
    tag, modal_ids: set[str], triggers: dict[str, str]
) -> str | None:
    """If *tag* sits inside a known modal, return the label of the control that
    opens that modal (falling back to the modal id), else None. Never raises."""
    with never_raise():
        anc = tag
        for _ in range(15):
            anc = getattr(anc, "parent", None)
            if anc is None:
                break
            aid = anc.get("id") if hasattr(anc, "get") else None
            if aid and aid in modal_ids:
                return triggers.get(aid) or aid
        return None
    return None
