"""The per-app profile: every word the engine must never know.

A profile is a JSON data file. It names the package, where the app writes its own
event log on the device, which logcat tag it uses, and -- above all -- the regular
expressions of the app's prose grammar: how it writes a data-layer call, a model
request, a usage line, a turn boundary, a card push. The engine (``grammar.py``)
holds a stream NAME per pattern and the parse algorithm; the pattern TEXT comes from
here, compiled once. A missing or invalid pattern disables that one stream and is
listed under "Can this capture be trusted"; it never raises.

Where profiles live. ``tools/mobile_evidence/profiles/*.json`` in this private
repository (excluded from the public distribution), then the tester's own
``~/.qa-agents/mobile/profiles/*.json`` (``paths.sub("profiles")``). A dist install
therefore has no profile until its owner writes one, and every evidence section
says so. Selection is by EXACT package name.

This module knows no app. Its only literals are field names and stream names.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PROFILES_DIR = Path(__file__).with_name("profiles")

#: Every stream the grammar can read. A profile may leave any of them out; the ones in
#: REQUIRED_STREAMS make the difference between "some evidence" and "no usable evidence".
STREAMS = (
    "bind_req",
    "bind_res",
    "bind_err",
    "bind_retry",
    "call_req",
    "call_res",
    "llm_req",
    "llm_res",
    "llm_frame",
    "llm_err",
    "prompt_msg",
    "prompt_user",
    "usage",
    "note",
    "agent_turn",
    "agent_answer",
    "agent_fail",
    "tool_invoke",
    "tool_done",
    "flow_state",
    "flow_field",
    "card_push",
    "net",
    "hash_head",
    "served_model",
    "tool_call",
    "data_line",
)

#: The minimum for a parse worth showing: a model request, its reply, a usage line and
#: the turn boundary that attributes them.
REQUIRED_STREAMS = ("llm_req", "llm_res", "usage", "agent_turn")

#: Patterns that must match across lines: the app logs a multi-line body as ONE event.
DOTALL_STREAMS = frozenset(
    {
        "bind_res",
        "bind_err",
        "call_res",
        "llm_res",
        "llm_frame",
        "llm_err",
        "prompt_msg",
        "prompt_user",
        "note",
        "agent_turn",
        "agent_answer",
        "agent_fail",
        "tool_invoke",
        "tool_done",
        "flow_state",
    }
)

MAX_PROFILE_BYTES = 256 * 1024


@dataclass
class Profile:
    """One app's vocabulary. Every field has a safe default so a partial file still loads."""

    name: str = ""
    package: str = ""
    log_dir_pattern: str = ""
    segment_name_regex: str = ""
    logcat_tag: str = ""
    log_prefix: str = ""
    patterns: dict = field(default_factory=dict)
    turn_markers: dict = field(default_factory=dict)
    log_failures: list = field(default_factory=list)
    runlog_lanes: list = field(default_factory=list)
    runlog_bad: list = field(default_factory=list)
    env_by_host: dict = field(default_factory=dict)
    rates: dict = field(default_factory=dict)
    rates_note: str = ""
    structured: dict = field(default_factory=dict)
    source: str = ""

    def log_dir(self) -> str:
        return str(self.log_dir_pattern or "").replace("{package}", self.package)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def from_dict(body: dict, source: str = "") -> Profile:
    """A Profile from a decoded JSON object; unknown keys are ignored, missing ones default."""
    body = _as_dict(body)
    patterns = {
        str(key): str(value)
        for key, value in _as_dict(body.get("patterns")).items()
        if isinstance(value, str)
    }
    return Profile(
        name=_as_str(body.get("name")),
        package=_as_str(body.get("package")),
        log_dir_pattern=_as_str(body.get("log_dir_pattern")),
        segment_name_regex=_as_str(body.get("segment_name_regex")),
        logcat_tag=_as_str(body.get("logcat_tag")),
        log_prefix=_as_str(body.get("log_prefix")),
        patterns=patterns,
        turn_markers={
            str(k): str(v) for k, v in _as_dict(body.get("turn_markers")).items()
        },
        log_failures=[
            list(item)
            for item in _as_list(body.get("log_failures"))
            if isinstance(item, (list, tuple)) and len(item) == 2
        ],
        runlog_lanes=[
            list(item)
            for item in _as_list(body.get("runlog_lanes"))
            if isinstance(item, (list, tuple)) and len(item) == 2
        ],
        runlog_bad=[str(item) for item in _as_list(body.get("runlog_bad"))],
        env_by_host={
            str(k): str(v) for k, v in _as_dict(body.get("env_by_host")).items()
        },
        rates={
            str(k): list(v)
            for k, v in _as_dict(body.get("rates")).items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        },
        rates_note=_as_str(body.get("rates_note")),
        structured=_as_dict(body.get("structured")),
        source=source,
    )


