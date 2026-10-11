"""Mobile session: run planning, resumption and readback (resolve, list_runs). Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
import math

from tools.mobile import (
    explore_runner,
    importers,
    run_finalize,
    run_store,
    scheduler,
    sdk_locator,
)
from tools.mobile.replay.context import Context
from tools.mobile.session_core import (
    LANE_EXPLORE,
    LANE_SUITE,
    STATE_ABANDONED,
    STATE_GATE,
    STATE_REPORT,
    STATE_RUNNING,
    mint_run_id,
)
from tools.mobile.session_records import (
    capture_record,
    device_record,
    locale_record,
    reset_app_record,
)

logger = logging.getLogger("tools.mobile.session")


def _case_dumps(cases: object) -> list[dict]:
    """Case bodies for the manifest, so a fresh chat can rehydrate them.

    ``scheduler.plan_run`` writes only the ORDER, and nothing in Phases 1-2
    writes the cases themselves -- so a resumed chat would hold a tc_id it could
    not run. Three of the six start-menu sources never produce a stored suite
    (pasted markdown, csv, xlsx) and an exploratory run has none at all, so
    re-loading from ``suite_store`` was not an option.
    """
    out: list[dict] = []
    for case in list(cases or []):
        try:
            out.append(case.model_dump(mode="json"))
        except (ValueError, TypeError, AttributeError):
            logger.warning("mobile.session: uncheckpointable case skipped")
    return out


def case_signature(package: object, cases: object) -> str:
    """An identity for "this app, these cases", stable across chats.

    Derived from the package and the SORTED (tc_id, title) pairs, so the order
    the cases were pasted in does not make a second run look like a new one,
    and a genuinely different set never collides.

    It exists because on 2026-09-04 a session started EIGHT runs of one case in
    seventeen minutes: nothing could tell the model that the run it had just
    abandoned was the one to go back to, so it planned another.
    """
    import hashlib

    pairs = []
    for case in list(cases or []):
        try:
            body = case.model_dump(mode="json") if hasattr(case, "model_dump") else case
        except (ValueError, TypeError, AttributeError):  # pragma: no cover
            continue
        if not isinstance(body, dict):
            continue
        # THE STEPS TOO, not only the id and the title. Hashing the name alone
        # meant a tester who EDITED TC-001's steps and re-submitted was offered
        # the previous run -- which then executed the old steps and reported
        # them under the new case's name. A silently wrong result is worse than
        # a duplicate run, which is all this signature exists to avoid.
        steps = []
        for step in body.get("steps") or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                str(step.get("action") or "")
                + "\u0000"
                + str(step.get("test_data") or "")
                + "\u0000"
                + str(step.get("expected_result") or "")
            )
        pairs.append(
            str(body.get("tc_id") or "")
            + "\u0000"
            + str(body.get("title") or "")
            + "\u0000"
            + "\u0003".join(steps)
        )
    seed = str(package or "") + "\u0001" + "\u0002".join(sorted(pairs))
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]


def run_signature(run_id: str) -> str:
    """The signature recorded on a run's manifest, or ``""``."""
    manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
    return str(manifest.get("case_signature") or "")


def find_resumable_run(signature: str, limit: int = 60) -> dict | None:
    """The newest UNFINISHED run of the same app and the same cases, or None.

    Deliberately keyed on package plus case set and NOT on the serial. A run is
    a durable thing on disk; a serial is which emulator happened to be attached
    when it started. A tester whose emulator was restarted, or who is resuming
    from another chat, still wants the run they were in the middle of --
    ``_mobile_resume_device_stage`` re-checks the device on every resume, and
    the lane's device lock already serialises concurrent starts, so ignoring
    the serial here adds no hazard.

    The *limit* is a real bound, stated rather than hidden: the newest 60 runs
    are considered, and a resumable run older than that is not offered. It is
    an offer, not a guarantee, and `qa_mobile_status` still lists everything.

    Never raises: this decides whether to OFFER something, and a lookup that
    failed must fall through to planning a run rather than refusing one.
    """
    try:
        wanted = str(signature or "")
        if not wanted:
            return None
        for row in (list_runs(limit) or {}).get("content") or []:
            offer = _resumable_offer(row, wanted)
            if offer:
                return offer
        return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.session.find_resumable_run failed")
        return None


