"""Jira image gate: BEAT 2, the second ask once the fetched ticket reveals unsupplied images."""

from __future__ import annotations

import logging
import sys
from typing import NamedTuple

from config.settings_guard import never_raise
from handlers.image_gate_beat2_text import (
    _full_head,
    _second_beat_full,
    _second_beat_partial,
)
from handlers.image_gate_names import _clamped_count, _jira_image_cap
from handlers.seam_alias import alias

logger = logging.getLogger("tools.mcp_handlers")


class _BeatTwoReq(NamedTuple):
    """What the BEAT 2 gate decides on (n_images: the ticket's own count claim)."""

    n_images: int
    names: list
    kind: str
    plan: str
    have_images: int


def _text_only_image_skip_stamp(plan: str, img_n: int, img_names: list) -> tuple:
    """(count, names[:8]) to stamp into envelope["meta"] when a text-only
    source_plan='jira' pick skipped beat 2 while the ticket referenced
    screens (v1.97.0 cursor-hardening item 2) -- (0, []) otherwise, so the
    skip is disclosed rather than silently dropped. Never raises."""
    with never_raise(logger, "_text_only_image_skip_stamp failed", exc_info=True):
        n = _clamped_count(img_n, lo=0, hi=99) if plan == "jira" else 0
        names = [str(x) for x in (img_names or [])][:8] if n > 0 else []
        return n, names
    return 0, []


def _second_beat_is_silent(kind: str, plan: str, have_images: int) -> bool:
    """True when beat 2 must not ask, whatever the image counts say."""
    if kind not in ("attachments", "embedded"):
        return True
    # v1.97.0 cursor-hardening (item 2): source_plan='jira' (text-only) IS
    # the tester's own answer to "where do the screens come from" -- beat 1
    # already asked and got that answer, so beat 2 must not ask again. The
    # skipped-attachment count is still disclosed elsewhere (never silent),
    # just not via a second dialog.
    if plan == "jira":
        return True
    # 2026-08-31 (C12): menu option 5 reads "Device screens only -- IGNORE
    # the ticket text". Demanding the ticket's attachments from a tester who
    # picked it and has already captured contradicts the choice they were
    # offered. Only once a capture actually arrived: `device` with nothing
    # supplied is still a plan that promised images and delivered none.
    return plan == "device" and have_images > 0


# COMPLETENESS, not presence (2026-08-09): ONE image out of three used
# to silence this gate, so generation started on a SUBSET of the
# screens. `count` is the ticket's OWN claim and is clamped to
# settings.jira_max_images -- that cap bounds how many screens this
# server will ever carry, so an unclamped N could demand images the
# tester can never supply. `image_gate_ack=true` is still an ABSOLUTE
# override at any ratio: the CALLER checks it before reaching here.
def _beat2_want_have(count: int, have_images: int) -> tuple[int, int]:
    """Return the clamped (wanted, held) image counts beat 2 compares."""
    _cap = _jira_image_cap()
    _want = min(max(_clamped_count(count), 1), _cap)
    _have = _clamped_count(have_images, hi=999)
    return _want, _have


def _beat2_named(names: list) -> str:
    """Return the clause naming the ticket's own screens, or an empty string."""
    return (
        " The ticket names them: " + ", ".join(f"`{n}`" for n in names) + "."
        if names
        else ""
    )


# BEAT 2 text, or "" to stay SILENT.
#
# Silent whenever the ticket revealed no images, or images actually arrived,
# or the evidence is merely INCONCLUSIVE (kind == "unknown": the Jira payload
# came back with no `attachment` field at all). That last case used to gate,
# which spent a whole extra round trip to say "I could not tell" on a ticket
# that may well have no images -- it is purely informational, so it is left to
# the pre-existing post-hoc notice on the finished payload instead. Silent
# also whenever images actually arrived, or the ticket's own plan is the
# text-only `source_plan='jira'` pick -- a plan that already covered the
# screens is never asked twice, and beat 1's `source_plan='jira'` IS the
# tester's own answer to where the screens come from, so beat 2 must not
# ask a second time (v1.97.0 cursor-hardening item 2 -- this used to fire
# here too, forcing a double ask). Never
# raises.
def image_gate_second_beat(req: _BeatTwoReq) -> str:
    """Return the BEAT 2 text, or an empty string to stay silent."""
    count, names, kind, plan, have_images = req
    with never_raise(logger, "image_gate_second_beat failed", exc_info=True):
        if _second_beat_is_silent(kind, plan, have_images):
            return ""
        _want, _have = _beat2_want_have(count, have_images)
        if _have >= _want:
            return ""
        named = _beat2_named(names)
        if _have > 0:
            return _second_beat_partial((_want, _have), names, named)
        return _second_beat_full(_full_head(count, kind), plan, named)
    return ""


_image_gate_second_beat = alias(sys.modules[__name__], "image_gate_second_beat")
