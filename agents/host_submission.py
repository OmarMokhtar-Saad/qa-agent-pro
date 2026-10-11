"""Host mode: submitted-suite parsing, salvage and validation. Cut verbatim from agents/host_mode.py (P4.3)."""

from __future__ import annotations

import dataclasses
import json
import logging
import re

from agents.host_dedup import _extract_duplicate_groups
from agents.host_prep_serde import PrepParseError
from config.settings import settings
from tools.models import TestCase, TestSuite

logger = logging.getLogger("agents.host_mode")

# Cap on how many dropped-case reasons parse_host_suite reports, so a hostile
# 100k-garbage-case submission cannot produce a 100k-line reason list.
_MAX_DROPPED_REASONS = 20
_MAX_FIELD_ERRORS_PER_CASE = 20


@dataclasses.dataclass
class ParsedSubmission:
    """Result of parse_host_suite. Carries the validated suite AND the salvage
    delta so ops-3d can ALWAYS tell the tester "N case(s) were dropped as
    malformed" -- independent of checklist config. Silence about
    dropped cases was the thing being fixed: without this, a host submitting 40
    cases of which 39 are malformed would yield a silent 1-case suite presented
    as finished."""

    suite: TestSuite
    dropped_count: int = 0
    dropped_reasons: list = dataclasses.field(default_factory=list)
    # 2026-08-31 (F9): cases KEPT after their OPTIONAL `test_data` plan failed
    # validation. Deliberately distinct from dropped_reasons -- the case is in
    # the suite and the plan is not, and saying exactly that is the difference
    # between a disclosure and a silent partial accept.
    salvaged_reasons: list = dataclasses.field(default_factory=list)
    # Piece 1: the host's OPTIONAL cross-category duplicate review, SHAPE-validated
    # against this suite's tc_ids (see _extract_duplicate_groups). Still unscreened
    # -- screen_duplicate_groups applies the safety bounds before anything is
    # removed. Empty on the per-category path, where the field cannot be used.
    duplicate_groups: list = dataclasses.field(default_factory=list)
    # True when the submission actually CARRIED a `duplicate_groups` key, however
    # malformed. Distinguishes "the host reviewed and found no duplicates" from
    # "the host ignored the request" -- both yield an empty list, but only the
    # second means no review happened. Always False on the per-category merge
    # path, where _merge_category_rows structurally drops the field.
    duplicate_review_offered: bool = False
    # Why part of that field was rejected. Surfaced in the reply -- silence about a
    # rejected untrusted field is the failure mode being avoided.
    duplicate_notes: list = dataclasses.field(default_factory=list)
    # The host's OPTIONAL `requirement_matches` field, carried RAW and
    # UNVALIDATED. It is popped here (before TestSuite validation, which sets
    # extra="forbid") so a stray field from a host still following an older
    # prompt cannot fail an otherwise valid submit. NOTHING READS IT since the
    # host coverage review was deleted (2026-08-12); it is kept for that
    # tolerance alone.
    raw_requirement_matches: object = None
    # The host's OPTIONAL `acceptance_criteria` field (the AC boomerang job's
    # return_field), carried RAW and UNVALIDATED for exactly the same reason:
    # it is popped before TestSuite validation (extra="forbid") but validated
    # later, in extract_host_acs. Nothing may read it without validating it.
    raw_acceptance_criteria: object = None
    # The entailment review's OPTIONAL `grounding_verdicts`, raw and unvalidated,
    # for the same reason: popped before TestSuite validation (extra="forbid") and
    # validated later, in tools.grounding_verdicts.parse_verdicts, which matches
    # every id against the suite that was actually submitted. Absent is NOT a
    # failure -- the review is optional, so an absent field just means no
    # grounding report.
    raw_grounding_verdicts: object = None
    # The ambiguity job's OPTIONAL `ambiguity_result`, raw and unvalidated.
    # Absent is meaningful here: it means the blocking safety preflight
    # left no evidence it ran (see extract_ambiguity_result).
    raw_ambiguity_result: object = None
    # The image job's OPTIONAL `image_descriptions`, raw and unvalidated.
    # Absent is NOT a failure here: the job is non-blocking, so an absent field
    # only means the server has no record of what the screenshots showed.
    raw_image_descriptions: object = None
    # Residue R4: the checklist job's OPTIONAL `checklist_items` field, raw and
    # unvalidated. Popped before TestSuite validation (extra="forbid") and
    # validated later in extract_host_checklist. Absent means NO checklist: the
    # server does not decompose the ticket to fill the gap.
    raw_checklist_items: object = None