def _resumable_offer(row: dict, wanted: str) -> dict | None:
    """The offer for one listed run when it matches *wanted* and is unfinished."""
    manifest = row.get("manifest") or {}
    # THE KEPT SIGNATURE, and only that one.
    #
    # There was a second comparison here against `source_signature` --
    # the cases the tester supplied before the filters -- so that a
    # re-run of failures could recognise its own earlier run. Round 3
    # showed it had to be conditioned on "that run was started with no
    # filter", because otherwise a tester who ran five cases filtered
    # to two and then started the whole suite was told the earlier run
    # was "running the same cases": it was running two of five, and
    # accepting the offer finishes with three never executed and a
    # report that looks complete.
    #
    # And with that condition the clause is DEAD, which mutation then
    # proved: a run started with no filter has the two signatures EQUAL
    # by construction, so the kept comparison has already matched
    # whenever the source one could. An unkillable guard is not a
    # guard, so it is gone and `source_signature` stays on the manifest
    # as the record of what was asked for.
    if wanted != str(manifest.get("case_signature") or ""):
        return None
    # NO package argument at all. There was a package comparison here
    # and mutation proved it unreachable -- `case_signature` is seeded
    # WITH the package, so a signature match already implies it -- and
    # round 2 then pointed out that keeping the PARAMETER after
    # deleting its use is a trap for a caller who passes a package that
    # did not build the signature. The binding is in the signature, and
    # `test_a_different_package_gives_a_different_signature` pins it.
    run_id = str(row.get("run_id") or "")
    resolved = (resolve(run_id) or {}).get("content") or {}
    if not resolved or resolved.get("finished"):
        return None
    return {
        "run_id": run_id,
        "state": str(resolved.get("state") or ""),
        "done": int(resolved.get("done") or 0),
        "total": int(resolved.get("total") or 0),
    }


def _no_case_survived(ordered: dict) -> dict:
    """The refusal for a filter set that dropped every case."""
    applied = ", ".join((ordered.get("content") or {}).get("applied") or []) or "none"
    return {
        "error": (
            f"No case survived the filters, so no run was created (applied: {applied})."
        ),
        "content": None,
    }


def _run_records(
    device: object, locale: object, capture: object, reset_app: object
) -> dict:
    """The four environment records both lanes put on a new run's manifest."""
    return {
        # WHAT LANGUAGE THIS RAN IN, on the manifest for the same reason
        # `device` is: a finished run must still say so after the device
        # is gone, and two readers asking the device again could get two
        # answers.
        "locale": locale_record(locale),
        # WHAT HARDWARE THIS RAN ON, recorded once at planning time.
        # On the manifest rather than re-read per status call because a
        # finished run must still say what it ran on after the device is
        # unplugged, and because two readers asking the device again
        # could get two answers.
        "device": device_record(device),
        # WHAT TIER THIS RUN'S API CAPTURE REACHED, recorded once at
        # planning time for the same reason `locale`/`device` are: a
        # finished run must still say so after the proxy is torn down.
        "capture": capture_record(capture),
        "reset_app": reset_app_record(reset_app),
    }


def _suite_manifest(
    cases: object, kept: list, package: str, serial: str, source: str
) -> dict:
    """The suite lane's own manifest keys (identity, signatures, kept cases)."""
    return {
        "lane": LANE_SUITE,
        "package": str(package or ""),
        "serial": str(serial or ""),
        "source": str(source or ""),
        "case_signature": case_signature(package, kept),
        # The cases the TESTER supplied, before the filters -- the
        # RECORD of what was asked for, and nothing more.
        #
        # It was added so a re-run of failures could recognise its own
        # earlier run, and `find_resumable_run` no longer reads it: that
        # match had to be conditioned on "started with no filter", and
        # with that condition it was dead. The flow survives anyway,
        # because a run's manifest stores the KEPT cases, so a second
        # re-run hashes those and matches `case_signature`.
        "source_signature": case_signature(package, cases),
        "cases": _case_dumps(kept),
    }


