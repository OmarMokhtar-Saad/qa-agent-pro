"""Component-id comparison and display for input-method ids."""

from __future__ import annotations

from tools.mobile.ime_consts import IME_ID_DISPLAY_CHARS


def display_id(value: object) -> str:
    """An input-method id, shortened for a report cell.

    In this package rather than at each surface: qa-doctor's prose and
    `machine_report`'s rows both print this id, and two independent truncations
    of one value is how two surfaces start disagreeing about what the device
    said.
    """
    text = str(value or "").strip()
    if len(text) <= IME_ID_DISPLAY_CHARS:
        return text
    return text[: IME_ID_DISPLAY_CHARS - 1] + "…"


def _expand_component(value: object) -> str:
    text = str(value or "").strip()
    package, sep, cls = text.partition("/")
    if not sep or not cls:
        return text
    if cls.startswith("."):
        cls = package + cls
    return package + "/" + cls


# Do two Android component ids name the SAME component?
#
# `pkg/.Class` and `pkg/pkg.Class` are the same thing: a class name beginning
# with `.` is relative to the package. Android accepts either and STORES the
# shorthand -- measured 2026-09-04 on a real device, setting the fully
# qualified id and reading `settings get secure default_input_method` back:
#
#     set: io.qaagents.ime/io.qaagents.ime.QaImeService
#     got: io.qaagents.ime/.QaImeService
#
# So a string comparison against the pinned id is false on every install, and
# `preflight`'s `ime_selected` check refused every run on a device where the
# QA keyboard was correctly installed, enabled and active. The whole suite was
# green because the fake answered with the expanded form, which no device
# ever returns.
#
# Blank never matches blank: an absent id is not evidence of anything, and two
# unknowns are not the same component.
def same_component(left: object, right: object) -> bool:
    """Do two Android component ids name the SAME component?"""
    a, b = _expand_component(left), _expand_component(right)
    return bool(a) and a == b
