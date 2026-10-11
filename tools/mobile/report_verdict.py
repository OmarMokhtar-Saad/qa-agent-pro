"""Verdict vocabulary and derivation for the run report: tones, tallies, goals and error classes."""

from __future__ import annotations

from tools.mobile import run_store
from tools.mobile.explore_stop import display_stop
from tools.mobile.replay.screen import DEVICE_ERROR_OUTCOMES
from tools.mobile.report_text import _text, _token

#: The verdicts that mean a case is finished. An empty verdict is NOT one of
#: them, and :func:`_verdict_of` never invents one -- a case shown as passed
#: because a field was missing is the worst artifact a report can produce.
#: Re-exported from `run_store`, which is the layer that decides whether a
#: case will be handed out again. Two literals drifted once already.
DONE_VERDICTS = run_store.DONE_VERDICTS


#: Re-exported for the same reason ``DONE_VERDICTS`` is: ONE producer of
#: "reached a verdict", in the layer that owns what a verdict means, read here
#: and by the chat surface. A local copy is how the last such value came to
#: have three definitions.
verdict_coverage = run_store.verdict_coverage


coverage_phrase = run_store.coverage_phrase

#: Tiles always rendered, in this order, so a zero is visible rather than absent.
TILES = (
    "pass",
    "fail",
    "blocked",
    "unverified",
    "needs_tester",
    "needs_model",
    "planning",
    "unknown",
)

#: How each verdict reads on the page: (swatch, segment, pill, label). Status
#: colours are reserved for state, and every colour ships with its word.
VERDICT_TONE = {
    "pass": ("ok", "s-pass", "p-ok", "Passed"),
    "fail": ("def", "s-def", "p-def", "Failed"),
    "blocked": ("gap", "s-gap", "p-gap", "Blocked"),
    "unverified": ("gap", "s-gap", "p-gap", "Not verified"),
    "needs_tester": ("void", "s-void", "p-void", "Needs the tester"),
    "needs_model": ("void", "s-void", "p-void", "Needs the model"),
    "planning": ("void", "s-void", "p-void", "Planning"),
    "unknown": ("void", "s-void", "p-void", "No verdict"),
}


_OTHER_TONE = ("void", "s-void", "p-void", "")


def _verdict_of(case: object) -> str:
    """The verdict to show, never invented.

    A terminal verdict wins. Otherwise the STATUS is shown (``planning``,
    ``needs_model``, ...) so a case in flight reads as in flight. An empty
    verdict with no status is ``unknown`` -- never ``pass``.
    """
    body = case if isinstance(case, dict) else {}
    verdict = _token(body.get("verdict"))
    if verdict in DONE_VERDICTS:
        return verdict
    status = _text(body.get("status"), 40)
    return _token(status) if status else "unknown"


def _tone(verdict: str) -> tuple:
    sw, seg, pill_cls, label = VERDICT_TONE.get(verdict, _OTHER_TONE)
    return sw, seg, pill_cls, (label or verdict.replace("_", " "))


def tally(cases: object) -> dict:
    """Counts per verdict, with every tile present at zero."""
    out = {name: 0 for name in TILES}
    for case in list(cases or []):
        key = _verdict_of(case)
        out[key] = out.get(key, 0) + 1
    return out


#: The literal `session._explore_case` writes as an exploratory turn's one step.
EXPLORE_GOAL_PREFIX = "Explore towards the goal: "


EXPLORATORY = "Exploratory"


def _unify_verdict(raw: object) -> str:
    """``pass``/``fail``/``blocked`` pass through; every other word is ``unfinished``.

    ``unverified`` is a verdict the run could not stand behind, so it reads as
    blocked, not as a pass. In-flight states (``needs_model``, ``planning``,
    ``needs_tester``, ``done`` without a verdict) and ``unknown`` never finished.
    """
    word = _token(raw)
    if word in ("pass", "fail", "blocked"):
        return word
    if word == "unverified":
        return "blocked"
    return "unfinished"


def _case_type(case: object, manifest: dict) -> str:
    body = case if isinstance(case, dict) else {}
    planned = _planned_case(manifest, _text(body.get("tc_id"), 40))
    return _text(planned.get("type") or body.get("type"), 40)


def _run_kind(cases: object, manifest: dict | None = None) -> str:
    """``exploratory``, ``scripted`` or ``mixed``; an empty run is ``scripted``."""
    manifest = manifest if isinstance(manifest, dict) else {}
    kinds = {_case_type(case, manifest) == EXPLORATORY for case in list(cases or [])}
    if kinds == {True}:
        return "exploratory"
    if True in kinds:
        return "mixed"
    return "scripted"


def _run_goals(manifest: dict) -> list:
    explore = manifest.get("explore") if isinstance(manifest, dict) else None
    explore = explore if isinstance(explore, dict) else {}
    charter = explore.get("charter")
    charter = charter if isinstance(charter, dict) else {}
    goal = _text(charter.get("goal") or explore.get("goal"), 2000)
    return [goal] if goal else []


def _goal_of(case: object, manifest: dict) -> str:
    """The goal text an exploratory turn was driving at, read from its one step."""
    body = case if isinstance(case, dict) else {}
    planned = _planned_case(manifest, _text(body.get("tc_id"), 40))
    for step in planned.get("steps") or []:
        action = _text(step.get("action") if isinstance(step, dict) else "", 2000)
        if action.startswith(EXPLORE_GOAL_PREFIX):
            return action[len(EXPLORE_GOAL_PREFIX) :].strip()
    return ""


