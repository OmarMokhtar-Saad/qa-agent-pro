"""Failure signatures and hit mining (split out of knowledge_stage_mistakes)."""

from __future__ import annotations

from tools.mobile.kstg_mist_detail import detail, join
from tools.mobile.kstg_proven import proven

OK_OUTCOMES = ("ok", "assert_pass", "visual_check", "done")
TAP_OPS = ("tap", "tap_text", "click", "long_press")
_OUTCOME_SIGNATURE = {
    "wait_timeout": "timeout",
    "missing_element": "element_missing",
    "route_mismatch": "wrong_screen",
}
_SYMPTOM = {
    "field_collision": "two different fields were typed into %s",
    "text_not_landed": "typing into %s did not land",
    "dead_tap": "tap on %s left the screen unchanged",
    "wrong_screen": "%s led to a different screen than expected",
    "timeout": "waiting after %s timed out",
    "element_missing": "%s was not found on this screen",
    "tester_correction": "a tester corrected the step on %s",
}
_NO_TARGET = "the screen"


def degraded(key: str) -> bool:
    return not key or key.startswith("sk1d:")


def _name(rec: dict) -> str:
    return str(rec.get("target_rid") or "") or _NO_TARGET


def ok(rec: dict) -> bool:
    return str(rec.get("outcome") or "") in OK_OUTCOMES


def _typed_failed(rec: dict) -> bool:
    if rec.get("op") not in ("type", "fill"):
        return False
    return rec.get("landed") is False or rec.get("outcome") == "device_error"


def _dead_tap(rec: dict) -> bool:
    after = str(rec.get("after_screen_key") or "")
    if rec.get("op") not in TAP_OPS or not after:
        return False
    return rec.get("outcome") in ("ok", "no_change") and after == rec.get("screen_key")


def signature_of(rec: dict) -> str:
    """The failure signature of one step, or ``""`` (collisions need the run)."""
    if rec.get("tester_correction"):
        return "tester_correction"
    if _typed_failed(rec):
        return "text_not_landed"
    if _dead_tap(rec):
        return "dead_tap"
    return _OUTCOME_SIGNATURE.get(str(rec.get("outcome") or ""), "")


def _collision(rec: dict, seen: dict) -> bool:
    field = str(rec.get("text_field") or "")
    rid = str(rec.get("target_rid") or "")
    if rec.get("op") != "type" or not field or not rid:
        return False
    first = seen.setdefault((rec.get("screen_key"), rid), field)
    return first != field


def _same_input(later: dict, rec: dict) -> bool:
    """The retry: same op on the same element, or the same field on the right one."""
    if later.get("op") != rec.get("op"):
        return False
    field = str(rec.get("text_field") or "")
    return later.get("target_rid") == rec.get("target_rid") or (
        bool(field) and later.get("text_field") == field
    )


def _fix_of(steps: list, i: int, sig: str) -> dict | None:
    """The later OK step on the same screen_key that contrasts with the failure."""
    rec = steps[i]
    for later in steps[i + 1 :]:
        if later.get("screen_key") != rec.get("screen_key") or not ok(later):
            continue
        if sig == "dead_tap":
            moved = later.get("after_screen_key") not in ("", later.get("screen_key"))
            if moved and later.get("target_rid") != rec.get("target_rid"):
                return later
        elif _same_input(later, rec):
            return later
    return None


def find_hits(steps: list) -> list:
    """One hit per failure episode: ``{sig, rec, prev, fix}`` in step order."""
    hits: list = []
    seen: dict = {}
    last = ("", "", "")
    for i, rec in enumerate(steps):
        sig = "field_collision" if _collision(rec, seen) else signature_of(rec)
        sig = sig if sig and proven(steps, i, sig) else ""
        here = (sig, rec.get("screen_key"), rec.get("target_rid"))
        if sig and here != last:
            prev = steps[i - 1] if i else None
            hits.append(
                {"sig": sig, "rec": rec, "prev": prev, "fix": _fix_of(steps, i, sig)}
            )
        last = here if sig else ("", "", "")
    return hits


def texts(hit: dict) -> tuple:
    """``(symptom, fix)`` from templates and names only."""
    rec, prev, fix = hit["rec"], hit["prev"], hit["fix"]
    symptom = _SYMPTOM[hit["sig"]] % _name(rec)
    if prev is not None and prev.get("op"):
        symptom += " (after %s on %s)" % (prev.get("op"), _name(prev))
    fixed = ""
    if fix is not None:
        fixed = "a later %s on %s worked on this screen" % (fix.get("op"), _name(fix))
    return symptom[:200], join(fixed, detail(rec, fix))
