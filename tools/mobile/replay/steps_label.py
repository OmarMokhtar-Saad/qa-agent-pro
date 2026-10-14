"""Replay engine: the label and node the destructive guard judges. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import _Step
from tools.mobile.replay.elements import (
    TouchSpec,
    _clamp_source,
    _touch_point,
    actuated_element,
    element_label,
)


# The node the action ACTUATES where the touch resolved one, else the node it
# NAMES. The same derivation the actuated-node guard already uses -- not a
# second one.
def _actuated_node(step: _Step, screen: dict | None) -> Any:
    """The node the touch of *step* lands on, ``None`` with no element."""
    if step.element is None:
        return None
    spec = TouchSpec(step.op, step.action, step.element, _clamp_source(screen))
    return actuated_element(screen, _touch_point(spec))


# The actuated node is judged on its OWN strings -- no
# `screen`, so no containment. It is an ANCESTOR by
# construction, and an ancestor's contained text is every
# word inside it: with containment, tapping "Notifications"
# inside a clickable settings list that also held "Delete
# account" was refused (an executing review measured it).
# The named element keeps its containment rule; the residual
# is a wrapper with no strings of its own whose destructive
# word lives only in a SIBLING of the child that was named.
# Appended only when it IS a different node. When the named
# element is itself the smallest clickable under the finger
# (the common case) appending it again would join its label
# to itself, and `destructive_hit` matches multi-word terms
# as a token RUN: "Money ... send" followed by "Money ..."
# manufactures "send money" at the seam -- a false stop, and
# a stop is terminal for a scripted case.
def _guard_label(step: _Step, screen: dict | None) -> tuple[str, Any]:
    """The words the destructive guard judges, and the node it judges: the
    node the action ACTUATES where the touch resolved one, else the one it
    NAMES. Builds the label only; the decision stays in ``_guard_stop``."""
    element = step.element
    labels = [
        actions_mod.action_text(step.action),
        element_label(element, screen),
    ]
    actuated = _actuated_node(step, screen)
    if actuated is not None and actuated is not element:
        labels.append(element_label(actuated))
    judged_node = actuated if actuated is not None else element
    return " ".join(labels).strip(), judged_node
