"""Host mode: the acceptance-criteria result type, shape caps and text cleaners (split from host_ac.py)."""

from __future__ import annotations

import dataclasses
import re

from config.settings_guard import never_raise

# Shape caps on the UNTRUSTED `acceptance_criteria` field. This number is the one
# the AC job's own instructions quote to the host, so the two must move TOGETHER
# -- _AC_JOB_INSTRUCTIONS names it precisely because anything past it is
# truncated here (with a note). Do not restate the value in prose: this comment
# said "at most 20" for twelve lines after the constant became 60.
#
# 2026-08-31: the justification this comment used to carry -- that _AC_GEN_SYSTEM
# asked a server-side synthesizer for 3-8 criteria, so 20 was already generous --
# named a symbol DELETED in P2-G. Nothing server-side derives criteria any more,
# and the 3-8 bound it justified was itself the defect: a source enumerating 12
# use cases legitimately needs 12 criteria, and capping the ask at 8 made at
# least four of them untraceable.
#
# Raised 20 -> 60 on the same day, after an independent review pointed out that
# 20 was the SAME defect one bound higher: the prompt said both "NEVER merge two
# entries" and "at most 20", which for a 21-requirement source are unsatisfiable
# together, with no tiebreak stated. A 25-requirement epic is ordinary. 60 is a
# malformed-input bound, not a quality judgement -- and the prompt now tells the
# host what to do when the material exceeds it (say so in the last criterion)
# instead of leaving it to merge silently. Prompt and enforcement must move
# together: _AC_JOB_INSTRUCTIONS quotes this number.
_AC_MAX_ITEMS = 60
_AC_MAX_DESC_CHARS = 300
_AC_MIN_DESC_CHARS = 5
_AC_MAX_NOTES = 10
_AC_ID_RE = re.compile(r"^AC-\d{3}$")
_AC_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


@dataclasses.dataclass
class HostACResult:
    """Validated result of the host's `acceptance_criteria` field.

    ``ran`` is False when the field was absent or UNUSABLE. In that case ``acs``
    is EMPTY and the server does NOT fall back to synthesizing its own -- the
    whole point of the flag is that it makes no such call. The suite finalizes
    with no requirements traceability and the reply says so; fabricating
    criteria to make an RTM look populated would be worse than an empty one.
    """

    ran: bool = False
    requested: bool = False
    acs: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)
    dropped: int = 0
    reassigned: int = 0


def _ac_clean(text: object) -> str:
    """Sanitize one host-authored criterion for display + downstream reuse.

    URLs are stripped for the reason tools/comment_reconciler stripped them
    before batch D5 deleted it on 2026-08-15: this text is derived from
    _GUARD-wrapped ticket/comment material that host mode deliberately places
    in the host's context, and it comes back as a requirement -- it must never
    be able to plant a navigation target. That makes this one of the two URL
    strippers that SURVIVE the reconciler (the other is the parent-context
    stripper in tools/jira_mcp), and the reasoning is recorded in
    docs/RETIRED_CAPABILITIES.md section 4. Newlines collapse so one criterion
    cannot forge extra list rows in the report.
    """
    with never_raise():
        s = _AC_URL_RE.sub("[link removed]", str(text or ""))
        s = re.sub(r"\s+", " ", s).strip()
        return s[:_AC_MAX_DESC_CHARS]
    return ""


def _ac_note(res: HostACResult, msg: str) -> None:
    """Append a note, capped at _AC_MAX_NOTES."""
    if len(res.notes) < _AC_MAX_NOTES:
        res.notes.append(msg)
