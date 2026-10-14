"""Diagnostics device facts: the app label, the language cell and the device sub-line."""

from __future__ import annotations

from tools.mobile.report_text import _text, esc


def _app_label(manifest: dict) -> str:
    package = _text(manifest.get("package"), 80)
    return package or "(no app)"


def _locale_record(manifest: dict) -> dict:
    body = manifest.get("locale")
    return body if isinstance(body, dict) else {}


def _locale_unmatched(actual: str, requested: str) -> tuple:
    return (
        "language",
        esc(actual or "(not confirmed)", 24),
        esc(requested, 24) + " was asked for and the device did not take it",
        "gap",
    )


def _locale_asked(requested: str, record: dict) -> str:
    return (
        "asked for "
        + esc(requested, 24)
        + (
            " and set at boot"
            if str(record.get("source") or "") == "boot"
            else " and set on the running device"
        )
    )


# The "what language did this run in?" fact, or None when unrecorded.
#
# None rather than "(unknown)" for every run written before this field
# existed, and for a device that would not answer: a cell claiming a language
# nobody recorded is worse than an absent one, which is the rule
# ``session.device_line`` already follows for the hardware kind.
#
# The VALUE is what the device said it was RENDERING in; the sub-line names
# what was asked for and how it got there. Those are different facts, and a
# run whose request did not take has to say so in the header a tester reads
# first -- with the ``gap`` tone, because it is one.
def _locale_cell(manifest: dict) -> tuple | None:
    record = _locale_record(manifest)
    actual = str(record.get("actual") or "")
    requested = str(record.get("requested") or "")
    if not actual and not requested:
        return None
    if not requested:
        return ("language", esc(actual, 24), "the device was already in it", "")
    if not record.get("matched"):
        return _locale_unmatched(actual, requested)
    return ("language", esc(actual, 24), _locale_asked(requested, record), "")


#: The two kinds ``session.device_record`` writes, and the third state it leaves
#: EMPTY on purpose.
#:
#: That producer refuses to default an unknown kind to ``emulator`` because a
#: confident wrong answer about whether a run touched real hardware is worse than
#: a blank -- and this page then printed " · emulator" unconditionally, throwing
#: that care away and labelling every physical-device run an emulator. The
#: unknown case is now named rather than guessed.
#:
#: TWO SURFACES, TWO STRINGS, ONE STATE -- enumerated rather than asserted. This
#: page says "device kind unrecorded"; ``session.device_line`` says
#: "(kind not recorded for this run)" for the same empty kind. Nothing branches
#: on either, so this is an un-unified WORDING and not a half-wired sentinel --
#: but "one producer, one meaning" would be a false claim while a second wording
#: exists, so the pointer stands here instead of the claim. Unifying them is a
#: change to a different surface with its own tester-facing text.
DEVICE_KINDS = {"emulator": "emulator", "physical": "physical device"}


DEVICE_KIND_UNKNOWN = "device kind unrecorded"


def _device_sub(manifest: dict) -> str:
    """The device cell's sub-line: the avd, and what the manifest SAYS it ran on."""
    device = manifest.get("device")
    kind = str((device if isinstance(device, dict) else {}).get("kind") or "")
    return (
        esc(manifest.get("avd") or "(unknown avd)", 40)
        + " · "
        + DEVICE_KINDS.get(kind, DEVICE_KIND_UNKNOWN)
    )
