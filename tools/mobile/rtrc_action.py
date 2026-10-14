"""The action line of a trace entry: one human sentence, with typed text masked."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile.report_text import _text


# **This docstring described the opposite rule until 2026-09-04, and the rule
# it described was itself the defect.** The typed ``text`` was never rendered,
# on the reasoning that a rendering which cannot reach a credential beats one
# that masks it. The cost was that a chat case's report showed ``type -> e14``
# and no tester could tell which question had been asked -- for a lane whose
# whole job is to evidence that the app answered, that is not a report.
#
# So the control moved from absence to masking, and it is doubled:
#
# * :func:`tools.mobile.actions.redact_action` masks at the SOURCE -- on the
#   ``secret`` marker and on ``CREDENTIAL_TERMS`` -- so the packet, the audit
#   log and the checkpoint are covered, not only this page;
# * this function masks again at the RENDER boundary, because a checkpoint
#   written by an older build was never through that rule.
#
# The mutation proof lives on the mask below -- delete it, or either half of
# its condition, and the secret tests in tests/mobile/test_mobile_report.py
# and test_mobile_report_chat.py go red. It does NOT live on the
# ``run_store.redact`` call in :func:`_trace_rows`: deleting that still
# changes no byte of the page, measured, because the mask here fires first.
# Said plainly because the first version of this docstring claimed otherwise.
def _action_line(action: object) -> str:
    """One human line for a trace action, including what it typed."""
    body = action if isinstance(action, dict) else {}
    if not body:
        return _text(action, 80)
    hint = _action_hint(body.get("target"))
    extra = _text(body.get("kind") or body.get("field") or body.get("dir"), 40)
    parts = [_text(body.get("op"), 24) or "?"]
    if hint:
        parts.append("-> " + hint)
    if extra:
        parts.append("(" + extra + ")")
    typed = _typed_literal(body)
    if typed:
        parts.append("\u201c" + typed + "\u201d")
    return " ".join(parts)


def _action_hint(target: object) -> str:
    """The target's label for a trace line: ``""`` when it has none."""
    target = target if isinstance(target, dict) else {}
    # `rid` first, then what was on screen, and the short `id` LAST -- the same
    # order `actions.resolve_target` uses, and for the reader's sake rather
    # than the resolver's: an id is a content hash, so a step table that put it
    # first read `tap -> e9f3a1b2` where the model had also given a resource id
    # a tester could recognise.
    return _text(
        target.get("rid")
        or target.get("text")
        or target.get("desc")
        or target.get("id"),
        60,
    )


def _typed_literal(body: dict) -> str:
    """What a ``type`` action sent, or the mask. ``""`` for any other op."""
    if str(body.get("op") or "") != "type":
        return ""
    if body.get("secret") or actions_mod.is_credential_action(body):
        return actions_mod.SECRET_MASK
    return _text(body.get("text"), 160)
