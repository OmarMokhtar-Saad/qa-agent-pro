"""The one description of the HTML report a tester is ever shown.

Fifteenth layer of the ``render_*`` split of ``render.py``.

``report_line`` -- the ONE place the HTML report is described to a tester.

Three shapes and no fourth: a path was written, a render was attempted and
failed, or none was attempted. It never names a `.html` file that does not
exist -- the failure mode the text this replaced was carefully avoiding, and
the reason its replacement is a rewrite rather than an edit.

The four inputs travel as one private :class:`_ReportRef` bundle (path, partial,
opened, error) so the function stays inside the three-parameter limit; the only
caller is ``summary_block``.
"""

from __future__ import annotations

from typing import NamedTuple


class _ReportRef(NamedTuple):
    path: str = ""
    partial: bool = False
    opened: bool = False
    error: str = ""


_FAILED_TAIL = (
    " Every verdict above is read straight from the run's own "
    "checkpoint files and is unaffected."
)
_NONE_TEXT = (
    "No HTML report was written for this call. Ask for one at any time "
    "with `qa_mobile_status` and `report_now=true`: it is built from the "
    "run's own checkpoint files, so a mid-run report works too."
)
_PARTIAL_NOTE = "It covers the cases finished so far; ask again later for the rest. "
_OPENED_NOTE = "It was opened in your browser. "
_UNOPENED_NOTE = (
    "Open it in a browser: it is one self-contained folder — the "
    "page and its media together — so it works offline and can be zipped "
    "as it stands. "
)
_WRITTEN_TAIL = (
    "Every screen in it is shown as the picture the lane captured, with a "
    "drawing composed from the element list beneath it; each step also carries "
    "the device's own recording of it, recorded smaller than the screen and "
    "captioned to say so, and a step that types a credential stores no picture "
    "at all, and says so."
)


def _failed_text(ref: _ReportRef) -> str:
    return (
        "⚠️ The HTML report could not be written: " + str(ref.error)[:300] + _FAILED_TAIL
    )


def _none_text(ref: _ReportRef) -> str:
    return _NONE_TEXT


def _written_text(ref: _ReportRef) -> str:
    return (
        ("Partial report" if ref.partial else "Report")
        + " — `"
        + str(ref.path)[:400]
        + "`\n\n"
        + (_PARTIAL_NOTE if ref.partial else "")
        + (_OPENED_NOTE if ref.opened else _UNOPENED_NOTE)
        + _WRITTEN_TAIL
    )


def report_line(ref: _ReportRef) -> str:
    """The ONE place the HTML report is described to a tester."""
    if ref.error:
        return _failed_text(ref)
    if not ref.path:
        return _none_text(ref)
    return _written_text(ref)