def _bounded_json_spans(raw: str, *, budget: int):
    """Yield each TOP-LEVEL balanced ``{...}`` object in ``raw``, string/escape
    aware, in a SINGLE forward pass -- every character is visited at most once,
    so total work is O(len(raw)).

    This deliberately does NOT reuse ``llm._balanced_json_spans``: that helper
    re-scans forward from EVERY ``{`` (and yields nested spans), which is O(n^2)
    on adversarial input -- a 4 MB unbalanced-brace blob would scan for days, and
    even 64 KB takes minutes -- and it is shared with the server-mode ask_json
    parser, so it must not change. Host-submitted JSON is UNTRUSTED, so a linear,
    self-contained scanner is used here. ``budget`` is a hard ceiling on the
    number of characters visited; exceeding it raises PrepParseError so a hostile
    blob is rejected FAST rather than hanging.
    """
    n = len(raw)
    depth = 0
    start = -1
    in_string = False
    escaped = False
    i = 0
    while i < n:
        if i >= budget:
            raise PrepParseError("submitted JSON exceeded the scan budget")
        ch = raw[i]
        if in_string:
            in_string, escaped = _string_scan_state(ch, escaped)
        elif ch == '"':
            in_string = True
        elif ch in "{}":
            depth, start, span = _brace_scan_step(raw, i, depth, start)
            if span is not None:
                yield span
        i += 1


def _string_scan_state(ch: str, escaped: bool) -> tuple[bool, bool]:
    """One character inside a JSON string: return ``(in_string, escaped)``."""
    if escaped:
        return True, False
    if ch == "\\":
        return True, True
    return ch != '"', False


def _brace_scan_step(
    raw: str, i: int, depth: int, start: int
) -> tuple[int, int, str | None]:
    """Apply the brace at ``raw[i]``: return ``(depth, start, closed_span)``.

    ``closed_span`` is the top-level object just completed, else ``None``.
    """
    if raw[i] == "{":
        return depth + 1, (i if depth == 0 else start), None
    if depth == 0:
        return depth, start, None
    depth -= 1
    if depth == 0 and start >= 0:
        return 0, -1, raw[start : i + 1]
    return depth, start, None


def _pop_sidecar_fields(data) -> tuple[dict, object, dict]:
    """Pop every submission-level key off a COPY of ``data``.

    Returns ``(copy, raw_duplicate_groups, sidecar)``; ``sidecar`` holds the
    ``ParsedSubmission`` kwargs that both of ``_validate_suite``'s returns share.
    """
    # Piece 1: duplicate_groups is a SUBMISSION-level field, not a TestSuite field
    # (TestSuite is also the LLM response_model, so its schema must stay clean), and
    # TestSuite sets extra="forbid" -- so pop it from a COPY before validating.
    # Popping also keeps the fast path working: without it, a submission carrying the
    # field would ALWAYS fail whole-suite validation and fall into the salvage branch.
    data = dict(data) if isinstance(data, dict) else {}
    sidecar = {
        "duplicate_review_offered": "duplicate_groups" in data,
        # Piece 2: same reasoning, one field further -- pop it from the COPY so a
        # submission carrying it still takes the fast whole-suite validation path.
        "raw_requirement_matches": data.pop("requirement_matches", None),
        # Same reasoning again for the AC boomerang's return field.
        "raw_acceptance_criteria": data.pop("acceptance_criteria", None),
        # Same again for the entailment review's verdicts.
        "raw_grounding_verdicts": data.pop("grounding_verdicts", None),
        # ...and the ambiguity job's verdict, which is what makes its
        # `blocking: True` observable to the server at all.
        "raw_ambiguity_result": data.pop("ambiguity_result", None),
        # ...and the image job's descriptions of the screenshots this server
        # forwarded to the host INSTEAD of describing them itself.
        "raw_image_descriptions": data.pop("image_descriptions", None),
        # Residue R4: the checklist job's return field. Leaving it in place would
        # push EVERY submission carrying it into the salvage branch and silently
        # drop cases. It reaches BOTH return sites through this one dict, so the
        # checklist cannot vanish on the weak-host submissions that need it most.
        "raw_checklist_items": data.pop("checklist_items", None),
    }
    # The two Phase-3a post_merge return fields. RISK_JOB and TEST_PLAN_JOB were
    # DELETED on 2026-08-16 (dead-code deletion P2-H) and nothing reads either
    # field now -- but the POPS stay, because they never depended on the jobs.
    # TestSuite sets extra="forbid", so a submission carrying a stray
    # `risk_scores` or `test_plan_report` key would fail whole-suite validation
    # and drop into the salvage branch, silently losing cases. The values are
    # discarded.
    data.pop("risk_scores", None)
    data.pop("test_plan_report", None)
    return data, data.pop("duplicate_groups", None), sidecar


