"""The prompt block of a pruned screen and the verdict on its usability."""

from __future__ import annotations

import logging

from tools.mobile import perception_limits
from tools.mobile.perception_fit import _trim_to_fit
from tools.mobile.perception_limits import (
    MAX_ELEMENTS,
    MAX_PACKET_HEADROOM,
    MIN_USABLE_ELEMENTS,
)
from tools.mobile.perception_line import _rows, element_line
from tools.mobile.perception_render import render_lines
from tools.mobile.perception_summary import _summary_lines
from tools.mobile.perception_text import _count
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mobile.perception")


def _header_lines(content: dict) -> list[str]:
    """The one ``screen | package | activity`` line that heads the block."""
    header = [
        "screen " + str(content.get("screen_id") or "?"),
        "package " + str(content.get("package") or "?"),
    ]
    activity = str(content.get("activity") or "")
    if activity:
        header.append("activity " + activity)
    return [" | ".join(header)]


# The pruned screen, wrapped for a prompt. ``""`` when there is nothing.
#
# The RAW XML never reaches this function's output: only the fields ``prune``
# produced, each already neutralised. That is what makes the packet compact
# and what a Phase-3 test asserts by looking for ``<node`` in a packet.
#
# COERCED HERE, at the top, because this is the function whose `except`
# discards the screen. `_rows` was first called only inside
# `render_lines`, which sits BELOW the `fields` / `controls`
# comprehensions -- so a malformed element list still raised on
# `element.get` before the coercion was ever reached, and the tester's
# model got an empty block for a reason nothing named. The test that was
# supposed to grade this drove `render_lines` directly and never saw it.
# Measure at the transport, not at the function.
def to_prompt_block(pruned: object) -> str:
    """The pruned screen, wrapped for a prompt. ``""`` when there is nothing."""
    try:
        content = pruned if isinstance(pruned, dict) else {}
        if content.get("content") and isinstance(content.get("content"), dict):
            content = content["content"]
        elements = _rows(content.get("elements"))
        if not elements:
            return ""
        lines = _header_lines(content) + _summary_lines(elements)
        return _fit_block(content, elements, lines)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.perception.to_prompt_block failed")
        return ""


# WHAT ACTUALLY HAPPENED, not what the old single budget would have done.
# "Only the first MAX_ELEMENTS are shown" became false in both
# directions once the packet gained headroom: it denied elements it was
# rendering on a screen that dropped nothing, and it understated a
# 224-element loss as 150. And `truncated` alone cannot say WHICH side of
# the panel was thinned, which is the difference between "ask for a
# scroll" and "plan against this screen as if it were complete".
def _drops_of(content: dict) -> tuple:
    """``(inside, outside, unknown)`` elements the prune dropped, as ints."""
    return (
        int(_count(content.get("dropped_inside_panel"))),
        int(_count(content.get("dropped_outside_panel"))),
        int(_count(content.get("dropped_unclassified"))),
    )


# BOUNDED BEFORE THE LOOP. The loop re-joins the body each iteration, so
# it is quadratic in the number of lines: measured 0.001s at 225
# elements and 50s at 60000, which a stored packet can carry even though
# `prune` cannot produce it. No live packet exceeds this bound, so the
# cut costs nothing that was going to be rendered anyway.
#
# The SAME elements with no references, for the branch that cannot
# afford the legend. Built once; the loop chooses between them.
def _rendered_pair(elements: list) -> tuple:
    """``(rendered, plain)`` element lines, each cut to the packet bound."""
    bound = MAX_ELEMENTS + MAX_PACKET_HEADROOM
    rendered = render_lines(elements)[:bound]
    plain = [element_line(element) for element in elements][:bound]
    return rendered, plain


# BEFORE the element lines, because `wrap_untrusted` caps the block at
# :data:`MAX_BLOCK_CHARS` and a 225-element packet runs past it -- so a
# notice appended at the END was cut off on exactly the packets that
# dropped the most, which are the only ones it exists for. Measured
# while writing the fixture that grades it. It also reads better here: a
# planner learns the screen is incomplete before it reads 225 rows
# rather than after.
#
# THE BLOCK OWNS ITS OWN SIZE, so the count in the notice is true by
# construction rather than by hoping nothing downstream trims it: the
# lines are trimmed in `_trim_to_fit`, to what the budget can actually
# carry, and the notice reports what was emitted.
def _fit_block(content: dict, elements: list[dict], head: list[str]) -> str:
    """The wrapped packet: *head*, then the notice and as many element lines as fit."""
    drops = _drops_of(content)
    text = _trim_to_fit(list(head), _rendered_pair(elements), (content, drops))
    return wrap_untrusted("screen", text, limit=perception_limits.MAX_BLOCK_CHARS)


def _verdict(elements: list) -> str:
    """The reason a non-empty element list is not enough, or ``""``."""
    if len(elements) < MIN_USABLE_ELEMENTS:
        return "few_elements"
    if any("webview" in str(e.get("cls") or "").lower() for e in elements):
        return "webview"
    clickable = [e for e in elements if e.get("clickable")]
    unlabeled = [
        e for e in clickable if not (e.get("text") or e.get("desc") or e.get("label"))
    ]
    if len(unlabeled) >= 2 and 2 * len(unlabeled) >= len(clickable):
        return "unlabeled_controls"
    return ""


# Why the element list is NOT enough to act on, or ``""`` when it is.
#
# The screenshot goes to the model only when this names a reason, the model
# asked for it, or a ``visual`` assert is being judged. A wrong ``""`` costs
# a blind step and a wrong reason costs one image, so the rules lean towards
# naming a reason. Never raises: a screen this cannot read is ``no_dump``,
# which attaches the picture.
def dump_unusable(screen: object) -> str:
    """Why the element list is NOT enough to act on, or ``""`` when it is."""
    try:
        body = screen if isinstance(screen, dict) else None
        if body is None or body.get("error"):
            return "no_dump"
        if isinstance(body.get("content"), dict):
            body = body["content"]
        elements = _rows(body.get("elements"))
        return _verdict(elements) if elements else "no_elements"
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.perception.dump_unusable failed")
        return "no_dump"
