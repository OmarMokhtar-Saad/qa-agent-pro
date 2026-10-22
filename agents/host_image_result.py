"""Host mode: the image-description result type, shape caps and cleaners (split from host_image.py)."""

from __future__ import annotations

import dataclasses
import re

from agents.host_ac_result import _AC_URL_RE
from config.settings_guard import never_raise

# Shape caps on the UNTRUSTED `image_descriptions` field. Corpus-independent:
# `_budget_keep_flags` caps how many images can be forwarded in the first
# place, so a list far longer than that is a malformed field, not a richer
# ticket. The per-description cap is generous (a UI screenshot legitimately
# enumerates many controls) but finite.
_IMG_MAX_ITEMS = 20
_IMG_MAX_DESC_CHARS = 1200
_IMG_MIN_DESC_CHARS = 5
_IMG_MAX_ID_CHARS = 80
# Relevance verdict caps (QA_IMAGE_RELEVANCE_ENABLED). The verdict is a closed
# three-word ENUM and this map is an IDENTITY map on purpose (review finding C1):
# an earlier draft also accepted true/false/y/n/relevant/irrelevant, which meant a
# JSON boolean `true` -- str()'d to "True" by _img_clean -- resolved to `yes`,
# suppressing the very off-topic warning this feature exists to raise, while the
# host instruction and docs both promised only three words were accepted. Code,
# response_schema enum, host clause and docs now say the same thing. Anything
# else records NO VERDICT rather than an answer. The reason is ONE short line by
# contract, so its cap is far tighter than a description's.
_IMG_RELEVANCE_VALUES: dict = {"yes": "yes", "no": "no", "unsure": "unsure"}
_IMG_MAX_REASON_CHARS = 240
_IMG_MAX_NOTES = 10
_IMG_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclasses.dataclass
class HostImageResult:
    """Validated result of the host's `image_descriptions` field.

    ``ran`` is False when the field was absent or UNUSABLE. Nothing falls back:
    the server made no vision call for this prep by design, so there is no
    server-side description to substitute and none is invented.
    """

    ran: bool = False
    requested: bool = False
    images: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)
    dropped: int = 0
    # QA_IMAGE_RELEVANCE_ENABLED: whether THIS prep asked the host for a
    # per-image relevance verdict. Read from the prep's meta STAMP, never a live
    # flag, so an OLD envelope parses no verdict and warns about nothing.
    relevance_requested: bool = False
    # Entries whose verdict came back `no` or `unsure` -- the tell that the screen
    # may not belong to this ticket at all. Rendered FIRST and loudest. Holds
    # COPIES of the image dicts, so a later mutation of one list cannot silently
    # mutate the other.
    off_topic: list = dataclasses.field(default_factory=list)


def _img_clean(text: object, limit: int = _IMG_MAX_DESC_CHARS) -> str:
    """Sanitize one host-authored image description for display.

    URLs are stripped for the same reason _ac_clean strips them: this text is
    derived from material host mode deliberately places in the host's context --
    here including pixels an attacker may control -- and it comes back into a
    tester-facing report, so it must never be able to plant a navigation target.
    Control characters are removed and newlines collapse so one description
    cannot forge extra report rows.
    """
    with never_raise():
        s = _AC_URL_RE.sub("[link removed]", str(text or ""))
        s = _IMG_CTRL_RE.sub("", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s[:limit]
    return ""


def _img_note(res: HostImageResult, msg: str) -> None:
    """Record ``msg`` on ``res`` unless the note cap is already reached."""
    if len(res.notes) < _IMG_MAX_NOTES:
        res.notes.append(msg)


def _img_relevance_gap_note(images: list) -> str:
    """The note for images that came back with no usable `relevant` verdict, or
    "" when every image has one. Pure."""
    missing = [i for i in images if not i.get("relevant")]
    if len(missing) == len(images):
        return (
            "No usable `relevant` verdict came back for any image, so "
            "there is no record of whether the screen(s) actually match "
            "this ticket -- and this server made no vision call to check."
        )
    if missing:
        return (
            f"{len(missing)} of {len(images)} image(s) came back "
            "with no usable `relevant` verdict "
            f"({', '.join(str(i.get('image_id', '?')) for i in missing)})"
            " -- for those there is no record of whether the screen "
            "matches this ticket."
        )
    return ""