def _explore_manifest(state: dict, package: str, serial: str, avd: str) -> dict:
    """The exploratory lane's own manifest keys."""
    return {
        "lane": LANE_EXPLORE,
        "package": str(package or ""),
        "serial": str(serial or ""),
        "avd": str(avd or ""),
        # A SNAPSHOT of the PRODUCER's answer, not of a setting.
        # sdk_locator.avd_system_image() is the one function that says which
        # image this lane runs: it reads image.sysdir.1 out of the AVD's own
        # config.ini, so it reports what the device on disk actually boots.
        # Reading qa_mobile_system_image_tag here would be a SECOND derivation
        # -- and since the tester brings their own AVD, no setting decides it.
        # "" when the AVD cannot be resolved, which the report renders as "not
        # recorded" rather than inventing today's answer for a finished run.
        "system_image": str(
            (sdk_locator.avd_system_image(avd) or {}).get("content") or ""
        ),
        "order": [],
        "total": 0,
        "cases": [],
        "explore": state,
    }


def plan_suite_run(
    cases: object,
    *,
    package: str,
    serial: str,
    source: str = "",
    filters: object = None,
    avd: str = "",
    device: object = None,
    locale: object = None,
    capture: object = None,
    reset_app: object = None,
) -> dict:
    """Order, persist and create a suite run. ``{"error", "content": {...}}``."""
    try:
        ordered = scheduler.order_cases(cases, filters)
        if ordered.get("error"):
            return ordered
        kept = list((ordered.get("content") or {}).get("cases") or [])
        if not kept:
            return _no_case_survived(ordered)
        run_id = mint_run_id()
        planned = scheduler.plan_run(
            run_id,
            kept,
            None,
            manifest_extra={
                **_suite_manifest(cases, kept, package, serial, source),
                **_run_records(device, locale, capture, reset_app),
                "avd": str(avd or ""),
            },
        )
        if planned.get("error"):
            return planned
        body = dict(planned.get("content") or {})
        body["run_id"] = run_id
        body["lane"] = LANE_SUITE
        body["dropped"] = (ordered.get("content") or {}).get("dropped") or []
        body.pop("cases", None)
        return {"error": None, "content": body}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.plan_suite_run failed")
        return {"error": str(exc), "content": None}