def _derive_goals(cases: object, manifest: dict) -> list:
    """Exploratory turns grouped by goal, in first-seen order.

    ``[{"goal": text, "cases": [case, ...]}]``. The run's charter goal is
    listed first even before a turn names it; a turn whose own step names a
    different goal opens a group of its own; a turn naming no goal at all is
    its own group labelled by its title, never dropped. Scripted cases are not
    goals and are skipped.
    """
    manifest = manifest if isinstance(manifest, dict) else {}
    groups: list = []
    by_goal: dict = {}
    for goal in _run_goals(manifest):
        by_goal[goal] = {"goal": goal, "cases": []}
        groups.append(by_goal[goal])
    for case in list(cases or []):
        if _case_type(case, manifest) != EXPLORATORY:
            continue
        goal = _goal_of(case, manifest)
        if not goal:
            body = case if isinstance(case, dict) else {}
            groups.append(
                {
                    "goal": _text(body.get("title") or body.get("tc_id"), 250),
                    "cases": [case],
                }
            )
            continue
        # A charter goal is stored whole while a turn's copy may be the
        # truncated title-length form: match on the shared prefix.
        match = next(
            (
                g
                for text, g in by_goal.items()
                if text == goal or text.startswith(goal) or goal.startswith(text)
            ),
            None,
        )
        if match is None:
            match = by_goal[goal] = {"goal": goal, "cases": []}
            groups.append(match)
        match["cases"].append(case)
    return [g for g in groups if g["cases"]]


def _goal_result(cases: list) -> str:
    """A goal's one result: the newest turn that reached a verdict decides it.

    Exploratory turns before the committing one carry no verdict of their own
    (they are steps towards the goal), so they cannot fail it; a goal whose
    turns never reached one is ``unfinished``.
    """
    for case in reversed(list(cases or [])):
        body = case if isinstance(case, dict) else {}
        verdict = _token(body.get("verdict"))
        if verdict in DONE_VERDICTS:
            return _unify_verdict(verdict)
    return "unfinished"


def _planned_case(manifest: dict, tc_id: str) -> dict:
    """The suite's own record of the case, when the manifest carries one."""
    for entry in manifest.get("cases") or []:
        if isinstance(entry, dict) and _text(entry.get("tc_id"), 40) == tc_id:
            return entry
    return {}


def error_classes(facts: object) -> dict:
    """``{outcome: count}`` over every trace entry of every case. ONE derivation.

    The overview KPI, the Logs breakdown and any case badge all read THIS dict. A section
    that counted its own rows would be a second derivation of one answer, and mirrored
    conditions drift -- which is why the pin monkeypatches this function and requires BOTH
    consuming sections to move.

    Only outcomes in ``executor.DEVICE_ERROR_OUTCOMES`` are counted. An outcome minted
    there and missing from that set fails the source scan in the pin, not silently here.
    """
    counts: dict = {}
    for fact in facts if isinstance(facts, (list, tuple)) else ():
        rows = (fact or {}).get("rows") if isinstance(fact, dict) else None
        for row in rows if isinstance(rows, (list, tuple)) else ():
            outcome = (
                str((row or {}).get("outcome") or "") if isinstance(row, dict) else ""
            )
            if outcome in DEVICE_ERROR_OUTCOMES:
                counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def error_phrase(counts: object) -> str:
    """``"3 (1 dump_failed, 2 left_app)"`` -- or the named zero, never a blank."""
    book = counts if isinstance(counts, dict) else {}
    total = sum(book.values())
    if not total:
        return "0 - no action on this run ended for a device reason"
    return (
        str(total)
        + " ("
        + ", ".join(str(n) + " " + name for name, n in sorted(book.items()))
        + ")"
    )


def _app_logged(loaded: object) -> bool:
    """Whether the run holds an app log.

    When it does not, the Diagnostics "App evidence" part says why, once. Every
    case and turn repeating the same run-wide reason said nothing new.
    """
    content = loaded.get("content") if isinstance(loaded, dict) else None
    return isinstance(content, dict) and content.get("source") not in (None, "none")


def explore_stop(manifest: object) -> str:
    """Why this exploratory run has stopped, or ``""``. ONE producer.

    ``explore_runner.display_stop`` is the callee, and the producer underneath it
    is ``stop_reason`` with THREE clauses: a recorded ``stop``, the turn budget,
    and the wall-clock deadline. The two names are one core with two contracts:
    ``display_stop`` is TOTAL because this is a RENDER path, while
    ``stop_reason`` stays strict because ``session.resolve``'s containment gate
    IS its raise. Reading
    ``explore["stop"]`` alone -- which this module used to do -- answers only the
    first, so a run whose deadline passed with no final turn read as still
    running forever. Measured on mrun-20260908-061316-a41b2a: the page said "in
    progress" 1h42m after the deadline, while ``session.resolve`` -- the other
    consumer of the same fact, at the anchor
    ``stop = explore_runner.stop_reason(explore)`` -- already said ``report``.
    Cited by ANCHOR and not by line: several sessions are moving that file, and
    source rots faster than a comment about it.

    ``""`` for a suite run: the question does not apply there, and the caller's
    own checkpoint arithmetic answers it.
    """
    # The RENDER contract, delegated rather than re-implemented. NOT
    # `stop_reason`: that one is strict on purpose -- it is `session.resolve`'s
    # containment gate -- and a manifest with a junk `deadline` reaching it from
    # here raised through `_is_partial` into `render`'s outer `except`, which
    # writes no page at all.
    return display_stop(manifest)
