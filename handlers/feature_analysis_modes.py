"""Feature Analysis modes: the menu, the pick and the input gate."""

from __future__ import annotations

from handlers import _common, _elicit
from handlers.feature_analysis_barekey import _fa_bare_key_reply

_FA_MODES = ("jira", "mobile", "jira_mobile")
_FA_MODE_LABELS = {
    "Jira only": "jira",
    "Mobile only": "mobile",
    "Jira + Mobile": "jira_mobile",
}


def _fa_mode_menu_markdown() -> str:
    """Markdown fallback when no mode is given and elicitation is unavailable."""
    return (
        "## Feature analysis\n\n"
        "Provide a feature description or a Jira/issue URL as `feature_or_url`, "
        "and call `qa_feature_analysis` with one of these as `mode`:\n"
        "- `jira` — analyse a feature description or Jira/issue URL\n"
        "- `mobile` — capture screens from a connected device\n"
        "- `jira_mobile` — merge the ticket with captured screens"
    )


def _fa_normalize_mode(mode: str) -> str:
    mode = (mode or "").strip().lower().replace("+", "_").replace(" ", "_")
    return "jira_mobile" if mode == "jira_and_mobile" else mode


async def _fa_pick_mode(choose: _common.ChooseCb) -> tuple[str, str]:
    """Ask for the mode: ``(mode, reply)``; both empty falls through."""
    picked = await _elicit.elicit_choice(
        choose, "Which Feature Analysis mode?", list(_FA_MODE_LABELS)
    )
    if picked.status == _common.CHOSEN:
        return _FA_MODE_LABELS.get(picked.value or "", ""), ""
    if picked.status == _common.DECLINED:
        return "", "👍 Cancelled — no Feature Analysis mode selected."
    if _common.unanswered(picked):
        # K1 (2026-08-10): with feature_or_url supplied -- the common case --
        # the fallthrough below silently picked mode="jira" and spent a full
        # text-only analysis on an unanswered question. Hand back the menu.
        return "", _fa_mode_menu_markdown()
    return "", ""


async def _fa_resolve_mode(
    mode: str, text: str, choose: _common.ChooseCb
) -> tuple[str, str]:
    """Settle the mode: ``(mode, reply)``; a non-empty reply ends the call."""
    if mode:
        return mode, ""
    mode, reply = await _fa_pick_mode(choose)
    if mode or reply:
        return mode, reply
    if text:
        return "jira", ""  # single-shot back-compat when elicitation is off
    return "", _fa_mode_menu_markdown()


def _fa_input_gate_reply(mode: str, text: str) -> str:
    """The refusal for an unusable mode or input, else ""."""
    if mode not in _FA_MODES:
        return f"⚠️ Unknown mode '{mode}'. Choose one of: {', '.join(_FA_MODES)}."
    if mode in ("jira", "jira_mobile") and not text:
        return "⚠️ Provide a feature description or a Jira/issue URL as feature_or_url."
    return _fa_bare_key_reply(text, mode)