def _parsed_submission(
    suite: TestSuite, raw_groups, sidecar: dict, drops: dict | None = None
) -> ParsedSubmission:
    """The one ``ParsedSubmission`` build both ``_validate_suite`` paths return.

    ``drops`` carries the salvage path's dropped/salvaged delta; the fast path has none.
    """
    groups, dup_notes = _extract_duplicate_groups(
        raw_groups, {tc.tc_id for tc in suite.test_cases}
    )
    return ParsedSubmission(
        suite=suite,
        duplicate_groups=groups,
        duplicate_notes=dup_notes,
        **sidecar,
        **(drops or {}),
    )


def _salvage_case(c) -> tuple:
    """Validate ONE submitted case: ``(tc, dropped_reason, salvaged_reason)``.

    Exactly one of ``tc`` / ``dropped_reason`` is set; ``salvaged_reason`` is set
    only when the case was kept without its ``test_data`` plan.
    """
    if not isinstance(c, dict):
        return None, "a non-object entry in test_cases", None
    try:
        return TestCase(**c), None, None
    except Exception as exc:
        raw_id = c.get("tc_id")
        tcid = raw_id if isinstance(raw_id, str) else "?"
        # 2026-08-31 (F9): `test_data` is an OPTIONAL per-case provisioning
        # plan, and ONE wrong key inside it ("example" for `example_value`,
        # "seed account" for `seed_account`) used to discard the whole case.
        # Measured: 4 of 9 cases lost, the category then accepted at 5 with
        # a success-shaped reply. TestDataItem's own docstring justifies the
        # strict enum by "the per-category retry regenerates it" -- there is
        # no retry on this path. Salvage the CASE, drop only the plan, and
        # say so.
        tc = None
        if isinstance(c.get("test_data"), list) and c.get("test_data"):
            try:
                tc = TestCase(**{k: v for k, v in c.items() if k != "test_data"})
            except Exception:
                tc = None
        if tc is None:
            # F15 (2026-08-30): the class name alone ("failed validation
            # (ValidationError)") does not say WHICH field or WHICH rule,
            # so a host cannot fix the case without re-deriving the schema.
            # Name up to two field/message pairs; the messages are
            # pydantic's own text, never the rejected VALUE, so nothing
            # untrusted is echoed back.
            return None, f"{tcid}: failed validation ({_validation_detail(exc)})", None
        salvaged_reason = (
            f"{tcid}: kept, but its `test_data` plan was dropped "
            f"({_validation_detail(exc)})"
        )
        return tc, None, salvaged_reason


def _all_dropped_error(dropped: list) -> PrepParseError:
    """The error raised when NO submitted case survived validation."""
    # 2026-08-31: this raised with the COUNT alone and discarded `dropped`
    # -- the per-case field/rule detail F15 had just built. The all-dropped
    # case is where that detail matters MOST: every case failing at once is
    # what ONE systematic schema mistake looks like, so a single reason
    # usually fixes the whole suite. Without it the host was told
    # "(8 dropped)" and had to re-derive the schema by guessing. Measured:
    # 8 cases missing `module` and `type` reported nothing but the number.
    # Collapsed by REASON, not listed per case. Every case failing at once
    # is almost always ONE systematic mistake, and repeating an identical
    # sentence eighty times buries the single fact that fixes the suite --
    # while costing the host's context to read it.
    _by_reason: dict[str, list[str]] = {}
    for _d in dropped:
        _id, _, _reason = str(_d).partition(": ")
        _by_reason.setdefault(_reason or str(_d), []).append(_id)
    _parts: list[str] = []
    for _reason, _ids in list(_by_reason.items())[:_MAX_DROPPED_REASONS]:
        if len(_ids) == 1:
            _parts.append(f"{_ids[0]}: {_reason}")
        else:
            _parts.append(f"{len(_ids)} cases ({_ids[0]}..{_ids[-1]}): {_reason}")
    _why = "; ".join(_parts)
    _more = len(_by_reason) - _MAX_DROPPED_REASONS
    if _more > 0:
        _why += f" (+{_more} more distinct reason(s))"
    return PrepParseError(
        f"no valid test cases in the submitted suite ({len(dropped)} "
        f"dropped). Why: {_why}"
    )