def plan_explore_run(
    goal: str,
    *,
    package: str,
    serial: str,
    charter: object = None,
    watch_for: object = (),
    avd: str = "",
    device: object = None,
    locale: object = None,
    capture: object = None,
    reset_app: object = None,
) -> dict:
    """Create an exploratory run whose whole state is one manifest dict."""
    try:
        text = " ".join(str(goal or "").split())
        if len(text) < 5:
            return {
                "error": (
                    "Give the exploratory goal in a sentence -- what should the "
                    "app be made to do? Nothing was started."
                ),
                "content": None,
            }
        run_id = mint_run_id()
        # The run's TERMS go into the state, which IS manifest["explore"], so a
        # resume from another chat explores under the same charter.
        # charter=None reproduces this call's previous behaviour exactly.
        state = explore_runner.new_state(text, watch_for, charter=charter)
        created = run_store.create_run(
            run_id,
            {
                **_explore_manifest(state, package, serial, avd),
                **_run_records(device, locale, capture, reset_app),
            },
        )
        if created.get("error"):
            return created
        return {
            "error": None,
            "content": {"run_id": run_id, "lane": LANE_EXPLORE, "explore": state},
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.plan_explore_run failed")
        return {"error": str(exc), "content": None}


def import_cases(source: object) -> dict:
    """A tester's own cases -> ``{cases, rejected, truncated}``. Never raises."""
    return importers.load(source)


def load_case(run_id: str, tc_id: str) -> dict:
    """Rehydrate ONE ``TestCase`` from the run manifest.

    This is the other half of :func:`_case_dumps`: without it a resumed chat
    holds a tc_id and nothing to plan against.
    """
    try:
        from tools.models import TestCase

        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        for body in list(manifest.get("cases") or []):
            if not isinstance(body, dict):
                continue
            if str(body.get("tc_id") or "") != str(tc_id):
                continue
            try:
                return {"error": None, "content": TestCase(**body)}
            except (ValueError, TypeError) as exc:
                return {
                    "error": (
                        "Case "
                        + str(tc_id)
                        + " in run "
                        + str(run_id)
                        + " no longer validates ("
                        + str(exc)[:200]
                        + "), so it was not run."
                    ),
                    "content": None,
                }
        return {
            "error": (
                "Run "
                + str(run_id)
                + " has no case "
                + str(tc_id)
                + " on disk, so there is nothing to run."
            ),
            "content": None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.load_case failed")
        return {"error": str(exc), "content": None}


def load_run_cases(run_id: str) -> dict:
    """EVERY case of a run, rehydrated. Used by the re-run-failures source.

    Re-running the failures of a previous run needs that run's own cases, and
    the previous run is the only place they exist -- the suite it came from may
    never have been stored (a pasted markdown table is not a suite).
    """
    try:
        from tools.models import TestCase

        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        out: list = []
        for body in list(manifest.get("cases") or []):
            if not isinstance(body, dict):
                continue
            try:
                out.append(TestCase(**body))
            except (ValueError, TypeError):
                logger.warning("mobile.session: case in %s no longer validates", run_id)
        if not out:
            return {
                "error": ("Run " + str(run_id) + " has no cases on disk to re-run."),
                "content": None,
            }
        return {"error": None, "content": out}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.load_run_cases failed")
        return {"error": str(exc), "content": None}


def context_for(resolved: object) -> Context:
    """Build the per-call device context. Holds no tester value by default."""
    body = resolved if isinstance(resolved, dict) else {}
    # THE APP UNDER TEST, which a multi-app goal may have handed over (fix
    # round 3, item 1): `explore["app"]` is written only by
    # `_submit_explore`, after `explore_runner.handover` checked it against
    # the package the server itself saw in front. The run's own package stays
    # `home_package`, the only one `clear_app_data` may wipe. The launch
    # activity belongs to the home app, so after a hand-over it is left empty
    # and resolved from the device instead.
    explore = body.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    home = str(body.get("package") or "")
    handed = str(explore.get("app") or "")
    return Context(
        serial=str(body.get("serial") or ""),
        package=handed or home,
        home_package=home,
        activity="" if handed else str(body.get("activity") or ""),
        # The lane decides whether a script that asserted nothing is an
        # omission worth naming. Set HERE, at the one production construction
        # site, which both explore entry points (``next_packet`` and
        # ``submit``) already go through -- so the value cannot reach the
        # executor by one path and not the other.
        asserts_expected=str(body.get("lane") or "") != LANE_EXPLORE,
        # Per-app knowledge: the run's id and lane. ``begin_run`` reads the env
        # and app version from the run's manifest.
        run_id=str(body.get("run_id") or "") or None,
        lane=str(body.get("lane") or "") or None,
    )


def _lane_progress(run_id: str, manifest: dict, explore: dict) -> tuple[str, dict, str]:
    """``(state, scheduler point, explore stop)`` before the heartbeat check."""
    # PUBLISHED, not just branched on. This value already decided `state`
    # here while `report._is_partial` derived the same fact from the weaker
    # `explore["stop"]` and disagreed with it on the very same manifest.
    # Handing it out means every consumer reads the one producer.
    if str(manifest.get("lane") or "") == LANE_EXPLORE:
        explore_stop = explore_runner.stop_reason(explore)
        return (STATE_REPORT if explore_stop else STATE_RUNNING), {}, explore_stop
    point = (scheduler.next_case(run_id) or {}).get("content") or {}
    if point.get("finished") or run_finalize.stop_text(manifest):
        return STATE_REPORT, point, ""
    if point.get("gate"):
        return STATE_GATE, point, ""
    return STATE_RUNNING, point, ""


def _lease_age(lease: dict) -> float:
    """The lease's heartbeat age; a non-finite one reads as ZERO."""
    # A non-finite age reads as ZERO, i.e. "just refreshed", which is the
    # SAFE direction here: it leaves the run alone rather than declaring
    # somebody else's live session abandoned. Left unguarded, a NaN makes
    # the comparison in `_is_abandoned` False and the run could never be
    # reclaimed at all, because every comparison against NaN is False.
    age = float(lease.get("age") or 0.0)
    return age if math.isfinite(age) else 0.0


def _is_abandoned(state: str, lease: dict, lease_age: float) -> bool:
    """An unfinished run whose heartbeat stopped."""
    # D7 (2026-09-03): an unfinished run whose heartbeat stopped. Two runs
    # from the audit -- mrun-20260903-103316-b09ee4 (TC-003 stuck in
    # "planning", TC-004 never started) and ...103359-5b01fc -- still held
    # a lease, had no report, and `qa_mobile_status` reported them as
    # plain "running", so a tester had no way to tell a run that was
    # thinking from one nothing was driving. The lease's own heartbeat age
    # is the signal; STALE is the same threshold a takeover uses, so the
    # state and the takeover rule cannot drift apart.
    return (
        state not in (STATE_REPORT, STATE_GATE)
        and str(lease.get("state") or run_store.NONE) != run_store.NONE
        and lease_age > run_store.LEASE_STALE_S
    )


def _resolve_body(run_id: str, manifest: dict, lease: dict) -> dict:
    """The ``content`` of a resolved run, from its manifest and lease."""
    explore = manifest.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    state, point, explore_stop = _lane_progress(run_id, manifest, explore)
    lease_age = _lease_age(lease)
    if _is_abandoned(state, lease, lease_age):
        state = STATE_ABANDONED
    is_explore = str(manifest.get("lane") or "") == LANE_EXPLORE
    return {
        "run_id": str(run_id),
        "state": state,
        "lane": str(manifest.get("lane") or LANE_SUITE),
        "package": str(manifest.get("package") or ""),
        "serial": str(manifest.get("serial") or ""),
        "device": device_record(manifest.get("device")),
        "avd": str(manifest.get("avd") or ""),
        "source": str(manifest.get("source") or ""),
        "total": int(manifest.get("total") or 0),
        "done": int(point.get("done") or 0),
        "failed": list(point.get("failed") or []),
        "next_tc_id": str(point.get("tc_id") or ""),
        "gate": bool(point.get("gate")),
        # The explore lane has no scheduler and therefore no `point`,
        # so this key was False for the whole life of every exploratory
        # run -- which is why a stopped run's summary was headed
        # "Mobile run progress" and its report offered "ask again later
        # for the rest". Its producer is `stop_reason`.
        "finished": bool(explore_stop) if is_explore else bool(point.get("finished")),
        "explore_stop": explore_stop,
        "final": run_finalize.recorded(manifest),
        "explore": explore,
        "holder": str(lease.get("holder") or ""),
        "lease_state": str(lease.get("state") or run_store.NONE),
        "lease_age": lease_age,
    }


def resolve(run_id: str, session_token: str = "") -> dict:
    """Where this run is, read from disk ONLY.

    Nothing about this function's answer depends on anything a previous call
    left in memory, which is what makes every state re-entrant from a fresh
    chat given only a ``run_id``.
    """
    try:
        if not run_store.valid_run_id(run_id):
            return {
                "error": "Refusing " + repr(str(run_id)[:40]) + " as a run id.",
                "content": None,
            }
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        if not isinstance(manifest, dict) or not manifest:
            return {
                "error": (
                    "No run `"
                    + str(run_id)
                    + "` on this machine. `qa_mobile_status` with no run id "
                    "lists the runs that do exist."
                ),
                "content": None,
            }
        lease = (run_store.lease_status(run_id, str(session_token or "-")) or {}).get(
            "content"
        ) or {}
        return {"error": None, "content": _resolve_body(run_id, manifest, lease)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.resolve failed")
        return {"error": str(exc), "content": None}


def summary(run_id: str) -> dict:
    """Every checkpoint body for a run, newest verdicts included."""
    return run_store.list_cases(run_id)


def coverage_line(resolved: object, cases: object) -> str:
    """The ONE verdict-coverage sentence for a resolved run. Never raises.

    Thin on purpose. The producer is ``run_store.verdict_coverage`` /
    ``coverage_phrase`` -- the same pair ``report.py`` renders from -- and this
    is only the seam that lets the chat surface reach it without ``render.py``
    importing a store: that module imports nothing internal, and the two blocks
    of one status reply must print the SAME string rather than two counts that
    disagree about whether a status may stand in for a verdict.
    """
    try:
        body = resolved if isinstance(resolved, dict) else {}
        return run_store.coverage_phrase(
            run_store.verdict_coverage(
                cases,
                {
                    "lane": body.get("lane"),
                    "total": body.get("total"),
                    "explore": body.get("explore"),
                },
            )
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not summarise verdict coverage")
        return ""


def list_runs(limit: int = 10) -> dict:
    return run_store.list_runs(limit)
