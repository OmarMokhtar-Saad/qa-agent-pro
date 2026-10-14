"""Read a profile from a decoded JSON object or a file (split out of ``profiles.py``)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.mobile_evidence.profiles_model import MAX_PROFILE_BYTES, Profile


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _str_map(value: object) -> dict:
    return {str(k): str(v) for k, v in _as_dict(value).items()}


def _pairs(value: object) -> list:
    return [
        list(item)
        for item in _as_list(value)
        if isinstance(item, (list, tuple)) and len(item) == 2
    ]


def _text_fields(body: dict) -> dict:
    keys = (
        "name",
        "package",
        "log_dir_pattern",
        "segment_name_regex",
        "logcat_tag",
        "log_prefix",
        "rates_note",
    )
    return {key: _as_str(body.get(key)) for key in keys}


def _table_fields(body: dict) -> dict:
    patterns = {
        str(key): str(value)
        for key, value in _as_dict(body.get("patterns")).items()
        if isinstance(value, str)
    }
    rates = {
        str(k): list(v)
        for k, v in _as_dict(body.get("rates")).items()
        if isinstance(v, (list, tuple)) and len(v) == 2
    }
    return {
        "patterns": patterns,
        "turn_markers": _str_map(body.get("turn_markers")),
        "env_by_host": _str_map(body.get("env_by_host")),
        "structured": _as_dict(body.get("structured")),
        "rates": rates,
    }


def _list_fields(body: dict) -> dict:
    return {
        "log_failures": _pairs(body.get("log_failures")),
        "runlog_lanes": _pairs(body.get("runlog_lanes")),
        "runlog_bad": [str(item) for item in _as_list(body.get("runlog_bad"))],
    }


def from_dict(body: dict, source: str = "") -> Profile:
    """A Profile from a decoded JSON object; unknown keys are ignored, missing ones default."""
    body = _as_dict(body)
    return Profile(
        **_text_fields(body),
        **_table_fields(body),
        **_list_fields(body),
        source=source,
    )


def _failure(message: str) -> dict:
    return {"error": message, "content": None}


def _refusal(target: Path) -> dict | None:
    if not target.is_file():
        return _failure("No profile file at " + str(target)[:200])
    if target.stat().st_size > MAX_PROFILE_BYTES:
        return _failure("Profile file too large: " + str(target)[:200])
    return None


def load(path: object) -> dict:
    """``{"error", "content": Profile}`` from a JSON file. Never raises."""
    try:
        target = Path(str(path))
        bad = _refusal(target)
        if bad:
            return bad
        body = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            return _failure("Profile is not a JSON object: " + str(target)[:200])
        profile = from_dict(body, source=str(target))
        if not profile.package:
            return _failure("Profile names no package: " + str(target)[:200])
        return {"error": None, "content": profile}
    except (OSError, ValueError) as exc:
        return _failure("Could not read profile: " + str(exc)[:200])