def _validate_cases(cases) -> tuple[list, list, list]:
    """The salvage loop: ``(valid, dropped, salvaged)``, in submission order.

    Keeps every individually-valid case, drops malformed ones and any repeated
    tc_id, and records each drop. Raises PrepParseError when nothing is valid.
    """
    if not isinstance(cases, list):
        raise PrepParseError("submitted suite has no 'test_cases' list")
    valid: list[TestCase] = []
    seen_ids: set[str] = set()
    dropped: list[str] = []
    salvaged: list[str] = []
    for c in cases:
        tc, dropped_reason, salvaged_reason = _salvage_case(c)
        if salvaged_reason:
            salvaged.append(salvaged_reason)
        if tc is None:
            dropped.append(dropped_reason)
            continue
        if tc.tc_id in seen_ids:
            dropped.append(f"{tc.tc_id}: duplicate tc_id")
            continue
        seen_ids.add(tc.tc_id)
        valid.append(tc)
    if not valid:
        raise _all_dropped_error(dropped)
    return valid, dropped, salvaged


def _validate_suite(data: dict) -> ParsedSubmission:
    """Validate a candidate suite dict into a ParsedSubmission.

    Partial-validity policy: try whole-suite validation first (the common case,
    dropped_count == 0). If that fails, SALVAGE -- keep every individually-valid
    TestCase, drop the malformed ones (and any duplicate tc_id), and RECORD each
    drop. Rationale: a weak host almost always emits a few malformed cases;
    all-or-nothing would block the round trip forever, whereas keeping the valid
    ones lets each resubmission make progress. The dropped delta is returned (not
    swallowed) so ops-3d can ALWAYS tell the tester how many cases were discarded,
    regardless of checklist config. Raises PrepParseError only when
    NOTHING valid remains.
    """
    data, raw_groups, sidecar = _pop_sidecar_fields(data)
    try:
        suite = TestSuite(**data)
    except Exception:
        logger.debug("whole-suite validation failed; salvaging valid cases")
    else:
        return _parsed_submission(suite, raw_groups, sidecar)

    valid, dropped, salvaged = _validate_cases(data.get("test_cases"))
    try:
        suite = TestSuite(test_cases=valid)
    except Exception as exc:
        raise PrepParseError(f"could not assemble a valid suite: {exc}") from exc
    drops = {
        "dropped_count": len(dropped),
        "dropped_reasons": dropped[:_MAX_DROPPED_REASONS],
        "salvaged_reasons": salvaged[:_MAX_DROPPED_REASONS],
    }
    return _parsed_submission(suite, raw_groups, sidecar, drops)


# A prep_id is a uuid4 hex (tools/prep_store.py). Anything else is not one --
# and a host-supplied string rendered inside a code span in instructions the
# model then follows is an injection surface: `PID` INJECTED **bold** followed
# by a `## Fake heading` broke out of the span on two separate surfaces.
# Gating the SHAPE here makes every echo site safe by construction; sanitising
# the sites instead leaves the fortieth site one edit away.
_PREP_ID_RE = re.compile(r"\A[0-9a-fA-F]{8,64}\Z")


def safe_prep_id(value: object) -> str:
    """``value`` if it is shaped like a prep_id, else a sanitised stand-in.

    Never raises, and never returns something that can close a code span or
    start a markdown block. An id that fails the shape check is still ECHOED --
    a host that sent a typo needs to see what it sent -- but stripped and
    capped, so it can only ever read as a wrong id, never as instructions.
    """
    try:
        text = str(value or "").strip()
        if _PREP_ID_RE.match(text):
            return text
        flattened = "".join(
            " " if ch in "`\n\r\t" else ch
            for ch in text[:64]
            if ch.isprintable() or ch in "\n\r\t"
        )
        return " ".join(flattened.split())
    except Exception:  # pragma: no cover - defensive
        return ""


