"""Diagnostics page: the collapsed diagnostics block assembled from its parts."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.rdiag_a11y import _a11y_section
from tools.mobile.rdiag_capture import _capture_table
from tools.mobile.rdiag_env import _env_section
from tools.mobile.rdiag_facts import _facts_strip, _FactsIn
from tools.mobile.rdiag_logs import _logs_section
from tools.mobile.rdiag_part import (
    HEAD_A11Y,
    HEAD_CAPTURE,
    HEAD_ENV,
    HEAD_EVIDENCE,
    HEAD_IMAGES,
    HEAD_LOGS,
    HEAD_NETWORK,
    _diag_part,
    _evidence_html,
    _network_html,
    _timings_part,
)
from tools.mobile.report_media import _image_notes_html
from tools.mobile.report_widgets import _PageData, _Run


class _DiagIn(NamedTuple):
    """What the diagnostics block reads besides the run."""

    screens: object
    lease: dict
    partial: bool


# The first four diagnostics parts.
def _diag_head(run: _Run) -> str:
    capture = _capture_table(run.library)
    notes = _image_notes_html()
    evidence = _evidence_html(run.loaded)
    network = _network_html(run)
    return (
        _diag_part("screen-capture", HEAD_CAPTURE, capture)
        + _diag_part(
            "images",
            HEAD_IMAGES,
            ('<ul class="plain">' + notes + "</ul>") if notes else "",
        )
        + _diag_part("evidence", HEAD_EVIDENCE, evidence)
        + _diag_part("network", HEAD_NETWORK, network)
    )


# The last diagnostics parts: logs, accessibility, timings, environment.
def _diag_tail(parts: tuple) -> str:
    logs, a11y, timings, env = parts
    return (
        _diag_part("logs", HEAD_LOGS, logs)
        + _diag_part("diag-a11y", HEAD_A11Y, a11y)
        + timings
        + _diag_part("env", HEAD_ENV, env)
    )


# The page data is built by the caller so the sections probe still sees
# _PageData under the env part; the second parameter is that strip input.
def _env_block(run: _Run, strip: _FactsIn) -> str:
    return _facts_strip(strip) + _env_section(run.manifest, run.loaded)


def _diagnostics_html(run: _Run, diag: _DiagIn) -> str:
    """The collapsed diagnostics. Built after the journey and the case list, so
    every image note those pictures raised is in the book by now.
    """
    screens = diag.screens
    a11y = _a11y_section(screens)
    head = _diag_head(run)
    lease, partial = diag.lease, diag.partial
    env = _env_block(
        run,
        _FactsIn(
            run.run_id,
            run.manifest,
            lease,
            partial,
            _PageData(loaded=run.loaded, coverage=run.coverage),
        ),
    )
    logs_html = _logs_section(run.manifest, run.facts, len(run.facts))
    return head + _diag_tail((logs_html, a11y, _timings_part(run.manifest), env))
