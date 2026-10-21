"""The app's own reported events, as proof lines for the final packet (H09).

``evidence/events.ndjson`` is what the app itself logged during the run. Any app
that writes one gets the same generic summary: how many records, how many were
warnings or errors, the HTTP statuses it logged, and the latest config values it
reported (``env``, ``brand`` ...). Nothing here knows an app; it only counts and
quotes, and never raises (evidence is not a verdict).
"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from pathlib import Path

from tools.mobile import paths

_HTTP = re.compile(r"\bHTTP[ /:]*([1-5]\d\d)\b")
_CONFIG_KEYS = ("env", "brand", "language")
_BAD_LEVELS = frozenset({"W", "WARN", "WARNING", "E", "ERROR", "F", "FATAL"})


def _records(path: Path) -> list:
    out: list = []
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in itertools.islice(handle, 20000):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _latest_config(records: list) -> dict:
    """The newest value of each config key any record's ``fields`` reported."""
    seen: dict = {}
    for rec in records:
        fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        seen.update({k: str(fields[k]) for k in _CONFIG_KEYS if k in fields})
    return seen


def _statuses(records: list) -> Counter:
    return Counter(
        m.group(1) for r in records for m in _HTTP.finditer(str(r.get("msg") or ""))
    )


def _line_of(records: list) -> str:
    bad = sum(1 for r in records if str(r.get("level")).upper() in _BAD_LEVELS)
    parts = [f"{len(records)} events", f"{bad} warning/error"]
    http = _statuses(records)
    if http:
        parts.append("HTTP " + ", ".join(f"{k} x{v}" for k, v in sorted(http.items())))
    config = _latest_config(records)
    if config:
        parts.append("app config " + ", ".join(f"{k}={v}" for k, v in config.items()))
    return "App-reported events: " + "; ".join(parts) + "."


def events_line(run_id: str) -> str:
    """One quotable line about the app's own events, or ``""`` when it has none."""
    try:
        path = paths.run_dir(run_id) / "evidence" / "events.ndjson"
        records = _records(path) if path.is_file() else []
    except (OSError, ValueError):
        return ""
    return _line_of(records) if records else ""
