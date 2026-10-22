"""Elicitation adapters: chooser, free-text asker and the shared-budget pair."""

from __future__ import annotations

import time

from config.settings_guard import never_raise
from handlers import _common, _elicit
from handlers.mcp_srv_progress import _make_progress
from handlers.mcp_srv_state import logger
from handlers.wizard_request import GenerateRequest


def _gated_off() -> bool:
    """True when no dialog may be offered (client gated)."""
    # 2026-08-21: and no dialog at all for a client whose elicitation transport
    # has proven unanswerable in this process (one timeout; gated for the
    # 600 s TTL unless an answer arrives). None is the existing, tested "render the markdown
    # menu" path, so the caller degrades exactly as it does for a client with no
    # elicitation support -- but at 0s instead of 55s. Gated HERE rather than in
    # _make_elicitors so the single-sided call sites are covered too.
    return bool(_elicit.elicit_client_gated())


def _new_budget() -> dict:
    """A fresh per-call elicitation deadline holder."""
    return {"deadline": time.monotonic() + _elicit._ELICIT_CALL_BUDGET_S}


def _to_choice(result):
    """The ChoiceResult for a ``ctx.elicit`` reply (accept / decline)."""
    action = getattr(result, "action", None)
    if action == "accept":
        return _common.ChoiceResult(_common.CHOSEN, value=getattr(result, "data", None))
    return _common.ChoiceResult(_common.DECLINED)


# Adapt ``ctx.elicit`` into the handlers' ``choose(message, options)`` callback,
# mirroring ``_make_progress``. Returns ``None`` when ``_gated_off()``
# is True so the retrofit tools keep the non-interactive behaviour (the wizard
# treats a ``None``/unavailable chooser as 'render the markdown menu').
#
# Elicitation is supported only by some clients (Claude Code, Cursor — NOT
# Claude Desktop). A client without support makes ``ctx.elicit`` raise, which is
# caught here and reported as UNAVAILABLE so the caller falls back to markdown.
def _make_chooser(ctx):
    """Adapt ``ctx.elicit`` into the handlers' ``choose(message, options)`` callback."""
    if _gated_off():
        return None
    _budget = _new_budget()

    async def choose(message, options):
        with never_raise(
            logger, "mcp elicit unavailable for %r", message, exc_info=True
        ) as g:
            result = await ctx.elicit(message, response_type=list(options))
        if g.failed:
            return _common.ChoiceResult(_common.UNAVAILABLE)
        return _to_choice(result)

    choose._elicit_budget = _budget
    return choose


def _make_asker(ctx):
    """Adapt ``ctx.elicit`` into a free-text ``ask_text(message)`` callback —
    the text sibling of _make_chooser (same gating and degradation rules)."""
    if _gated_off():  # see _make_chooser
        return None
    _budget = _new_budget()

    async def ask_text(message):
        with never_raise(
            logger, "mcp elicit(text) unavailable for %r", message, exc_info=True
        ) as g:
            result = await ctx.elicit(message, response_type=str)
        if g.failed:
            return _common.ChoiceResult(_common.UNAVAILABLE)
        return _to_choice(result)

    ask_text._elicit_budget = _budget
    return ask_text


# K1b (2026-08-10). MCP dialogs chain sequentially inside a single tool call --
# the image gate asks twice, the wizard up to three times -- and most chains mix
# an enum dialog with a free-text one. Bounding each dialog separately still let
# one call run 110-220s and die at the client's ~120s idle timeout, so the budget
# has to be shared BETWEEN the two callbacks, which is only possible in a scope
# where both exist. That scope is this function.
#
# The holder is stamped EAGERLY here rather than at the first dialog: these
# callbacks are built as arguments to the handler coroutine, i.e. at the top of
# the tool body before ``_tracked`` awaits anything, so pre-dialog work inside the
# handler (device scans, the Jira fetch, generation) burns the same budget the
# dialogs do. That is the call-entry anchor, without threading a parameter through
# ~15 handler signatures.
#
# Read back by ``mcp_handlers.elicit_wait_s`` via ``cb._elicit_budget``.
# Single-sided call sites keep ``_make_chooser`` / ``_make_asker``: with only one
# callback in play, that factory's own private holder is already per-call correct.
#
# Returns a KWARGS DICT so a call site stays a single expression
# (``**_make_elicitors(ctx)``) -- a tuple would need a preceding statement and a
# restructure of every ``return await _tracked(...)`` it appears in.
def _make_elicitors(ctx):
    """Build BOTH elicitation callbacks for one tool call, sharing ONE budget."""
    choose = _make_chooser(ctx)
    ask_text = _make_asker(ctx)
    if choose is None or ask_text is None:
        return {"choose": choose, "ask_text": ask_text}
    budget = _new_budget()
    choose._elicit_budget = budget
    ask_text._elicit_budget = budget
    return {"choose": choose, "ask_text": ask_text}


def _prepare_call(handler, ctx, params: dict):
    """The handler coroutine of a prepare-style tool, from its argument dict."""
    rest = dict(params)
    feature_or_url = rest.pop("feature_or_url")
    request = GenerateRequest(
        **_make_elicitors(ctx), progress=_make_progress(ctx), **rest
    )
    return handler(feature_or_url, request)
