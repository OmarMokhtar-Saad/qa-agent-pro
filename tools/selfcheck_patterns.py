"""Detector patterns of the self-check: what counts as a flag, module, tool or model claim."""

from __future__ import annotations

import re

#: Same two-armed shape as the static scan's ``_TOKEN``: prefix-bound for flag
#: names, suffix-bound for credential names under ANY prefix (the arm that was
#: missing when two ``*_API_KEY`` strings survived P2-I).
_TOKEN = re.compile(
    r"\b(?:(?:QA|JIRA|TESTRAIL|XRAY)_[A-Z0-9_]{2,}"
    r"|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:API_KEY|TOKEN))\b"
)

#: A module a reply points the reader at. Deleted modules cannot be enumerated
#: (they are gone), so the check is INVERTED and needs no list: a reply that
#: names ``<something>.py`` must name a file that exists.
_MODULE = re.compile(r"\b((?:[a-z_][a-z0-9_]*/)*[a-z_][a-z0-9_]*\.py)\b")

#: ``llm.<symbol>`` -- checked against the module's real namespace. ``llm.py``
#: is 93 lines and exports four symbols; ``ask`` / ``ask_json`` / ``ask_vision``
#: were deleted with every backend on 2026-08-16.
_LLM_SYMBOL = re.compile(r"\bllm\.([a-z_][a-z0-9_]*)")

#: A tool name. Underscore-bound, plus the one registered hyphenated name. A
#: bare ``qa-[a-z]+`` arm matched the PRODUCT name ("qa-agents") and the fixture
#: seed when this was measured, which is the shape of a check reacting to a
#: string it sent itself.
_TOOL = re.compile(r"\bqa_[a-z0-9_]+\b|\bqa-doctor\b")

#: Nothing in this tree can call a model, so a tester-facing reply has no
#: legitimate reason to name one, nor to send the reader off to fix a "backend".
#: The ``claude-`` arm requires a model FAMILY or a separated version, never
#: bare digits: the first spelling was ``claude-[a-z0-9.-]{3,}``, which matched
#: ``claude-501`` out of the install path ``/tmp/claude-501/...`` that
#: ``qa-doctor`` prints. ``/tmp/claude-<uid>/`` is the standard Claude Code
#: scratchpad layout, so that spelling reported a false finding for any tester
#: whose checkout sat under one -- on the very tool a confused tester is told
#: to run first. Matches claude-opus-5, claude-sonnet-5,
#: claude-haiku-4-5-20251001, claude-fable-5-1, claude-3-5-sonnet; not
#: claude-501 and not claude-code.
_MODEL = re.compile(
    r"claude-(?:opus|sonnet|haiku|fable|instant|[0-9]+[.\-][0-9])[a-z0-9.\-]*"
    r"|gpt-[0-9o]|\bsonnet\b|\bopus\b|\bhaiku\b|"
    r"LLM backend|model backend",
    re.I,
)

#: A reply that CLAIMS this server runs, or would run, a model. Distinct from
#: ``_MODEL``, which catches a model being NAMED: these are claims about where
#: the work happens, and both live instances said a feature was skipped or
#: unavailable BECAUSE of a server-side call that has not existed since P2-G.
#:
#: Precise on purpose. Measured over the 24 surfaces a real pass collects: this
#: form matches ZERO of the three live denials (``instructions``,
#: ``tool:qa_bug_report``, ``tool:qa_explore_step``, all "no model call"), while
#: broadening it to ``model call`` matches all three. An earlier design tried to
#: keep the loose form and exclude negations; measured, the exclusion suppressed
#: nothing, so precision does the work instead of machinery.
_SERVER_MODEL_CLAIM = re.compile(
    r"server[- ]side (?:LLM|model) call|server (?:LLM|model) call",
    re.I,
)

#: Hosts the egress guard lets through. ``adb`` reaches its server on
#: 127.0.0.1:5037 and device listing is a reply surface worth scanning.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", ""})
