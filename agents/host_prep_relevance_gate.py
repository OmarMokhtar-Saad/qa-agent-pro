"""Refusal and gate for QA_HOST_IMAGE_REQUIRE_RELEVANT (the submit-side image check)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from agents.host_prep_relevance_verdict import relevance_facts, relevance_verdicts
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


class RelevanceCheck(NamedTuple):
    """The inputs of one image_relevance_gate call."""

    meta: object
    result: object
    prep_id: str
    ack: bool = False
    staged_hint: str = ""


def _acked_text(facts: str) -> str:
    """The ``acked`` body of refusal_text."""
    return (
        "> \u26a0\ufe0f  Image relevance below the bar this prep asked "
        "for (refusal OVERRIDDEN by `image_relevance_ack=true`):\n"
        + "".join(f">   {line}\n" for line in facts.splitlines() if line.strip())
        + ">   The suite below was accepted as submitted. If that is not "
        "deliberate, capture or attach the correct screen and prepare "
        "again with `proceed_anyway=true` (a prep for this source is "
        "already open).\n\n"
    )


def _resubmit_option(prep_id: str, staged_hint: str) -> str:
    """Option 2 of the refusal: how to resend the missing verdicts."""
    # Staged route: the cases are already on the server, so the retry carries
    # the missing field ALONE. The wording is REUSED from
    # _staged_resubmit_hint rather than paraphrased, so this refusal and the
    # ambiguity one read the same way to a host. The array's SHAPE is handed
    # to that helper (`field_shape`) rather than appended here: appended, it
    # landed after the hint's closing "...cases the tester already reviewed"
    # and so after "and nothing else", dangling off the wrong noun.
    if staged_hint:
        return staged_hint + ".\n"
    return (
        f"resubmit the SAME suite with the SAME prep_id `{prep_id}` "
        "and a top-level `image_descriptions` array carrying one "
        "`relevant` verdict per image (the bare string `yes`, `no` or "
        "`unsure`) plus a one-line `relevance_reason`. On the "
        "per-category route send it in the finalize sidecar.\n"
    )


def _ignored_ack_text() -> str:
    """The note that an ack on the FIRST submit was ignored."""
    return (
        "\n\n> \u26a0\ufe0f  `image_relevance_ack=true` arrived on the "
        "FIRST submit for this prep and was IGNORED: the tester cannot "
        "have seen these screens yet. Show them the finding above; if "
        "they confirm the suite is right anyway, resubmit it unchanged "
        "with the ack and it WILL be honoured."
    )


def _refusal_head(facts: str, ignored_ack: str) -> str:
    """The refusal's opening: why it was refused, then the findings."""
    return (
        "\u26d4 **Submission refused:** `QA_HOST_IMAGE_REQUIRE_RELEVANT` is "
        "on for this prep and this submission carries no clean per-image "
        "verdict for the screens it was generated from.\n\n" + facts + ignored_ack
    )


def _refusal_options(prep_id: str, staged_hint: str) -> str:
    """The refusal's closing: intact-prep note and the three options."""
    return (
        f"\n\nNothing was discarded and prep `{prep_id}` is intact -- the "
        "prepared context and every staged category are still there.\n\n"
        "**Pick one:**\n"
        "1. If the screen really is the wrong one, capture or attach the "
        "correct screen and run `qa_prepare_test_cases` again WITH "
        "`proceed_anyway=true` -- a prep for this source is already open, "
        "so the duplicate-prep guard (default ON) refuses that call "
        "without it. Cases grounded on the wrong screen are the defect "
        "this refusal exists to catch.\n"
        "2. If you did read the screens and simply did not report it, "
        + _resubmit_option(prep_id, staged_hint)
        + "3. Or, ONLY if the TESTER has seen the finding above and confirms "
        "the suite is right anyway, resubmit it unchanged with "
        "`image_relevance_ack=true`. Ask them first -- do not decide that on "
        "your own judgement."
    )


