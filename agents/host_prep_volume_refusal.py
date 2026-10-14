"""Fact lines, refusal text and warning text of the volume floor."""

from __future__ import annotations

from agents.host_prep_volume import _VOLUME_MAX_NAMED, VolumeCheck, VolumeVerdict
from agents.host_prep_volume_options import volume_options


def volume_shown(short: list, floor: int) -> str:
    """The below-floor categories as ``name got/floor``, capped at a few."""
    shown = ", ".join(f"`{n}` {got}/{floor}" for n, got in short[:_VOLUME_MAX_NAMED])
    if len(short) > _VOLUME_MAX_NAMED:
        shown += f", (+{len(short) - _VOLUME_MAX_NAMED} more)"
    return shown


def _last_count(meta: dict) -> int:
    try:
        return int(meta.get("volume_last_count") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


# Refusal channel ONLY. post_dedup measures the FINAL suite after the
# server's own de-duplication, so a smaller number there is the server's
# doing, not the host's, and the suite is already finalized -- the top-up
# advice would be wrong on both counts. On the `acked` beat the tester has
# just signed the smaller suite off, so nagging about it is equally wrong.
def volume_shrink_fact(verdict: VolumeVerdict, meta: dict, post_dedup: bool) -> str:
    """The "smaller than the refused one" line, or "" when it does not apply."""
    last = _last_count(meta)
    if last <= verdict.total or post_dedup or verdict.mode != "refuse":
        return ""
    return (
        f"- **⚠️ This submission is SMALLER than the one "
        f"refused a moment ago ({last} → {verdict.total} cases).** Cases "
        "are being removed, not added, and generating fewer is not a "
        "way through a volume refusal. Take one of the routes below."
    )


def _unknown_fact(v: VolumeVerdict) -> str:
    """The "no recognisable category" bullet."""
    return (
        f"- **{v.unknown} case(s) carried no recognisable `category`**"
        + (
            " -- too few to account for an empty category"
            if v.empty and v.unknown < v.floor
            else ""
        )
        + ". They count toward the total but toward no category; set "
        "each case's `category` to one of the payload's own category "
        "names."
    )


def _asked_fact(v: VolumeVerdict) -> str:
    """The "This prep asked for" bullet."""
    return (
        f"- **This prep asked for:** {v.floor_total} case(s) overall "
        f"({v.floor} per category × {len(v.names)} categories). A category "
        "may come in under that number when it genuinely has less to test, "
        "PROVIDED the suite makes it up elsewhere -- what is reported here "
        "is the suite falling short overall, a category with nothing in it, "
        "or one below half its share."
    )


def volume_facts(verdict: VolumeVerdict, meta: dict, post_dedup: bool) -> list:
    """The bullet facts shared by the refusal and the warning."""
    v = verdict
    facts = [
        f"- **{'In the FINAL suite' if post_dedup else 'Submitted'}:** "
        f"{v.total} case(s), {v.total - v.unknown} of them carrying a recognised "
        "category.",
        _asked_fact(v),
        f"- **Below that floor:** {volume_shown(v.short, v.floor) or '(none)'}.",
    ]
    shrink = volume_shrink_fact(v, meta, post_dedup)
    if shrink:
        facts.append(shrink)
    if v.unknown:
        facts.append(_unknown_fact(v))
    return facts


def _ignored_ack(verdict: VolumeVerdict, check: VolumeCheck) -> str:
    """The note that an ack on the FIRST submit was ignored, or ""."""
    if not (check.ack and not verdict.refused_before):
        return ""
    return (
        "\n\n> ⚠️  `volume_floor_ack=true` arrived on the "
        "FIRST submit for this prep and was IGNORED: the tester "
        "cannot have seen these numbers yet. Show them the figures "
        "above; if they confirm the smaller suite is right, "
        "resubmit it unchanged with the ack and it WILL be "
        "honoured."
    )


_NO_HAND_FILE = (
    "Do **not** write the suite to a file yourself. A hand-authored "
    "CSV or XLSX skips de-duplication, the coverage critic, "
    "requirements traceability, risk scoring, the auto-export and "
    "the audit log, and it has no `suite_id` -- it is not a "
    "substitute for finalizing here, whatever the tester agreed to."
)


def volume_refusal_text(verdict: VolumeVerdict, facts: list, check: VolumeCheck) -> str:
    """The markdown of a ``refuse`` verdict."""
    return (
        "⛔ **Submission refused:** this suite is materially below "
        "the generation volume this prep's own payload asked for.\n\n"
        + "\n".join(facts)
        + _ignored_ack(verdict, check)
        + f"\n\nNothing was discarded and prep `{check.prep_id}` is intact -- "
        "the prepared context and every staged category are still "
        "there.\n\n"
        "**Pick one:**\n" + volume_options(verdict, check) + "\n" + _NO_HAND_FILE
    )


def _worst_clause(v: VolumeVerdict) -> str:
    """The "N of M categories are below ..." clause, or the total-only one."""
    if not v.short:
        return f"the suite totals {v.total} case(s) against {v.floor_total}"
    worst = min(v.short, key=lambda pair: pair[1])
    return (
        f"{len(v.short)} of {len(v.names)} categories are below the number "
        "of cases this prep asked for"
        f", the worst being `{worst[0]}` with {worst[1]} of {v.floor}"
    )


def _head_qualifiers(v: VolumeVerdict, post_dedup: bool) -> str:
    """The parenthetical and override qualifiers after UNDER-GENERATED."""
    final = " (in the FINAL suite, after de-duplication and any re-filing)"
    acked = " -- refusal OVERRIDDEN by `volume_floor_ack=true`"
    return (final if post_dedup else "") + (acked if v.mode == "acked" else "")


def volume_warning_head(verdict: VolumeVerdict, post_dedup: bool) -> str:
    """The HEAD line of a warning, naming and quantifying the shortfall.

    F5: the old head said only "Volume below the requested floor" and the tail
    said the suite "was accepted as submitted", so the one number that matters
    (`Boundary Values` 2/8) sat in the middle of a blockquote and a host
    summarising the reply kept the .xlsx path and dropped this.
    """
    return (
        "> ⚠️  **UNDER-GENERATED"
        + _head_qualifiers(verdict, post_dedup)
        + ":** "
        + _worst_clause(verdict)
        + ".\n"
    )


def volume_warning_text(verdict: VolumeVerdict, facts: list, check: VolumeCheck) -> str:
    """The markdown of a ``warn`` or ``acked`` verdict."""
    return (
        volume_warning_head(verdict, check.post_dedup)
        + "".join(f">   {line}\n" for line in facts)
        + ">   **This suite was still accepted and exported** -- nothing "
        "here blocked it, and the shortfall is recorded on the "
        '"Generation Notes" sheet of the workbook. Show the tester these '
        "numbers. If the short categories were not a deliberate choice, "
        "regenerate them and resubmit with prep_id "
        f"`{check.prep_id}`.\n\n"
    )