def load(path: object) -> dict:
    """``{"error", "content": Profile}`` from a JSON file. Never raises."""
    try:
        target = Path(str(path))
        if not target.is_file():
            return {"error": "No profile file at " + str(target)[:200], "content": None}
        if target.stat().st_size > MAX_PROFILE_BYTES:
            return {
                "error": "Profile file too large: " + str(target)[:200],
                "content": None,
            }
        body = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            return {
                "error": "Profile is not a JSON object: " + str(target)[:200],
                "content": None,
            }
        profile = from_dict(body, source=str(target))
        if not profile.package:
            return {
                "error": "Profile names no package: " + str(target)[:200],
                "content": None,
            }
        return {"error": None, "content": profile}
    except (OSError, ValueError) as exc:
        return {"error": "Could not read profile: " + str(exc)[:200], "content": None}


def compile(profile: Profile) -> dict:  # noqa: A001 - the reference name, kept on purpose
    """Compile every pattern the profile carries.

    ``{"error": None, "content": {"patterns": {stream: re.Pattern}, "disabled": {stream:
    reason}, "missing": [required streams absent]}}``. A bad regex disables that one stream
    with its reason; the rest still compile. Never raises.
    """
    compiled: dict = {}
    disabled: dict = {}
    for stream in STREAMS:
        text = profile.patterns.get(stream)
        if not text:
            continue
        flags = re.S if stream in DOTALL_STREAMS else 0
        try:
            compiled[stream] = re.compile(text, flags)
        except re.error as exc:
            disabled[stream] = "pattern does not compile: " + str(exc)[:120]
    for marker, text in (profile.turn_markers or {}).items():
        try:
            compiled["turn_" + str(marker)] = re.compile(str(text))
        except re.error as exc:
            disabled["turn_" + str(marker)] = (
                "pattern does not compile: " + str(exc)[:120]
            )
    missing = [stream for stream in REQUIRED_STREAMS if stream not in compiled]
    return {
        "error": None,
        "content": {"patterns": compiled, "disabled": disabled, "missing": missing},
    }


def _search_dirs(extra: list | None) -> list:
    dirs = [PROFILES_DIR]
    try:
        from tools.mobile import paths

        dirs.append(paths.sub("profiles"))
    except Exception:  # pragma: no cover - the lane is always present in this tree
        dirs.append(Path.home() / ".qa-agents" / "mobile" / "profiles")
    for item in extra or []:
        dirs.append(Path(str(item)))
    return dirs


def available(search_dirs: list | None = None) -> list:
    """Every loadable profile, in search order. Unreadable files are skipped and logged."""
    found: list = []
    for directory in _search_dirs(search_dirs):
        try:
            candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
        except OSError:
            candidates = []
        for candidate in candidates:
            loaded = load(candidate)
            if loaded.get("error"):
                logger.info(
                    "mobile_evidence.profiles: skipped %s: %s",
                    candidate,
                    loaded["error"],
                )
                continue
            found.append(loaded["content"])
    return found


def profile_for(package: object, search_dirs: list | None = None) -> Profile | None:
    """The profile whose package equals *package* exactly, or None."""
    wanted = str(package or "").strip()
    if not wanted:
        return None
    for profile in available(search_dirs):
        if profile.package == wanted:
            return profile
    return None
