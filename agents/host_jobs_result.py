"""Host mode: the validated ``ambiguity_result`` field (split from host_jobs.py)."""

from __future__ import annotations

import dataclasses
import logging
import re

from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

_AMBIGUITY_SEVERITIES = ("none", "low", "medium", "high")
_AMBIGUITY_SURFACES = ("ui", "api", "backend", "docs", "none", "unclear")
_AMB_MAX_QUESTIONS = 5
_AMB_MAX_Q_CHARS = 300


@dataclasses.dataclass
class HostAmbiguityResult:
    """Validated `ambiguity_result`. ``ran`` is False when the field was absent
    or unreadable -- which is deliberately NOT the same as severity "none"; the
    whole TICKET-7154 rule is that "could not classify" must never flatten to
    "clear"."""

    ran: bool = False
    requested: bool = False
    severity: str = ""
    testable_surface: str = ""
    questions: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)

    @property
    def cleared(self) -> bool:
        """True only for a READABLE verdict that is not `high`. An absent field
        is never `cleared`, so the fail-safe direction is the default."""
        return bool(self.ran and self.severity in ("none", "low", "medium"))


def _clean_ambiguity_questions(raw_questions) -> list:
    """Whitespace-collapsed, de-duplicated, capped list of string questions."""
    questions: list = []
    for q in raw_questions or []:
        if not isinstance(q, str):
            continue
        q = re.sub(r"\s+", " ", q).strip()[:_AMB_MAX_Q_CHARS]
        if q and q not in questions:
            questions.append(q)
        if len(questions) >= _AMB_MAX_QUESTIONS:
            break
    return questions


def _read_ambiguity_surface(raw: dict, res: HostAmbiguityResult) -> str:
    """The recognised testable_surface, or "" plus a note on `res`."""
    surface = str(raw.get("testable_surface") or "").strip().lower()
    if surface and surface not in _AMBIGUITY_SURFACES:
        res.notes.append(
            "`ambiguity_result.testable_surface` was not a recognised value "
            "and was ignored."
        )
        return ""
    return surface


_NOT_OBJECT_NOTE = (
    "`ambiguity_result` was not an object -- the safety preflight "
    "could not be verified from this submission."
)


def _read_ambiguity_severity(raw: dict, res: HostAmbiguityResult) -> str:
    """The recognised severity, or "" plus a note on `res`."""
    sev = str(raw.get("severity") or "").strip().lower()
    if sev not in _AMBIGUITY_SEVERITIES:
        res.notes.append(
            f"`ambiguity_result.severity` was {sev[:32]!r}, which is not one "
            f"of {', '.join(_AMBIGUITY_SEVERITIES)} -- it was NOT read as "
            '"none". The preflight is reported as unverified.'
        )
        return ""
    return sev


def _log_ambiguity_ran(sev: str, surface: str) -> None:
    """Log that the host reported a readable preflight verdict."""
    logger.info(
        "host ambiguity preflight reported severity=%s surface=%s "
        "-- self-reported, not a server classification",
        sev,
        surface or "unspecified",
    )


def _read_ambiguity_body(raw, res: HostAmbiguityResult) -> HostAmbiguityResult:
    """Fill `res` from a readable `raw`, or leave it unverified with a note."""
    if not isinstance(raw, dict):
        res.notes.append(_NOT_OBJECT_NOTE)
        return res
    sev = _read_ambiguity_severity(raw, res)
    if not sev:
        return res
    surface = _read_ambiguity_surface(raw, res)
    res.severity = sev
    res.testable_surface = surface
    res.questions = _clean_ambiguity_questions(raw.get("questions"))
    res.ran = True
    _log_ambiguity_ran(sev, surface)
    return res


def _unreadable_ambiguity(requested) -> HostAmbiguityResult:
    """The result for an `ambiguity_result` that raised while being read."""
    return HostAmbiguityResult(
        requested=bool(requested),
        notes=[
            "`ambiguity_result` could not be read -- the safety preflight is "
            "reported as unverified."
        ],
    )


# Validate the SHAPE of the UNTRUSTED top-level `ambiguity_result` field.
#
# Never raises. An unreadable value degrades to ran=False plus a note -- never
# to "clear". Only the enum members are accepted; free-form severity strings are
# rejected rather than coerced, because coercing an unrecognised value toward
# "none" is exactly the flattening this must not do.
def extract_ambiguity_result(raw, *, requested: bool = True) -> HostAmbiguityResult:
    """Validate the SHAPE of the UNTRUSTED top-level `ambiguity_result` field."""
    res = HostAmbiguityResult(requested=bool(requested))
    with never_raise(
        logger,
        "could not read ambiguity_result -- reporting it as unverified",
        level=logging.WARNING,
        exc_info=True,
    ):
        if raw is None:
            return res
        return _read_ambiguity_body(raw, res)
    return _unreadable_ambiguity(requested)
