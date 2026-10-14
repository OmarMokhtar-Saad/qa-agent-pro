"""The whole report for one capture: ``build`` never raises."""

from __future__ import annotations

from typing import NamedTuple

from config.settings_guard import never_raise
from tools.mobile_evidence.grammar_consts import _LOGCAT_FALLBACK_GAP, GAPS, SCHEMA
from tools.mobile_evidence.grammar_net import GrammarKit
from tools.mobile_evidence.grammar_parse import parse
from tools.mobile_evidence.grammar_prompts import _logcat_prompts
from tools.mobile_evidence.grammar_read import read_logcat, read_ndjson
from tools.mobile_evidence.grammar_totals import totals
from tools.mobile_evidence.grammar_turns import attribute_turns


class CaptureSources(NamedTuple):
    """The two captures one report is built from; each a path or an iterable of lines."""

    ndjson: object = None
    logcat: object = None


def env_from_traffic(bindings, profile):
    """(effective env, [hosts seen]) -- None when nothing left the device."""
    by_host = getattr(profile, "env_by_host", None) or {}
    hosts = []
    for b in bindings or []:
        url = b.get("url") or ""
        if "://" not in url:
            continue
        host = url.split("://", 1)[1].split("/", 1)[0]
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        return None, []
    envs = {by_host.get(h) for h in hosts}
    envs.discard(None)
    if not envs:
        return None, hosts
    return (envs.pop() if len(envs) == 1 else "mixed"), hosts


def _report_counts(parsed: dict, events: list) -> dict:
    """The headline counts of one parsed capture."""
    return {
        "events": len(events),
        "appRuns": len(parsed["appRuns"]),
        "turns": len(parsed["turns"]),
        "llmCalls": len(parsed["llm"]),
        "bindingCalls": len(parsed["bindings"]),
        "toolCalls": len(parsed["tools"]),
        "notes": len(parsed["notes"]),
        "errors": len(parsed["errors"]),
        "narrated": len(parsed["runlog"]),
    }


def _parsed_sections(parsed: dict) -> dict:
    """The parsed streams the report passes through unchanged."""
    keys = (
        "turns",
        "utterances",
        "answers",
        "flowStates",
        "cards",
        "appRuns",
        "llm",
        "bindings",
        "tools",
        "notes",
        "errors",
        "runlog",
        "unresolvedBindings",
        "configs",
    )
    return {key: parsed[key] for key in keys}


class _Capture(NamedTuple):
    """One capture read and parsed: where it came from and everything it held."""

    source: str
    manifests: list
    events: list
    malformed: int
    checkpoints: list
    parsed: dict
    prompts: list

    def last_manifest(self):
        """The newest session manifest, or None."""
        return self.manifests[-1] if self.manifests else None


def _capture(sources: tuple, profile, compiled) -> _Capture:
    """Read and parse a capture: the structured log when there is one, else the logcat."""
    ndjson, logcat = sources
    if ndjson is not None:
        manifests, events, malformed, checkpoints = read_ndjson(ndjson)
        parsed = attribute_turns(parse(events, logcat, GrammarKit(profile, compiled)))
        return _Capture("ndjson", manifests, events, malformed, checkpoints, parsed, [])
    manifests, events, malformed, checkpoints = read_logcat(logcat, profile)
    parsed = attribute_turns(parse(events, None, GrammarKit(profile, compiled)))
    prompts = _logcat_prompts(logcat, profile, parsed)
    return _Capture(
        "logcat", manifests, events, malformed, checkpoints, parsed, prompts
    )


def _gaps(source: str) -> list:
    """The named gaps of a capture; a logcat-only one has one more."""
    gaps = list(GAPS)
    if source == "logcat":
        gaps.append(dict(_LOGCAT_FALLBACK_GAP))
    return gaps


def _run_section(cap: _Capture, profile) -> dict:
    """The ``run`` block of the report: source, environment claims and traffic hosts."""
    parsed = cap.parsed
    effective_env, hosts = env_from_traffic(parsed["bindings"], profile)
    claimed_env = (cap.last_manifest() or {}).get("env")
    return {
        "source": cap.source,
        "mergedNetworkLines": parsed.get("mergedNetworkLines") or 0,
        "malformedLines": cap.malformed,
        "turnsFromNarration": bool(parsed.get("turnsFromNarration")),
        "appRuns": len(parsed["appRuns"]),
        "env": effective_env or claimed_env,
        "envClaimed": claimed_env,
        "envFromTraffic": effective_env,
        "envDisagrees": bool(
            effective_env and claimed_env and effective_env != claimed_env
        ),
        "hosts": hosts,
        "disabled": parsed.get("disabled") or [],
    }


def _report(cap: _Capture, profile) -> dict:
    """The report dict of one parsed capture, in its key order."""
    parsed = cap.parsed
    return {
        "schema": SCHEMA,
        "run": _run_section(cap, profile),
        "manifest": cap.last_manifest(),
        "manifests": cap.manifests,
        "counts": _report_counts(parsed, cap.events),
        "tokens": totals(parsed),
        "systemPrompts": cap.prompts,
        **_parsed_sections(parsed),
        "checkpoints": cap.checkpoints,
        "clock": parsed.get("clock") or {},
        "gaps": _gaps(cap.source),
    }


def build(kit: GrammarKit, sources: CaptureSources = CaptureSources()) -> dict:
    """Parse one capture into the report dict every downstream reader consumes.

    ``sources.ndjson`` and ``sources.logcat`` are each a path or an iterable of lines;
    with neither, or with input that cannot be read, the result is
    ``{"error", "content": None}``.
    """
    # noqa: BLE001 - the contract is never to raise
    with never_raise() as g:
        if sources.ndjson is None and sources.logcat is None:
            return {
                "error": "no capture: neither a structured log nor a logcat was given",
                "content": None,
            }
        cap = _capture((sources.ndjson, sources.logcat), kit.profile, kit.compiled)
        return {"error": None, "content": _report(cap, kit.profile)}
    return {
        "error": "could not parse the capture: " + str(g.exc)[:200],
        "content": None,
    }
