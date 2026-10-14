"""Resolve-or-ask: one exact answer, or a question -- never a guess.

Every mobile tool that needs an app, package, device, build flavour, release or
install source decides through :func:`resolve_or_ask`. The rule is the owner's
(2026-10-05): when there is not exactly ONE answer, stop and ask the tester.

* an explicit value always wins;
* a candidate whose key equals the text is exact;
* otherwise every candidate matching the text counts: one -> use it and say so,
  several -> ask, none -> say so and ask;
* a LABEL that equals the text is not exact: "acme" must not pick the
  production flavour when a qa and a master flavour are installed too.

The question travels through an injected :data:`AskCb` (the host builds it from
MCP elicitation), so this module imports nothing from ``handlers``.
When the callback is missing, declined or raising, the caller gets the
unresolved :class:`Resolution` back and shows :meth:`Resolution.menu` instead.
"""

from __future__ import annotations

import logging
from typing import Sequence

from tools.mobile.resolve_match import (  # noqa: F401
    _DEFAULT,
    PickOpts,
    _pick,
    decide,
    exact_key_match,
    fuzzy_match,
)
from tools.mobile.resolve_types import (  # noqa: F401
    ASK,
    ASK_FAILURES,
    MAX_ASK_OPTIONS,
    NONE,
    RESOLVED,
    AskCb,
    Candidate,
    Resolution,
)

log = logging.getLogger(__name__)


async def _ask_user(first: Resolution, opts: PickOpts) -> Resolution | None:
    """The tester's pick as a Resolution, or None when nobody answered."""
    ask, shown = opts.ask, first.options[:MAX_ASK_OPTIONS]
    if ask is None:
        return None
    try:
        answer = await ask(first.question, [c.display() for c in shown])
    except ASK_FAILURES:  # a transport that cannot ask must degrade, not crash
        log.warning("resolve_or_ask: ask callback failed", exc_info=True)
        return None
    picked = _pick(shown, answer)
    if picked is None:
        return None
    return Resolution(RESOLVED, picked.key, (picked,), how="asked")


async def resolve_or_ask(
    text: str, candidates: Sequence[Candidate], opts: PickOpts = _DEFAULT
) -> Resolution:
    """Decide; when the decision is a question and ``opts.ask`` exists, put it.

    An explicit value never needs asking: callers holding one call
    :func:`decide` with ``explicit=`` directly.

    Never raises for a failing transport: the unresolved Resolution comes back
    and its ``menu()`` is the text fallback.
    """
    first = decide(text, candidates, opts)
    if first.resolved or opts.ask is None or not first.options:
        return first
    asked = await _ask_user(first, opts)
    return first if asked is None else asked
