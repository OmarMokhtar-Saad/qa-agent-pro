"""The capture tray: stash captured screens, read them back without consuming."""

from __future__ import annotations

import logging
import uuid

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from handlers.capture_evict import _normalised_ids
from handlers.capture_tray_sweep import _sweep_capture_tray
from tools import clock

logger = logging.getLogger("tools.mcp_handlers")


def _usable_shot(shot) -> bool:
    """ONE predicate for 'this captured screen has bytes the tray can hold',
    shared by _stash_captures and by the reply that counts what it offered.
    Two predicates here drifted (truthy `data` vs bytes-only) and a screen
    dropped by the type check was reported as dropped by the byte budget."""
    return (
        isinstance(shot, dict)
        and isinstance(shot.get("data"), (bytes, bytearray))
        and bool(shot.get("data"))
    )


def _insert_shots(screens: list, labels: list | None, ids: list) -> None:
    """Put each usable shot in the tray; its new id is appended to *ids* as it goes."""
    names = list(labels or [])
    for i, shot in enumerate(screens or []):
        if not _usable_shot(shot):
            continue
        data = shot.get("data")
        label = str(names[i] or "").strip()[:80] if i < len(names) else ""
        cid = f"cap_{uuid.uuid4().hex[:12]}"
        _limits._CAPTURE_TRAY[cid] = {
            "filename": shot.get("filename") or f"screen_{i + 1}.png",
            "mime": shot.get("mime") or "image/png",
            "data": bytes(data),
            "label": label or f"screen_{i + 1}",
            "created_at": clock.now(),
        }
        ids.append(cid)


# ONLY WHAT RESOLVES. The caller puts this list straight into a
# tester-facing reply -- the count, the id rows and the names all come
# from it -- so an id the sweep just evicted is a screen the tester is
# told they captured and cannot use. Measured 2026-09-06 with the real
# device sizes the tray's own row cites (1-3 MiB): 12 screens at 3 MiB
# reports 12 and resolves 10; 20 at 2 MiB reports 20 and resolves 16.
# Eviction is oldest-first, so the ids that die are the FIRST screens
# of the flow.
#
# The caller compares this against what it handed in and names the
# shortfall, so the drop is disclosed rather than silent -- the same
# shape as revive_captures above.
def _only_resolving(ids: list) -> list:
    """What the caller may tell the tester they hold: ids still in the tray."""
    return [cid for cid in ids if cid in _limits._CAPTURE_TRAY]


# Stash captured screens in the tray; returns their capture_ids in order.
#
# Each entry carries a LABEL (the tester's name for that screen, else
# ``screen_N``) so generated cases can reference a screen BY NAME instead of
# "the screenshot". Never raises.
def _stash_captures(screens: list, labels: list | None = None) -> list:
    """Stash captured screens in the tray; returns their capture_ids in order."""
    ids: list = []
    with never_raise(logger, "stashing captured screens failed", exc_info=True):
        _sweep_capture_tray()
        _insert_shots(screens, labels, ids)
        # SWEEP AFTER INSERTING, not only before. The sweep at entry bounds
        # what was already there and says nothing about what this call adds:
        # measured 2026-09-06, two stashes of 12 x 3 MiB left the tray at
        # 66 MiB against a 32 MiB cap until some later tool call happened to
        # touch it. A bound that only applies between calls is not a bound on
        # the peak.
        _sweep_capture_tray()
        ids = _only_resolving(ids)
    return ids


def _peek_into(wanted: list, out: tuple) -> None:
    """Resolve the capped slice of *wanted* into ``out = (images, labels, missing)``."""
    images, labels, missing = out
    for cid in wanted[: _limits.tray_max()]:
        item = _limits._CAPTURE_TRAY.get(cid) if cid else None
        if not item:
            # BARE ids here. Three consumers read this list: two render it
            # for the tester and call _describe_missing to put the eviction
            # reason beside each id; the third writes it to the audit row's
            # `capture_ids` field, which must stay queryable by id. Putting
            # the reason in here broke that consumer (round-2 review).
            missing.append(cid or "(blank)")
            continue
        images.append(_tray_image_payload(item))
        labels.append(f"{item['filename']} — {item['label']}")


def _peek_overflow(wanted: list, missing: list) -> None:
    # Beyond the per-call cap. NAMED, not silently sliced away -- as a
    # BARE id (the audit field reads this list by id); the tester-facing
    # consumers derive the per-call reason through _describe_missing /
    # _over_cap_ids from the call's own list. NOT written to the
    # eviction ledger: that is per-id state, and this is a fact about
    # this call (round-4 review).
    missing.extend(cid or "(blank)" for cid in wanted[_limits.tray_max() :])


# READ tray entries for *capture_ids* without consuming them:
# ``(images, labels, missing)``.
#
# Read-only on purpose -- see the module comment above. Every unknown,
# expired, or beyond-the-cap id comes back in *missing* so the reply can
# disclose it: never a silent drop, and never a silent slice either. Also
# sweeps, which is what enforces the TTL on a process that has no timer.
#
# DUPLICATE ids are reported ONCE (2026-08-09, review M1): a repeated id used
# to ship the SAME screen twice and burn two of the per-call cap slots. The
# same collapsing applies to repeated BLANKS, so a list of several blank ids
# names "(blank)" once rather than once per occurrence -- deliberate, and
# stated here rather than left to be rediscovered.
# Never raises.
def peek_captures(capture_ids: list | None) -> tuple:
    """READ tray entries for *capture_ids* without consuming them: ``(images, labels, missing)``."""
    images: list = []
    labels: list = []
    missing: list = []
    with never_raise(logger, "reading captured screens failed", exc_info=True):
        _sweep_capture_tray()
        # DEDUPED before the cap slice: a duplicate must not consume a slot a
        # real screen needs. The SAME normalisation _over_cap_ids slices with.
        wanted = _normalised_ids(capture_ids)
        _peek_into(wanted, (images, labels, missing))
        _peek_overflow(wanted, missing)
    return images, labels, missing


def _tray_image_payload(item: dict) -> dict:
    """The image fields a prepare ships for one tray entry."""
    return {"filename": item["filename"], "mime": item["mime"], "data": item["data"]}