def _validation_detail(exc: Exception) -> str:
    """``field `tc_id`: <rule>`` for every failing field, up to
    _MAX_FIELD_ERRORS_PER_CASE (v1.97.0 cursor-hardening item 4b -- a case
    invalid on 5 fields at once used to report 2 and hide the rest behind a
    bare "+N more" count).

    Falls back to the exception class name for anything that is not a pydantic
    ValidationError, which is exactly the previous behaviour. Never raises, and
    never echoes the rejected value.
    """
    try:
        errors = exc.errors()  # type: ignore[attr-defined]
        parts = []
        for err in list(errors)[:_MAX_FIELD_ERRORS_PER_CASE]:
            loc = ".".join(str(p) for p in (err.get("loc") or ())) or "(root)"
            msg = str(err.get("msg") or "invalid")[:120]
            parts.append(f"field `{loc}`: {msg}")
        if parts:
            extra = (
                ""
                if len(errors) <= _MAX_FIELD_ERRORS_PER_CASE
                else f", +{len(errors) - _MAX_FIELD_ERRORS_PER_CASE} more"
            )
            return "; ".join(parts) + extra
    except Exception:
        # The fallback below is the documented behaviour for a non-pydantic
        # error, so this is not a failure path -- but a pydantic error whose
        # .errors() raises IS one, and it used to be indistinguishable.
        logger.debug("host_mode: could not read validation errors", exc_info=True)
    return type(exc).__name__


# F16 (2026-09-02 audit): the deepest `{`/`[` nesting parse_host_suite will look
# at. json.loads is RECURSIVE, so a submission of 40,000 open brackets raised
# RecursionError -- NOT the PrepParseError this function's contract promises --
# and only mcp_handlers' outer generic `except Exception` kept the tool alive.
# A real suite is five levels deep (object -> test_cases -> case -> steps ->
# step), so 200 is two orders of magnitude of headroom: this is a GUARD against
# a hostile shape, not a schema constraint anything legitimate can reach.
_MAX_JSON_DEPTH = 200


def _text_nesting_depth(text: str) -> int:
    """Deepest `{`/`[` nesting in *text*, ignoring brackets inside JSON strings.

    ONE O(n) pass and NO recursion -- which is the whole point: it runs BEFORE
    json.loads precisely so the recursive decoder is never handed input that
    would exhaust the interpreter stack. String-aware, so a case title full of
    brackets cannot be mistaken for nesting.
    """
    depth = 0
    deepest = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > deepest:
                deepest = depth
        elif ch in "]}":
            depth -= 1
    return deepest


def _object_nesting_depth(obj, limit: int) -> int:
    """Deepest container nesting in *obj*, stopping once *limit* is exceeded.

    Iterative for the same reason as :func:`_text_nesting_depth`: the MCP tool
    signatures accept an already-parsed object for ``suite_json``, and handing a
    30,000-deep dict to json.dumps (which the size cap below must do to measure
    it) raised RecursionError before any of this function's own error handling
    could run.
    """
    deepest = 0
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > deepest:
            deepest = depth
            if deepest > limit:
                return deepest
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend((v, depth + 1) for v in node)
    return deepest


def _check_submitted_object(text_or_obj, enforce_size_cap: bool) -> None:
    """Depth and size checks for an already-parsed submitted object."""
    # 2026-08-03 (Fix 1): the MCP tool signatures now accept an OBJECT for
    # suite_json, so this branch is reachable from a real submission for the
    # first time. It MUST honour the same size cap as the string branch
    # below -- otherwise widening the annotation would have silently removed
    # the only bound on submission size, since the cap sits after this early
    # return. Serialising to measure costs the same order of memory as the
    # string path already does.
    if _object_nesting_depth(text_or_obj, _MAX_JSON_DEPTH) > _MAX_JSON_DEPTH:
        raise PrepParseError(
            f"submitted JSON nests deeper than {_MAX_JSON_DEPTH} levels"
        )
    cap = int(getattr(settings, "qa_prep_max_bytes", 0) or 0)
    if cap and enforce_size_cap:
        try:
            size = len(
                json.dumps(text_or_obj, ensure_ascii=False).encode("utf-8", "ignore")
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise PrepParseError(
                f"submitted object is not JSON-serialisable: {exc}"
            ) from exc
        if size > cap:
            raise PrepParseError(f"submitted JSON exceeds the {cap}-byte cap")


def _strip_json_fence(stripped: str) -> str:
    """Remove a leading ```json fence and a trailing ``` fence, if present."""
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        # 2026-09-02 audit F10b. This was re.sub(r"\s*```$", "", stripped).
        # An unbounded whitespace run in front of an ANCHORED literal makes the
        # REFUSAL path quadratic: the engine restarts `\s*` at every position of
        # the run and each restart walks to the end of it. A fenced payload
        # whose JSON carries one long internal whitespace run and no closing
        # fence therefore cost x4 per doubling -- 40,000 spaces measured 2.7 s,
        # on EVERY qa_submit_suite / qa_submit_category.
        #
        # rstrip + endswith is the same transformation in one linear pass. It is
        # EQUIVALENT, not merely close: `$` could only ever match at the end of
        # this string, because the .strip() above has already removed any
        # trailing newline, and `\s*` could only ever consume the whitespace
        # immediately before the closing fence.
        stripped = stripped.rstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    return stripped


def _largest_suite_object(stripped: str) -> dict | None:
    """The largest balanced JSON object carrying ``test_cases``, else None."""
    # A hard ceiling equal to the input length: the single-pass scanner visits
    # each character at most once, so this never trips for legitimate input; it
    # is an explicit invariant guard, while the caller's size cap bounds the input.
    best: dict | None = None
    best_len = -1
    for span in _bounded_json_spans(stripped, budget=len(stripped) + 1):
        try:
            obj = json.loads(span)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if isinstance(obj, dict) and "test_cases" in obj and len(span) > best_len:
            best, best_len = obj, len(span)

    if best is None:
        # Last resort: the whole stripped string as one object.
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError, RecursionError):
            obj = None
        if isinstance(obj, dict) and "test_cases" in obj:
            best = obj
    return best


