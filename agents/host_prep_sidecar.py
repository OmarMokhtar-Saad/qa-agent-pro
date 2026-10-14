"""Review-sidecar merge, recognition and duplicate-group helpers."""

from __future__ import annotations

import json
import logging

from agents.host_prep_sidecar_keys import retained_sidecar, sidecar_keys
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

# [merge_retained_sidecar]
# Returning a non-None value here is what lets a BARE finalize -- no
# suite_json, no sidecar -- still reach the sidecar merge branch and pick up
# the acceptance criteria, checklist and ambiguity verdict a previous
# sidecar-only submit produced. That is F3's whole acceptance path.
#
# PRESENT-BUT-EMPTY DISCIPLINE is preserved end to end: only keys a sidecar
# actually MENTIONED are stored and only those are overlaid, so a field
# neither sidecar named stays absent and can never read downstream as "the
# host reviewed and found none".


def merge_retained_sidecar(sidecar_obj, meta: object):
    """This call's sidecar laid over the retained one (either may be absent)."""
    with never_raise(logger, "_merge_retained_sidecar failed", exc_info=True):
        retained = retained_sidecar(meta)
        if not retained:
            return sidecar_obj
        if not isinstance(sidecar_obj, dict):
            return retained
        merged = dict(retained)
        merged.update(sidecar_obj)
        return merged
    return sidecar_obj


# [superseded_sidecar_note]
# A silent overwrite is the same class of loss F3 exists to close: the host
# would have no way to know its earlier review had been replaced.


def superseded_sidecar_note(sidecar_obj, meta: object) -> str:
    """Name the retained keys this call's sidecar overwrote."""
    with never_raise(logger, "_superseded_sidecar_note failed", exc_info=True):
        retained = retained_sidecar(meta)
        if not retained or not isinstance(sidecar_obj, dict):
            return ""
        clashes = sorted(k for k in sidecar_obj if k in retained)
        if not clashes:
            return ""
        return _replaced_text(clashes)
    return ""


def _replaced_text(clashes: list) -> str:
    """The notice paragraph naming the clashing keys."""
    return (
        "> ℹ️  This submission's `"
        + "` / `".join(clashes)
        + "` REPLACED the value retained from an earlier sidecar-only "
        "submit on this prep.\n\n"
    )


# [with_retained_sidecar]
# CRITICAL CONTRACT: the submitted `test_cases` are what must never be lost,
# so every doubt here returns `suite_json` UNCHANGED. A retained field is
# added ONLY when the submission does not carry that key itself -- this call's
# own values always win, and a key neither side mentioned stays absent, which
# is the same present-but-empty discipline the sidecar merge uses.
#
# Path B needs its own fold because `_review_sidecar` returns None for a
# submission that HAS test cases, so a full suite can never enter the sidecar
# merge branch -- and must not, since that branch rebuilds the suite from
# staged rows and would discard everything the host just sent.


def with_retained_sidecar(suite_json, meta: object):
    """Fold a retained sidecar into a FULL (Path B) submission."""
    with never_raise(
        logger,
        "with_retained_sidecar failed - using the submission as sent",
        exc_info=True,
    ):
        retained = retained_sidecar(meta)
        if not retained:
            return suite_json
        data = _full_suite_dict(suite_json)
        if data is None:
            return suite_json
        merged = dict(data)
        for key, value in retained.items():
            if key not in merged:
                merged[key] = value
        return merged
    return suite_json


def _full_suite_dict(suite_json):
    """The submission as a dict when it carries test cases, else None."""
    if isinstance(suite_json, str):
        data = json.loads(suite_json)
    elif isinstance(suite_json, dict):
        data = suite_json
    else:
        return None
    if not isinstance(data, dict) or not data.get("test_cases"):
        return None
    return data


def _json_object(suite_json):
    """The submission parsed to an object, None for blank/unsupported input."""
    if isinstance(suite_json, str):
        raw = suite_json.strip()
        if not raw:
            return None
        return json.loads(raw)
    return suite_json if isinstance(suite_json, dict) else None


# [review_sidecar]
# A sidecar is a JSON object that carries at least one of the recognised
# post-merge review fields (_SIDECAR_KEYS) and has no test cases (missing
# or empty ``test_cases``). It is how the per-category route (Path A) can
# still deliver fields that merge_category_rows structurally drops --
# generalised from the duplicate-groups-only sidecar so the AC boomerang
# works on that route too. Never raises.


def review_sidecar(suite_json, meta: object = None) -> "dict | None":
    """Return the parsed object when suite_json is a REVIEW SIDECAR."""
    with never_raise(logger, "review sidecar parse failed", exc_info=True):
        data = _json_object(suite_json)
        if not isinstance(data, dict):
            return None
        if not any(k in data for k in sidecar_keys(meta)):
            return None
        cases = data.get("test_cases")
        if cases is None or cases == []:
            return data
        return None
    return None


# [dedup_sidecar_groups]
# superseded by review_sidecar for the ONE call site that used to invoke it
# (handle_submit_suite), which now calls review_sidecar instead. Retained --
# not deleted -- solely because the evening ops' own tests still call it
# directly; it is dead in the actual request path.
#
# A sidecar is a JSON object that carries ``duplicate_groups`` and has no
# test cases (missing or empty ``test_cases``). Never raises.


def dedup_sidecar_groups(suite_json) -> "object | None":
    """Return raw duplicate_groups if suite_json is a dedup-only sidecar."""
    with never_raise(logger, "dedup sidecar parse failed", exc_info=True):
        data = _json_object(suite_json)
        if not isinstance(data, dict) or "duplicate_groups" not in data:
            return None
        cases = data.get("test_cases")
        if cases is None or cases == []:
            return data.get("duplicate_groups")
        return None
    return None


def remap_dup_groups(groups, id_map: dict) -> list:
    """Remap tc_ids in duplicate_groups through a merge id_map. Never raises."""
    out = []
    with never_raise(logger, "dup group remap failed", exc_info=True) as guard:
        for g in groups or []:
            if not isinstance(g, (list, tuple)):
                continue
            mapped = [id_map.get(tid, tid) for tid in g if isinstance(tid, str)]
            if mapped:
                out.append(mapped)
    if guard.failed:
        return list(groups or [])
    return out
