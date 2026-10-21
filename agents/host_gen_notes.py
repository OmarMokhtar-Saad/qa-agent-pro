"""Host mode: tester-facing notes of the prepare pipeline (C3c).

Moved from handlers/generate.py: the age label, the reply-note join and the
"what this server did NOT do" disclosure. Pure text; no hooks needed.
"""

from __future__ import annotations

from agents.host_gen_args import NoticeArgs


def ago_label(seconds) -> str:
    """Tester-facing age for a notice: "less than a minute", "12 minute(s)",
    "2 hour(s) 31 minute(s)". Never raises.

    I3 (2026-08-10): the duplicate-SUITE window is a day wide now, so the old
    bare "{n} minute(s)" would routinely have printed "151 minute(s) ago".
    """
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError, OverflowError):
        return "some time"
    mins = total // 60
    if mins < 60:
        return f"{mins} minute(s)" if mins else "less than a minute"
    hours, rem = divmod(mins, 60)
    return f"{hours} hour(s) {rem} minute(s)" if rem else f"{hours} hour(s)"


def prepare_reply_notes(notice: str, *notes: str) -> str:
    """*notice* with every non-empty note APPENDED in order, blank-line apart.

    APPEND, never assign -- see PreparePayloadResult."""
    for note in notes:
        if note:
            notice = (notice + "\n\n" + note) if notice else note
    return notice


# Host-side ambiguity preflight, UNCONDITIONAL: the TICKET-7154 pre-pass
# never runs server-side, because llm.resolve_generation_mode() returns
# the "host" constant, so ground_and_gate gets run_ambiguity_llm=False.
_AMBIGUITY_LINE = (
    "> ℹ️  The requirement pre-pass did **not** run on "
    "this server: the under-specified/no-UI check is handed to your own "
    "chat model instead. That is why this prepare made no "
    "ambiguity-gate LLM call."
)
_AC_LINE = (
    "> ℹ️  This ticket carries no acceptance criteria, and this "
    "server did **not** synthesize any: deriving them is step 0b of the "
    "payload's `jobs_to_run`. Return them as a top-level "
    "`acceptance_criteria` array with your suite; they will be labelled "
    "MODEL-DERIVED, and without them the suite finalizes with no "
    "requirements traceability."
)
_IMAGE_LINE = (
    "> ℹ️  This server made **no** vision call for the "
    "screenshot(s) you attested or supplied in your own conversation; read "
    "them with your OWN multimodal model. This server has no vision backend to "
    "call and needs no key of its own. Read them as step 0c of the "
    "payload's `jobs_to_run`, ground "
    "your cases in them, and return an optional top-level "
    "`image_descriptions` array so this server can record what they "
    "showed."
)
# Residue R4: the checklist fold's own block. It names the fold, the return
# field, BOTH submission routes and the honest cost (host authorship of the
# coverage denominator), plus the two server-side counterweights that make the
# fold defensible rather than an over-claim. It no longer opens by naming
# QA_ATOMIC_CHECKLIST_ENABLED: that setting was DELETED (batch 8b-ii) and the
# checklist hardcoded ON, so announcing it as a live switch would be the
# over-claim this whole disclosure exists to prevent, inverted.
_CHECKLIST_LINE = (
    "> ℹ️  This server made **no** "
    "requirement-decomposition call: breaking the "
    "ticket into an atomic requirements checklist is step 0d of the "
    "payload's `jobs_to_run`. Derive it BEFORE you generate, generate "
    "so that every item is covered, and return it as an optional "
    "top-level `checklist_items` array with your merged suite (on the "
    "per-category route, in the finalize review sidecar). This server "
    "assigns every `CL-NNN` id and labels the result MODEL-DERIVED. "
    "**You will have authored both the requirement set and the test "
    "cases.** This server's pure-Python granularity audit still runs "
    "over your list and is the only independent check left. Omit the "
    "field and the suite finalizes with no requirement checklist at "
    "all: nothing is decomposed to fill the gap."
)
# ...and the ONE accepted capability narrowing that rides with it, claimed only
# when a rule pack actually mandated a line that is now falling back to prompt
# + advisory instead of being scored.
_RULE_PACK_LINE = (
    "> ℹ️  Rule packs (bilingual / atomicity / standing "
    "rules) mandated requirement "
    "line(s) for this ticket, and with the decomposition handed to you "
    "they run in **prompt + advisory** mode: the mandated lines still "
    "reach your generation prompt and the advisory rule-pack report is "
    "still rendered, but they are NOT interleaved into the checklist, "
    "so nothing checks them against the suite. Cover them anyway -- nothing "
    "downstream will tell you if you did not."
)


def host_mode_server_llm_notice(args: NoticeArgs) -> str:
    """Disclose what this server did NOT do on the host path.

    ALWAYS returns at least one line (the ambiguity-preflight disclosure is
    unconditional). The four flags still vary per prepare: a ticket with real
    ACs ships no AC job, no screenshots ships no image job, and
    rule_packs_narrowed is the only channel for a REVIVED rule pack's narrowing.
    Never raises.
    """
    lines = notice_base_blocks(args.ac_boomeranged, args.img_boomeranged)
    lines += notice_extra_blocks(args.checklist_boomeranged, args.rule_packs_narrowed)
    return "\n".join(lines)


def notice_base_blocks(ac_boomeranged: bool, img_boomeranged: bool) -> list:
    """Pre-pass, acceptance-criteria and image disclosure lines."""
    lines: list = []
    lines.append(_AMBIGUITY_LINE)
    if ac_boomeranged:
        lines.append(_AC_LINE)
    if img_boomeranged:
        lines.append(_IMAGE_LINE)
    return lines


def notice_extra_blocks(checklist_boomeranged: bool, rule_packs_narrowed: bool) -> list:
    """Checklist-fold and rule-pack narrowing disclosure lines."""
    lines: list = []
    if checklist_boomeranged:
        lines.append(_CHECKLIST_LINE)
    if rule_packs_narrowed:
        lines.append(_RULE_PACK_LINE)
    return lines
