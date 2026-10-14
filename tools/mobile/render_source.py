"""Start-menu and install-menu source lookups: argument to key, label to key.

Second layer of the ``render_*`` split of ``render.py``; needs only the tables
in :mod:`tools.mobile.render_menu`.
"""

from __future__ import annotations

from tools.mobile.render_menu import INSTALL_SOURCES, MOBILE_SOURCES

#: Which start-menu source each start ARGUMENT implies. One argument, one lane:
#: a tester who pasted their own cases has already answered the menu question,
#: and asking it again is what made the lane read as broken. Order is the order
#: a conflict is reported in.
SOURCE_IMPLICATIONS: tuple[tuple[str, str], ...] = (
    ("cases", "own_cases"),
    ("goal", "explore"),
    ("suite_id", "stored_suite"),
)


def implied_sources(**arguments: object) -> list[str]:
    """The source keys implied by non-empty start arguments, in menu order.

    Empty when nothing was given (so ask the menu), ONE when the tester has
    already said what they want, and more than one when two arguments disagree
    -- which is a narrower QUESTION, never a guess. ``run_id`` is deliberately
    not in the table: it is already owned by ``handle_mobile_test``'s own resume
    branch, and one argument with two owners is how the two drift.
    """
    out: list[str] = []
    for name, key in SOURCE_IMPLICATIONS:
        if str(arguments.get(name) or "").strip():
            out.append(key)
    return out


def source_labels() -> list[str]:
    """The menu lines, for an elicitation dialog's option list."""
    return [line for _key, line in MOBILE_SOURCES]


def source_for_label(label: str) -> str:
    """The source KEY for a dialog label, or ``""``.

    A dialog returns the label it displayed, so the mapping back to a key has to
    live next to the labels rather than in the handler -- the shape that let a
    device picker return a name no branch recognised.
    """
    wanted = str(label or "").strip()
    for key, line in MOBILE_SOURCES:
        if line == wanted or key == wanted:
            return key
    return ""


def install_source_for_label(label: str) -> str:
    """The install-source KEY for a key or a displayed line, or ``""``.

    Symmetric with :func:`source_for_label`, and for the same reason: the host
    sends back what it was shown, and only a KEY survives a UI that relabels
    and reorders. It replaced a POSITIONAL lookup that also closed a second,
    quieter defect: the start menu and this menu share the ``source`` argument,
    and the install stage tried its own number lookup FIRST -- so a start-menu
    answer of "3" (``own_cases``) was read here as ``app_tester``, whether or
    not the host had preserved the order.
    """
    wanted = str(label or "").strip()
    for key, line in INSTALL_SOURCES:
        if line == wanted or key == wanted:
            return key
    return ""
