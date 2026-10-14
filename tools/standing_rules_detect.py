"""Trigger detection for the standing rules: what the ticket content mandates."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from tools.standing_rules_patterns import (
    _API_STRONG_RE,
    _API_WEAK_MIN,
    _API_WEAK_RE,
    _ERROR_FLOW_RE,
    _MAX_EVIDENCE,
    _MAX_OPEN_QUESTIONS,
    _OPEN_QUESTION_RE,
    _UI_RE,
)
from tools.standing_rules_text import _MAX_REF_CHARS
from tools.swagger_fetcher import looks_like_openapi_url

logger = logging.getLogger("tools.standing_rules")


@dataclass
class Triggers:
    """What the ticket content mandates. All fields default to "nothing fired"."""

    api: bool = False
    api_evidence: list[str] = field(default_factory=list)
    api_weak_only: bool = False
    ui: bool = False
    ui_evidence: list[str] = field(default_factory=list)
    error_flow: bool = False
    has_spec: bool = False
    spec_hint: str = ""
    open_questions: list[str] = field(default_factory=list)

    @property
    def fired(self) -> bool:
        return bool(self.api or self.ui)


@dataclass(frozen=True)
class TriggerContext:
    """Side evidence for ``detect_triggers``: a live UI extraction, a spec, screenshots."""

    ui_content: dict | None = None
    openapi_text: str = ""
    images_present: bool = False


_NO_CONTEXT = TriggerContext()


def _evidence(blob: str, patterns: tuple[re.Pattern, ...]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        m = pat.search(blob)
        if m:
            out.append(m.group(0))
        if len(out) >= _MAX_EVIDENCE:
            break
    return out


def _log_weak_only(weak: list[str]) -> None:
    logger.info(
        "standing rules: API trigger fired on WEAK signals only (%s) - the "
        "mandated API cases will be labelled circumstantial",
        ", ".join(weak),
    )


def _trigger_evidence(blob: str, has_spec: bool) -> tuple[bool, list[str], bool]:
    """``(api, api_evidence, weak_only)`` for the API trigger, logging the weak cases."""
    strong = _evidence(blob, _API_STRONG_RE)
    weak = _evidence(blob, _API_WEAK_RE)
    weak_only = not strong and not has_spec and len(weak) >= _API_WEAK_MIN
    api = bool(strong) or has_spec or weak_only
    api_evidence = strong or (weak if weak_only else [])
    if weak_only:
        _log_weak_only(api_evidence)
    elif weak and not api:
        logger.debug(
            "standing rules: %d weak API signal(s) (%s) - below the %d-signal "
            "floor, no API rules forced",
            len(weak),
            ", ".join(weak),
            _API_WEAK_MIN,
        )
    return api, api_evidence, weak_only


def _spec_hint(blob: str) -> str:
    """The first OpenAPI/Swagger-looking URL in the ticket text, or ""."""
    for token in re.findall(r"https?://\S+", blob)[:20]:
        if looks_like_openapi_url(token):
            return token[:_MAX_REF_CHARS]
    return ""


def _open_questions(blob: str) -> list[str]:
    """The unresolved-question lines of the ticket text, capped."""
    return [
        m.group(0).strip()[:200]
        for m in list(_OPEN_QUESTION_RE.finditer(blob))[:_MAX_OPEN_QUESTIONS]
    ]


def _ui_fired(blob: str, context: TriggerContext) -> tuple[bool, list[str]]:
    """``(ui, ui_evidence)``: prose evidence, a live UI extraction or screenshots."""
    ui_evidence = _evidence(blob, _UI_RE)
    ui_content = context.ui_content
    has_ui_content = bool(ui_content and not ui_content.get("error"))
    return bool(ui_evidence) or has_ui_content or bool(
        context.images_present
    ), ui_evidence


def _build_triggers(blob: str, context: TriggerContext) -> Triggers:
    """The Triggers for one ticket blob and its side evidence."""
    has_spec = bool((context.openapi_text or "").strip())
    api, api_evidence, weak_only = _trigger_evidence(blob, has_spec)
    ui, ui_evidence = _ui_fired(blob, context)
    return Triggers(
        api=api,
        api_evidence=api_evidence,
        api_weak_only=weak_only,
        ui=ui,
        ui_evidence=ui_evidence,
        error_flow=bool(_evidence(blob, _ERROR_FLOW_RE)),
        has_spec=has_spec,
        spec_hint=_spec_hint(blob),
        open_questions=_open_questions(blob),
    )


# ``ui_content`` (a live UI extraction) and ``images_present`` (screenshots or
# mockups on the ticket) are themselves proof of a user-facing screen, so the
# UI trigger fires even when the prose never says "screen". (Both now travel
# on the ``context`` argument.)
#
# The API trigger fires on ONE strong signal, or on ``_API_WEAK_MIN`` distinct
# weak signals (then ``api_weak_only=True``), or when a real OpenAPI spec was
# supplied. Never raises -- any failure returns an all-false Triggers, which
# degrades to today's behaviour.
def detect_triggers(
    feature_text: str, jira_text: str = "", context: TriggerContext = _NO_CONTEXT
) -> Triggers:
    """Scan the ticket for the API / user-facing-UI standing-rule triggers."""
    try:
        blob = "\n".join(t for t in (feature_text or "", jira_text or "") if t)
        return _build_triggers(blob, context)
    except Exception:
        logger.exception("detect_triggers failed - no standing rule fires")
        return Triggers()
