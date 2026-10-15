from __future__ import annotations

import hashlib
import time
from typing import NamedTuple

from tools.mobile import render as mobile_render
from tools.mobile.report_diagnostics import DEVICE_KINDS
from tools.mobile.report_text import (
    _epoch,
    _short_stamp,
    _text,
    esc,
    fmt_ms,
    last_checkpoint,
)
from tools.mobile.report_verdict import coverage_phrase


def _footer_html(run_id: str, manifest: dict) -> str:
    return (
        "Runner: <code>tools/mobile/case_runner.py</code> · Run: <code>"
        + esc(run_id, 64)
        + "</code> · Driven against <code>"
        + esc(manifest.get("serial") or "(not attached)", 40)
        + "</code><br>Rendered "
        + esc(_short_stamp(time.time()), 40)
        + " from <code>manifest.json</code>, <code>cases/*.json</code> and <code>screens/*.json</code> "
        "in the run's own directory by <code>tools/mobile/report.py</code> on the shared shell in "
        "<code>tools/mobile/report_shell.html</code>. This is one self-contained FOLDER: this page "
        "and the <code>media/</code> beside it, referenced relatively, so the folder can be zipped "
        "and opened offline anywhere."
    )


def _build_label(manifest: dict) -> str:
    for key in ("app_version", "version_name", "build"):
        value = _text(manifest.get(key), 40)
        if value:
            return value
    return ""


def _duration(manifest: dict, cases: list) -> str:
    start = _epoch(manifest.get("created"))
    end = last_checkpoint(cases)
    if start is None or end is None or end < start:
        return ""
    return fmt_ms(int((end - start) * 1000))


def _source_digest(run_id: str, manifest: dict, n_cases: int) -> str:
    """The short stamp that ties a page to the run it was built from."""
    return hashlib.sha1(
        (
            str(run_id) + ":" + str(manifest.get("created") or "") + ":" + str(n_cases)
        ).encode("utf-8")
    ).hexdigest()[:12]


def _title_html(app: str, build: str) -> str:
    """The page title: the app name and, when known, its build."""
    return (
        '<span dir="auto">'
        + esc(app, 80)
        + "</span>"
        + ((' <span class="build">' + esc(build, 40) + "</span>") if build else "")
    )


def _device_what(manifest: dict) -> str:
    device = raw if isinstance(raw := manifest.get("device"), dict) else {}
    model = _text(device.get("model"), 40)
    api = _text(device.get("api"), 8)
    kind = DEVICE_KINDS.get(str(device.get("kind") or ""), "")
    return (
        " · ".join(
            bit
            for bit in (
                model or _text(manifest.get("avd"), 40),
                ("Android API " + api) if api else "",
                kind,
            )
            if bit
        )
        or _text(manifest.get("serial"), 40)
        or "device not recorded"
    )


def _state_text(manifest: dict, cases: list, partial: bool) -> str:
    if not partial:
        return "finished"
    try:
        planned = int(manifest.get("total") or 0)
    except (TypeError, ValueError, OverflowError):
        planned = 0
    waiting = max(0, planned - len(cases))
    return "in progress" + (
        (" — " + str(waiting) + " not yet checkpointed") if waiting else ""
    )


def _head_items(manifest: dict, cases: list) -> list:
    items = [
        ("Device", _device_what(manifest)),
        ("Date", _short_stamp(manifest.get("created"))),
    ]
    took = _duration(manifest, cases)
    if took:
        items.append(("Duration", took))
    return items


def _tail_items(cases: list, coverage: dict | None, explore: bool) -> list:
    items = []
    # The one coverage sentence (run_store owns it): "finished" alone would read
    # as "every case judged" on a run that judged two of three.
    if coverage:
        items.append(("Verdicts", coverage_phrase(coverage)))
    # ONE producer with `verdict_line`'s own tally (`mobile_render.typed_field_tally`)
    # -- the report and the chat reply must never disagree on a field count.
    tally = mobile_render.typed_field_tally(cases, explore).strip()
    if tally:
        items.append(("Typed fields", tally))
    return items


class _MetaIn(NamedTuple):
    """What the report meta line reads."""

    manifest: dict
    cases: list
    partial: bool
    coverage: dict | None = None


def _meta_html(meta: _MetaIn) -> str:
    manifest, cases = meta.manifest, meta.cases
    items = _head_items(manifest, cases)
    items.append(("State", _state_text(manifest, cases, meta.partial)))
    explore = manifest.get("lane") == "explore"
    items += _tail_items(cases, meta.coverage, explore)
    return "".join(
        "<span><b>" + esc(k, 20) + "</b> " + esc(v, 200) + "</span>"
        for k, v in items
        if v
    )
