"""Compile the patterns a profile carries (split out of ``profiles.py``)."""

from __future__ import annotations

import re

from tools.mobile_evidence.profiles_model import (
    DOTALL_STREAMS,
    REQUIRED_STREAMS,
    STREAMS,
    Profile,
)


def _compile_streams(profile: Profile) -> tuple[dict, dict]:
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
    return compiled, disabled


def _compile_turns(profile: Profile, compiled: dict, disabled: dict) -> None:
    for marker, text in (profile.turn_markers or {}).items():
        try:
            compiled["turn_" + str(marker)] = re.compile(str(text))
        except re.error as exc:
            disabled["turn_" + str(marker)] = (
                "pattern does not compile: " + str(exc)[:120]
            )


def compile(profile: Profile) -> dict:  # noqa: A001 - the reference name, kept on purpose
    """Compile every pattern the profile carries.

    ``{"error": None, "content": {"patterns": {stream: re.Pattern}, "disabled": {stream:
    reason}, "missing": [required streams absent]}}``. A bad regex disables that one stream
    with its reason; the rest still compile. Never raises.
    """
    compiled, disabled = _compile_streams(profile)
    _compile_turns(profile, compiled, disabled)
    missing = [stream for stream in REQUIRED_STREAMS if stream not in compiled]
    return {
        "error": None,
        "content": {"patterns": compiled, "disabled": disabled, "missing": missing},
    }