def parse_host_suite(text_or_obj, *, enforce_size_cap: bool = True) -> ParsedSubmission:
    """Tolerantly extract the host's generated suite into a ParsedSubmission
    (suite + salvage delta).

    Accepts an already-parsed dict, a bare JSON string, a ```json fenced block, or
    prose-wrapped JSON with chatter and multiple candidate objects. Among several
    top-level balanced objects the LARGEST one carrying a ``test_cases`` key wins
    -- a chat model frequently echoes a small schema/example object BEFORE the
    real suite, so "first object" would pick the wrong one.

    *enforce_size_cap* is True for everything a HOST submits. It is False
    for the merged dict the SERVER itself builds out of already-capped
    per-category rows on the Path-A finalize: applying QA_PREP_MAX_BYTES to
    that concatenation refused suites nobody had over-submitted (F7).

    UNTRUSTED-safe: host output re-enters the server, so there is NO code
    execution -- only ``json.loads``, never eval or ast-based literal parsing.
    Work is PROVABLY bounded: the input is rejected above
    ``settings.qa_prep_max_bytes`` before any scan, and extraction uses the LOCAL
    single-pass O(n) ``_bounded_json_spans`` (NOT llm._balanced_json_spans, which
    is O(n^2) on hostile input), so a pathological unbalanced-brace blob is
    rejected fast instead of hanging. Raises PrepParseError so ops-3d can turn a
    bad submission into a tester-readable message.
    """
    if isinstance(text_or_obj, dict):
        _check_submitted_object(text_or_obj, enforce_size_cap)
        return _validate_suite(text_or_obj)
    if not isinstance(text_or_obj, str):
        raise PrepParseError(
            "host suite must be a JSON string or object, got "
            f"{type(text_or_obj).__name__}"
        )

    _check_text_size_cap(text_or_obj, enforce_size_cap)
    return _parse_suite_text(text_or_obj)


def _check_text_size_cap(text: str, enforce_size_cap: bool) -> None:
    """Raise PrepParseError when ``text`` exceeds ``qa_prep_max_bytes``."""
    max_bytes = int(getattr(settings, "qa_prep_max_bytes", 0) or 0)
    if (
        enforce_size_cap
        and max_bytes
        and len(text.encode("utf-8", "ignore")) > max_bytes
    ):
        raise PrepParseError(f"submitted JSON exceeds the {max_bytes}-byte cap")


def _parse_suite_text(text: str) -> ParsedSubmission:
    """Fence-strip, depth-check and validate the largest suite object in ``text``."""
    stripped = _strip_json_fence(text.strip())

    if _text_nesting_depth(stripped) > _MAX_JSON_DEPTH:
        raise PrepParseError(
            f"submitted JSON nests deeper than {_MAX_JSON_DEPTH} levels"
        )

    best = _largest_suite_object(stripped)

    if best is None:
        raise PrepParseError(
            "no JSON object with a 'test_cases' key found in the submitted text"
        )
    return _validate_suite(best)