def refusal_text(check: RelevanceCheck, facts: str, ignore_ack: bool) -> str:
    """The markdown body of a ``refuse``; *ignore_ack* means an ack arrived on
    the FIRST submit."""
    ignored_ack = _ignored_ack_text() if ignore_ack else ""
    return _refusal_head(facts, ignored_ack) + _refusal_options(
        check.prep_id, check.staged_hint
    )


def _decide(check: RelevanceCheck) -> "tuple[str, str]":
    """The gate's verdict; may raise, image_relevance_gate fails open on it."""
    verdicts = relevance_verdicts(check.meta, check.result)
    if verdicts is None:
        return "", ""
    forwarded, counts, off, refused_before = verdicts
    facts = relevance_facts(forwarded, counts, off)
    if check.ack and refused_before:
        return "acked", _acked_text(facts)
    return "refuse", refusal_text(check, facts, check.ack and not refused_before)


# WHY image_relevance_gate EXISTS: Batch 2 made the off-topic verdict visible;
# it still could not stop a suite grounded on the wrong screen from being
# finalized, exported and persisted. Under QA_HOST_IMAGE_REQUIRE_RELEVANT an
# operator may turn that disclosure into a refusal, exactly as
# QA_HOST_AMBIGUITY_REQUIRE_RESULT does for the boomeranged TICKET-7154
# preflight.
#
# IT REFUSES ON TWO THINGS AND ONLY TWO:
#   * any ``relevant: "no"`` -- the host itself says the screen is not about
#     this ticket;
#   * NO usable verdict on ANY image -- the job was shipped, screens really
#     were forwarded, and nothing came back, so there is no record that the
#     screens were even looked at. "Silence reads as cleared" is the precise
#     failure the ambiguity FIX 2 audit change exists to end.
#
# ``unsure`` PASSES, with Batch 2's warning. Refusing on uncertainty punishes
# the honest answer and trains a host to reply ``yes``, which would destroy the
# signal this whole feature is built on; ``no`` and "nothing came back" are
# unambiguous, and the latter is what the reported run produced.
#
# EVERY input is a META STAMP (``host_image_require_relevant``,
# ``host_image_relevance``, ``captured_image_count`` /
# ``attached_image_count``, ``image_relevance_refused``), never a live settings
# flag, so neither a mid-flow .env flip nor a launcher auto-update between
# prepare and submit can change an in-flight prep, and an envelope written
# before those stamps existed returns ``("", "")`` on the first guard. The
# verdicts themselves arrive already validated by
# host_mode.extract_host_image_descriptions -- the strict three-word identity
# map and the isinstance-str gate -- so nothing here re-reads an untrusted
# token.
#
# FORWARDED-SCREENS GUARD: enforcement needs both counts to be zero to opt out,
# i.e. it applies only when this server captured screens or the host attested
# chat attachments. Ticket images fetched from Jira are excluded on purpose:
# they came from the ticket itself, so "off-topic relative to that ticket" is a
# far weaker claim, and refusing a suite over one is the false positive this
# gate can least afford.
def image_relevance_gate(check: RelevanceCheck) -> "tuple[str, str]":
    """Verdict on the IMAGE RELEVANCE of a finalizing submission.

    Returns ``("", "")`` when this prep carries no enforcement contract or the
    submission honours it, ``("acked", note)`` when a refusal was overridden by
    ``image_relevance_ack`` on a prep this gate had ALREADY refused, and
    ``("refuse", markdown)`` otherwise.

    Never raises; on an unexpected error it fails OPEN (``("", "")``) so the
    guard itself can never block a legitimate finalize -- the same discipline as
    prep_volume_floor_note and fanout_incomplete_note. The rationale is in the
    comment block above this function.
    """
    # pragma: no cover - defensive, must never block a finalize
    with never_raise(logger, "image relevance gate failed", exc_info=True):
        return _decide(check)
    return "", ""
