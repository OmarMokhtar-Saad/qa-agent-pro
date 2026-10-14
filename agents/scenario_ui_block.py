"""Prompt block describing the scraped UI context."""

from __future__ import annotations

import logging

logger = logging.getLogger("agents.test_scenario_agent")


def _ui_form_field_lines(form_fields: list) -> list[str]:
    """Markdown lines describing the form fields (empty when there are none)."""
    if not form_fields:
        return []
    lines = ["\n**Form fields**:"]
    for f in form_fields[:15]:
        label = f.get("label") or f.get("name") or f.get("placeholder") or "(unnamed)"
        ftype = f.get("type", "text")
        req = " (required)" if f.get("required") else ""
        ph = f" placeholder='{f['placeholder']}'" if f.get("placeholder") else ""
        trig = f.get("modal_trigger")
        modal_note = f" — inside a pop-up opened by clicking '{trig}'" if trig else ""
        lines.append(f"  - {label} [{ftype}]{ph}{req}{modal_note}")
    return lines


def _ui_modal_trigger_lines(form_fields: list) -> list[str]:
    # Fields hidden behind a pop-up/modal are in the DOM but NOT reachable
    # until the trigger is clicked. Tell the generator to OPEN the pop-up as
    # the first step, so steps aren't written as if the fields are already
    # on screen.
    modal_triggers = sorted(
        {f.get("modal_trigger") for f in form_fields if f.get("modal_trigger")}
    )
    if not modal_triggers:
        return []
    trig_list = " or ".join(f"'{t}'" for t in modal_triggers)
    return [
        "\n**IMPORTANT — pop-up form**: The form fields above are inside a "
        f"pop-up/modal dialog that is NOT visible on page load. It opens only "
        f"when the tester clicks {trig_list}. Every test case that uses these "
        f'fields MUST make its FIRST step open the pop-up (e.g. "Click {trig_list} '
        'to open the form") BEFORE entering any data — do not write steps as if '
        "the fields are already on screen."
    ]


def _ui_list_section_lines(ui_elements: dict) -> list[str]:
    """Buttons, navigation links and other interactive elements, in that order."""
    lines: list[str] = []
    buttons = ui_elements.get("buttons") or []
    if buttons:
        btn_strs = [b.get("text", "") for b in buttons[:10] if b.get("text")]
        lines.append("\n**Buttons**: " + ", ".join(btn_strs))

    nav = ui_elements.get("navigation_links") or []
    if nav:
        lines.append("\n**Navigation links**: " + ", ".join(nav[:10]))

    interactive = ui_elements.get("interactive") or []
    if interactive:
        lines.append("\n**Other interactive elements**:")
        lines.extend(f"  - {item}" for item in interactive[:10])
    return lines


_UI_SCOPE_NOTE = (
    "\nSCOPE — IMPORTANT: Generate test cases ONLY for the elements listed "
    "above and the form/page they belong to. Reference the exact field names "
    "and button labels found on the actual page. Do NOT invent tests for "
    "features that are not shown here (e.g. file upload, dashboard, login, "
    "admin panel, subscriptions, reports, staffing) unless they are directly "
    "reachable by interacting with the elements above. An out-of-scope test "
    "for a feature that does not exist on this page is a defect, not coverage."
)


def _ui_head_lines(ui_elements: dict, page_title: str) -> list[str]:
    lines: list[str] = ["## Live UI Structure"]
    if page_title:
        lines.append(f"**Page title**: {page_title}")
    headings = ui_elements.get("headings") or []
    if headings:
        lines.append("\n**Headings**: " + " / ".join(headings[:10]))
    return lines


def _build_ui_prompt_block(ui_content: dict) -> str:
    """Markdown UI-element section for the prompt; "" when no data. Never raises."""
    try:
        ui_elements = ui_content.get("ui_elements") or {}
        page_title = ui_content.get("page_title") or ""
        if not ui_elements and not page_title:
            return ""

        lines = _ui_head_lines(ui_elements, page_title)
        form_fields = ui_elements.get("form_fields") or []
        lines.extend(_ui_form_field_lines(form_fields))
        lines.extend(_ui_modal_trigger_lines(form_fields))
        lines.extend(_ui_list_section_lines(ui_elements))
        lines.append(_UI_SCOPE_NOTE)
        return "\n".join(lines)
    except Exception:
        logger.exception("_build_ui_prompt_block failed — skipping UI context")
        return ""
