"""Moved from handlers (refactor P2): generate_prepare."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agents.host_prep_serde import PrepSerdeError
from agents.host_submission import safe_prep_id
from config.settings import settings
from handlers import _common, _elicit, capture, export, generate, image_gate
from tools import dispatch_guard, handover_cost
from tools.tool_briefs import FLOW_BRIEF

logger = logging.getLogger("tools.mcp_handlers")


# Probe re-sent ids, then the duplicate-prep guard. Nothing in here moves
# a screen off the carry-forward shelf except _carry_forward_or_refuse's
# own revive (see "Deferred REVIVE" below).
#
# The open-prep clarify above stays dismissible by `proceed_anyway=true`;
# the IMAGE-loss refusal is not, because it has its own ack.
# F22: SHAPE before SCREENS. A board / dashboard / site-root URL on a Jira
# host names no ticket, so there are no ticket screens to ask about -- and
# _jira_page_without_issue_note fetches nothing, so this costs one string
# test. Placed after the open-prep guard (cheaper still, and it can
# end the call itself) and deliberately BEFORE the deferred revive
# below: a round that refuses must not first move a screen off the
# carry-forward shelf, which is the same rule the revive's own comment
# states for the clarify above it.
#
# Deferred REVIVE (review M3). NOTHING above this point may move a
# screen off the carry-forward shelf: the clarify above is a
# dismissible RETRY, and consuming the shelf on a round that returned a
# clarify destroyed the very disclosure the retry needed. Everything
# above only PROBES; the mutation happens here, once the call is certain
# to proceed to the gate and the fetch.
#
# ONE deliberate exception, above: _carry_forward_or_refuse revives
# before ITS refusal. That is correct -- a recovered screen must not sit
# orphaned on the shelf while the reply says it was recovered -- and it
# loses nothing, because that decision is driven by the prior PREP ROW,
# which is still in the store and re-derives the identical note on the
# retry. The re-sent-id path has no such source of truth.
#
# --- Jira image gate, BEAT 1 (QA_IMAGE_GATE_ENABLED) ------------------- #
# Placed AFTER the duplicate-prep guard (an already-open prep is the
# cheaper, more urgent signal) and BEFORE the fetch
#
# 2026-08-31 (F3): that ordering is still right, but it MASKED this gate
# -- a tester re-running a ticket saw only the duplicate warning and never
# the screens question, which then cost a further round trip after the
# proceed_anyway retry. Both duplicate clarifies now append
# _pending_image_gate_hint, whose condition MIRRORS the `if` below. Change
# one and you must change the other, or the hint promises a question that
# never comes (or stays silent about one that does). -- which is the whole
# point: the "I cannot read Jira images" disclosure used to be appended to a
# payload the host had ALREADY been told to generate from.
#
# REACHABILITY: handle_generate_test_cases re-routes into this handler in
# host mode, so both beats fire on qa_generate_test_cases too -- which is
# why that handler and that tool forward these same four arguments.
#
# PEEK, never pop. A Jira source's first prepare returns the fetch DIRECTIVE,
# so consuming the tray here would destroy the screens before the ticket
# arrived. _drop_captures runs only once a payload actually ships.
#
# The SECOND unconditional server-side call on this path (the first
# being the ambiguity classifier above): rtm.generate_acs, which fires
# whenever the ticket carried no parsed ACs and has no off switch of
# its own. Decided BEFORE _prepare_generation because AC synthesis is
# prepare-side -- its output feeds rtm_hint and the RTM -- so it
# cannot be deferred to submit; what is deferred is the RESULT.
# Retained because it still decides whether the raw bytes are forwarded
# to the host as MCP image content (host_images / _img_job below). The
# two server-side vision calls it used to suppress -- the Jira
# ticket-image description and ui_extractor's Tier-3 fallback -- were
# DELETED on 2026-08-16 (P2-F1), so there is nothing left to suppress.
#
# Whether the AC job was actually SHIPPED, which is narrower than the
# flag: a ticket that carried real acceptance criteria needs no job at
# all (source_acs is non-empty and nothing was synthesized either way).
def _host_generation_mode() -> bool:
    """True when generation runs in host mode.

    Evening-ops repair: `llm` is NOT imported at module scope in this file,
    so it is imported here; a failure still surfaces in the caller's except
    as "Preparation failed"."""
    import llm

    return llm.resolve_generation_mode() == "host"


def _revive_shelved(capture_ids: list | None) -> None:
    """Revive any shelved captures; the caller peeks the tray after this."""
    if capture_ids:
        capture._revive_captures(capture_ids)


def _beat1_clarify(menu: str, rendered_missing: str) -> str:
    """The BEAT 1 menu, with the rendered missing-captures note appended when due.

    The caller renders it, so `_render_missing` stays paired with its own
    `_peek_captures` (tests/test_render_missing_pairing.py)."""
    if rendered_missing:
        return (
            menu
            + generate._BEAT1_MISSING_HEAD
            + rendered_missing
            + generate._BEAT1_MISSING_TAIL
        )
    return menu


async def _plan_nudge_if_due(
    plan,
    image_gate_ack: bool,
    gctx: generate._GateCtx,
    cap_images: list,
    cap_missing: list,
) -> str | None:
    """The plan nudge, or None when there is no plan or the gate is acked."""
    if plan and not image_gate_ack:
        return await generate._image_plan_nudge(plan, gctx, cap_images, cap_missing)
    return None


async def handle_prepare_test_cases(
    feature_or_url: str,
    *,
    attached_images: list | None = None,
    proceed_anyway: bool = False,
    source_plan: str = "",
    attached_image_count: int = 0,
    capture_ids: list | None = None,
    image_gate_ack: bool = False,
    image_carry_ack: bool = False,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
    jira_content_json: str = "",
    stage_token: str = "",
) -> generate.PreparePayloadResult:
    """FRONT half of host-mode generation. Grounds + gates the inputs exactly
    like the server path, runs _prepare_generation, serializes the result into
    the prep store, and returns the grounded payload the tester's own chat model
    runs the fan-out against. Never raises."""
    dispatch_guard.require_dispatched("qa_prepare_test_cases")
    args = _PrepArgs(
        text=(feature_or_url or "").strip(),
        attached_images=attached_images,
        proceed_anyway=proceed_anyway,
        source_plan=source_plan,
        attached_image_count=attached_image_count,
        capture_ids=capture_ids,
        image_gate_ack=image_gate_ack,
        image_carry_ack=image_carry_ack,
        choose=choose,
        ask_text=ask_text,
        progress=progress,
        jira_content_json=jira_content_json,
    )
    front = await _prepare_front(args, stage_token)
    if isinstance(front, generate.PreparePayloadResult):
        return front
    return await _prepare_generate_guarded(front)


async def _prepare_generate_guarded(front) -> generate.PreparePayloadResult:
    """_prepare_generate with its failures turned into a clarify reply."""
    try:
        return await _prepare_generate(front)
    except PrepSerdeError as exc:
        logger.warning("host-mode prepare serialization failed", exc_info=True)
        return generate.PreparePayloadResult(
            clarify=(
                f"⚠️ Could not prepare host-mode generation: {exc}"
                + capture._capture_retry_hint(front.args.capture_ids)
            )
        )
    except Exception as exc:
        logger.exception("handle_prepare_test_cases failed")
        _common._capture_error(exc, "qa_prepare_test_cases")
        return generate.PreparePayloadResult(
            clarify=(
                f"⚠️ Preparation failed: {exc}"
                + capture._capture_retry_hint(front.args.capture_ids)
            )
        )


@dataclass
class _PrepArgs:
    """The arguments of one handle_prepare_test_cases call, as one value.

    ``capture_ids`` and ``jira_content_json`` are replaced as the front half
    resolves them (pre-flight carry, staged payload)."""

    text: str
    attached_images: list | None
    proceed_anyway: bool
    source_plan: str
    attached_image_count: int
    capture_ids: list | None
    image_gate_ack: bool
    image_carry_ack: bool
    choose: _common.ChooseCb
    ask_text: _elicit.AskCb
    progress: _common.ProgressCb
    jira_content_json: str


@dataclass
class _PrepFront:
    """What the refusals and image gate beats resolved, for the generation half."""

    args: _PrepArgs
    carry: Any
    intake: Any
    gctx: generate._GateCtx
    plan: Any
    cap_images: Any
    cap_labels: Any
    cap_missing: Any
    missing_rendered: str = ""


async def _prepare_early_refusal(
    a: _PrepArgs, stage_token: str
) -> tuple[generate.PreparePayloadResult | None, Any]:
    """The input, pre-flight, open-prep and page-shape refusals, in order.

    Returns (refusal or None, the pre-flight carry). Resolves
    ``a.jira_content_json`` and ``a.capture_ids`` in place."""
    refusal, a.jira_content_json = generate._prepare_input_refusal(
        a.text, a.jira_content_json, stage_token
    )
    if refusal:
        return generate.PreparePayloadResult(clarify=refusal), None
    refusal, carry, prep = await generate._prepare_preflight(
        a.text,
        a.capture_ids,
        a.attached_images,
        a.attached_image_count,
        a.image_carry_ack,
    )
    if refusal:
        return generate.PreparePayloadResult(clarify=refusal), carry
    a.capture_ids = carry.capture_ids
    if prep and not a.proceed_anyway:
        hint = generate._pending_image_gate_hint(
            a.text,
            a.source_plan,
            a.jira_content_json,
            a.attached_images,
            a.attached_image_count,
            a.capture_ids,
        )
        return generate.PreparePayloadResult(
            clarify=generate._open_prep_refusal(prep) + hint
        ), carry
    refusal = generate._jira_page_without_issue_note(a.text)
    if refusal:
        return generate.PreparePayloadResult(clarify=refusal), carry
    return None, carry


async def _prepare_front(
    a: _PrepArgs, stage_token: str
) -> generate.PreparePayloadResult | _PrepFront:
    """Everything before grounding: refusals, capture intake and both pre-gates."""
    refusal, carry = await _prepare_early_refusal(a, stage_token)
    if refusal is not None:
        return refusal
    _revive_shelved(a.capture_ids)
    plan = image_gate._normalize_source_plan(a.source_plan)
    sent = a.capture_ids
    cap_images, cap_labels, cap_missing = capture._peek_captures(sent)
    missing_rendered = capture._render_missing(cap_missing, sent) if cap_missing else ""
    intake = generate._image_intake(
        a.attached_images, cap_images, a.attached_image_count
    )
    a.attached_images = intake.attached_images
    gctx = generate._GateCtx(
        a.text, a.jira_content_json, a.attached_images, intake.attested, intake.captured
    )
    b1 = await generate._image_gate_beat1(
        gctx, plan, a.source_plan, a.choose, a.ask_text
    )
    if b1.menu is not None:
        return generate.PreparePayloadResult(
            clarify=_beat1_clarify(b1.menu, missing_rendered)
        )
    nudge = await _plan_nudge_if_due(
        b1.plan, a.image_gate_ack, gctx, cap_images, cap_missing
    )
    if nudge is not None:
        return generate.PreparePayloadResult(clarify=nudge)
    return _PrepFront(
        a,
        carry,
        intake,
        gctx,
        b1.plan,
        cap_images,
        cap_labels,
        cap_missing,
        missing_rendered,
    )


@dataclass(frozen=True)
class _PrepParts:
    """The locals of ``_prepare_generate`` that ``_build_prep_run`` reads."""

    grounded: Any
    prepared: Any
    prospective: Any
    skip: Any
    notes: Any
    img: Any
    host_mode_on: bool
    checklist_job: Any


def _build_prep_run(f: _PrepFront, parts: _PrepParts) -> generate._PrepRun:
    """Assemble the ``_PrepRun`` the finishing half reads."""
    a = f.args
    prepared = parts.prepared
    return generate._PrepRun(
        text=a.text,
        grounded=parts.grounded,
        prepared=prepared,
        capture_ids=a.capture_ids,
        cap_labels=f.cap_labels,
        image_carry_ack=a.image_carry_ack,
        carry=f.carry,
        intake=f.intake,
        skip=parts.skip,
        notes=parts.notes,
        img=parts.img,
        host_amb=parts.host_mode_on,
        ac_job=bool(
            parts.host_mode_on and not prepared.source_acs and not prepared.acs
        ),
        checklist_job=parts.checklist_job,
        rp_narrowed=generate._rule_pack_narrowed(prepared, parts.checklist_job),
        prospective_images=parts.prospective,
        captured_shipped=_shipped_capture_count(parts.prospective, f.cap_images),
    )


async def _prepare_generate(f: _PrepFront) -> generate.PreparePayloadResult:
    """Grounding, the second image beat and generation, then the finished reply."""
    a = f.args
    host_mode_on = _host_generation_mode()
    grounded = await generate._ground_and_gate(
        a.text,
        attached_images=a.attached_images,
        proceed_anyway=a.proceed_anyway,
        choose=a.choose,
        ask_text=a.ask_text,
        progress=a.progress,
        jira_content_json=a.jira_content_json,
    )
    if isinstance(grounded, str):
        return generate.PreparePayloadResult(clarify=grounded)
    skip = await generate._image_gate_beat2(
        grounded, f.gctx, f.plan, a.image_gate_ack, a.choose
    )
    if isinstance(skip, generate.PreparePayloadResult):
        return skip
    notes = await generate._ticket_recency_notes(a.text, grounded)
    host_images = generate._host_forward_images(
        grounded, a.attached_images, host_mode_on
    )
    img = generate._image_job_flags(host_mode_on, host_images, f.intake.attested)
    checklist_job = generate._checklist_job_on()
    prepared = await generate._run_prepare_generation(
        a.text, grounded, a.attached_images, a.progress, a.capture_ids
    )
    if isinstance(prepared, generate.PreparePayloadResult):
        return prepared
    prospective = generate._prospective_images(img.job, host_images, grounded)
    parts = _PrepParts(
        grounded,
        prepared,
        prospective,
        skip,
        notes,
        img,
        host_mode_on,
        checklist_job,
    )
    run = _build_prep_run(f, parts)
    return await generate._finish_prepare(run, lambda: _missing_note_of(f))


def _missing_note_of(f: _PrepFront) -> str:
    """The note on captures that could not be read back."""
    return generate._missing_captures_note(len(f.cap_missing), f.missing_rendered)


def _next_call_block(payload: object, prep_id: str) -> list:
    """The route + volume contract, stated ABOVE the payload body.

    2026-08-12. A prep result is tens of KB and clients preview the first ~2 KB,
    so anything stated only inside the JSON is, in practice, unread: one session
    never called ``qa_get_category_job`` at all, submitted 27 cases against a
    64-case contract, was refused three times, and shipped a hand-written CSV.
    The floor and the fan-out route are the two facts a host must have BEFORE it
    starts generating, so they go in the header.

    ``category_name="all"`` is REQUIRED, not optional: handle_get_category_job
    refuses an empty name outright, and the batch form is gated on
    ``category_name.lower() in ("all", "*")``. Advertising the call without it
    would recreate the dead-end read this block exists to prevent.

    The batch packet builder is flag-independent, and handle_submit_category
    serves an incremental host as well, so this route is valid on a default
    install where the payload itself carries no ``orchestration`` block.

    Pure. Returns [] whenever this payload carries no per-category floor, so a
    payload without a volume contract is byte-identical to today's output.
    """
    try:
        # Sanitise ONCE, here, rather than at each interpolation below: the id
        # is host-supplied and lands inside a code span in the first
        # instruction a host reads.
        prep_id = safe_prep_id(prep_id)
        if not isinstance(payload, dict):
            return []
        cats = payload.get("categories")
        if not isinstance(cats, list) or not cats:
            return []
        floors = _category_floors(cats)
        if not floors or len(floors) != len(cats):
            return []
        return _next_call_lines(prep_id, floors)
    except Exception:  # pragma: no cover - a header must never break a prepare
        logger.debug("next-call block failed", exc_info=True)
        return []


def _category_floors(cats: list) -> list[int]:
    """The positive ``min_cases`` of each dict category (others are skipped)."""
    floors = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        try:
            n = int(c.get("min_cases") or 0)
        except (TypeError, ValueError, OverflowError):
            n = 0
        if n > 0:
            floors.append(n)
    return floors


def _next_call_lines(prep_id: str, floors: list[int]) -> list:
    """The NEXT CALL lines for one per-category floor list (one floor per category)."""
    # The contract is per-category, so the suite total is their SUM. Using
    # min(floors) x len(cats) understated a non-uniform contract -- floors
    # of 4 and 12 rendered "8 cases" when the payload asked for 16 -- and
    # the same wrong number was repeated as the refusal threshold.
    floor = min(floors)
    uniform = len(set(floors)) == 1
    total = sum(floors)
    per_category = (
        f"{floor} case(s) per category \u00d7 {len(floors)} categories = "
        f"**{total} cases**"
        if uniform
        else f"**{total} cases**, per the per-category floors in the payload "
        f"(from {floor} to {max(floors)})"
    )
    return [
        "",
        f"**NEXT CALL -- do not skip.** This suite is expected to reach "
        f"{per_category}.",
        "",
        f"1. Call `qa_get_category_job` with prep_id `{prep_id}` and "
        '`category_name="all"` -- ONE call returns every category packet.',
        (
            f"2. Generate each category to at least {floor} cases."
            if uniform
            else "2. Generate each category to at least its own "
            "`min_cases` -- they differ."
        ),
        # The route named here is the one the host will take: this block is
        # the first thing it reads, by design. Which route is correct is
        # NOT this function's decision -- host_mode owns it, and gets it
        # right for both seam states -- so ask, never hardcode.
        "3. Call `qa_submit_category` once per category, then "
        + export._finalize_route_phrase()
        + ".",
        "",
        "Merging everything into a single `qa_submit_suite` call is also "
        f"allowed, but a suite materially below {total} cases is refused "
        "-- a partly-generated suite is the usual cause. If the tester has "
        "seen the shortfall and wants the smaller suite anyway, that "
        "refusal can be acknowledged; do not decide it yourself.",
    ]


def render_prepare_payload(result: generate.PreparePayloadResult) -> str:
    """Render a PreparePayloadResult as a single markdown/JSON text block for a
    string-only caller (a legacy MCP client, or the host-mode branch of
    qa_generate_test_cases in ops-3d-3). ops-3d-2 renders the richer
    multi-block / image form on capable clients. NEVER truncates silently: the
    whole payload is embedded and any disclosure rides in ``notice``.
    """
    if result.clarify:
        return result.clarify
    if not result.payload:
        return "⚠️ Nothing to prepare."
    body = json.dumps(result.payload, ensure_ascii=False, indent=2)
    parts = [
        "## Host-mode generation -- run this in your own chat, then submit it back",
        "",
        f"**prep_id:** `{result.prep_id}` (pass this to `qa_submit_suite`).",
        *_next_call_block(result.payload, str(result.prep_id or "")),
        "",
        FLOW_BRIEF.strip(),
        "",
        "Generate the suite from the payload below, then call `qa_submit_suite` "
        "with this prep_id and your merged JSON:",
        "",
        "```json",
        body,
        "```",
    ]
    # What this handover will cost the TESTER's own chat model. Computed from
    # the payload being returned in THIS call -- there is no meter and nothing
    # accumulates, which is the shape difference from the deleted
    # tools/token_meter.py (see tools/handover_cost.py's module docstring).
    # Image figures come from `_kept_image_bytes`, i.e. POST-budget: the raw
    # attachment list is not what ships.
    _img_n, _img_b = _kept_image_bytes(result.images)
    parts += [
        "",
        handover_cost.handover_line(
            result.payload, image_count=_img_n, image_bytes=_img_b
        ),
    ]
    if result.notice:
        parts += ["", result.notice]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Host-mode PREPARE result -> MCP content assembly (ops-3d-2, items 5 & 6)
#
# Item 5 (tool-result SIZE): a fastmcp tool may return a LIST of content blocks.
# The grounded payload is emitted as ONE text block when it fits the per-block
# byte budget (byte-identical to render_prepare_payload -- the legacy/string-only
# form), and SPLIT across labeled, self-contained blocks only when it does not.
# ops-3c made the payload chunk-friendly (flat top-level keys, self-contained
# category entries), so a field is NEVER cut mid-value and TEXT IS NEVER
# TRUNCATED. The only thing ever dropped for size is an IMAGE, and every drop is
# named (item 6).
#
# Thresholds (stated, with rationale):
#  * _MAX_TEXT_BLOCK_BYTES = 256 KiB -- the soft per-text-block budget. A typical
#    grounded payload (capped ticket/AC/parent/spec/openapi text + 8 category
#    instructions + the TestSuite schema) fits well under this, so the common
#    case stays a single legacy-identical block; only an unusually large context
#    triggers the split. It is a SPLIT trigger, not a truncation cap -- an
#    oversized single field is still emitted whole in its own block.
#  * _MAX_IMAGE_RESULT_BYTES = 4 MiB -- total RAW image bytes forwarded per
#    result. base64 inflates ~33%, so 4 MiB raw is ~5.3 MiB on the wire; this
#    keeps the whole tool result within sane host limits while allowing a few
#    screenshots. Images are ALSO bounded upstream by settings.jira_max_images
#    (count) and settings.jira_max_image_bytes (per image at download).
# --------------------------------------------------------------------------- #

_MAX_TEXT_BLOCK_BYTES = 262_144
_MAX_IMAGE_RESULT_BYTES = 4_194_304


def _budget_keep_flags(images: list) -> tuple[list[bool], list[str]]:
    """POSITIONAL verdicts for the per-result image budget: ``(flags, dropped)``.

    Split out of _select_prepare_images (2026-08-09, review M1) so the PREPARE
    stamp can ask "how many of these will actually ship?" using the very code
    that later decides it, instead of a second copy that drifts. ``flags[i]`` is
    True when ``images[i]`` rides on the reply; ``dropped`` names the images
    refused for size or for the jira_max_images count cap, in order. Pure."""
    flags: list[bool] = []
    dropped: list[str] = []
    used = 0
    kept_n = 0
    max_n = max(0, int(getattr(settings, "jira_max_images", 0) or 0))
    for img in images or []:
        if not isinstance(img, dict):
            flags.append(False)
            continue
        data = img.get("data")
        name = img.get("filename") or "attachment"
        if not isinstance(data, (bytes, bytearray)) or not data:
            flags.append(False)
            continue
        if kept_n >= max_n or used + len(data) > _MAX_IMAGE_RESULT_BYTES:
            dropped.append(name)
            flags.append(False)
            continue
        used += len(data)
        kept_n += 1
        flags.append(True)
    return flags, dropped


def _kept_image_bytes(images: list) -> tuple[int, int]:
    """``(count, raw_bytes)`` for the images that will ACTUALLY ride on this
    reply.

    Decided by ``_budget_keep_flags`` -- the very function that later drops
    them -- so the handover disclosure cannot bill the tester for an
    attachment the per-result byte budget or the ``jira_max_images`` count cap
    refuses. A second copy of those rules would drift; this has no copy at all.
    Never raises: a count may not break a prepare, and 0 under-reports rather
    than inventing bytes.
    """
    try:
        imgs = list(images or [])
        flags, _dropped = _budget_keep_flags(imgs)
        total = 0
        kept = 0
        for img, ok in zip(imgs, flags):
            if not ok:
                continue
            kept += 1
            data = img.get("data")
            if isinstance(data, (bytes, bytearray)):
                total += len(data)
        return (kept, total)
    except Exception:  # pragma: no cover - a count never breaks a prepare
        logger.debug("counting the images that ship failed", exc_info=True)
        return (0, 0)


def _shipped_capture_count(images: list, captured: list) -> int:
    """How many DEVICE-captured screens actually ride on this reply.

    2026-08-09 (review M1). The prepare envelope used to stamp the count read off
    the capture tray, but _select_prepare_images caps the reply at
    jira_max_images (default 3) -- so a 5-screen capture stamped 5, shipped 3,
    and the submit-side _attested_image_gap_note reported a shortfall that no
    action could ever clear. Matched by OBJECT IDENTITY, because the captured
    dicts are the same objects that were merged into the forwarded list, so no
    filename collision can miscount. Never raises: on any internal error it falls
    back to the pre-fix count, i.e. today's behaviour."""
    try:
        wanted = {id(i) for i in (captured or []) if isinstance(i, dict)}
        if not wanted:
            return 0
        imgs = list(images or [])
        flags, _dropped = _budget_keep_flags(imgs)
        return sum(1 for img, ok in zip(imgs, flags) if ok and id(img) in wanted)
    except Exception:  # pragma: no cover - a count never breaks a prepare
        logger.debug("counting shipped captured screens failed", exc_info=True)
        return len([i for i in (captured or []) if i])


def _select_prepare_images(
    result: generate.PreparePayloadResult,
) -> tuple[list[dict], str]:
    """Pick the ticket screenshots that fit the per-result byte budget and the
    jira_max_images count cap. Returns (kept, disclosure): kept is a list of
    {filename, mime, data} dicts; disclosure NAMES every image dropped for size
    (never silent). Pure -- no fastmcp import."""
    images = list(result.images or [])
    flags, dropped = _budget_keep_flags(images)
    kept: list[dict] = [
        {
            "filename": img.get("filename") or "attachment",
            "mime": (img.get("mime") or "image/png"),
            "data": bytes(img.get("data")),
        }
        for img, ok in zip(images, flags)
        if ok
    ]
    disclosure = ""
    if dropped:
        disclosure = (
            "> ℹ️  "
            f"{len(dropped)} ticket screenshot(s) were NOT attached as image "
            "content to keep the reply within the size budget "
            f"({', '.join(dropped)}). Their text description, if any, is in the "
            "payload above."
        )
    return kept, disclosure


def _text_only_image_skip_note(meta: dict) -> str:
    """ "> ..." disclosure when the tester's own text-only source_plan='jira'
    pick skipped beat 2 while the ticket itself referenced screens (v1.97.0
    cursor-hardening item 2). "" when the prep's meta carries no skip stamp
    (every prep before this change, and every prep where the pick did not
    skip anything). Never raises."""
    try:
        _n = image_gate._clamped_count(
            (meta or {}).get("text_only_image_skip_count"), lo=0, hi=99
        )
        if _n <= 0:
            return ""
        _names = [
            str(x) for x in ((meta or {}).get("text_only_image_skip_names") or [])
        ][:8]
        _named = (
            (" Names: " + ", ".join(f"`{n}`" for n in _names) + ".") if _names else ""
        )
        return (
            f"\n> \u2139\ufe0f {_n} screen(s) referenced by this ticket were not "
            f"read -- the tester chose ticket text only.{_named}"
        )
    except Exception:
        logger.debug("_text_only_image_skip_note failed", exc_info=True)
        return ""


def _attested_image_gap_note(attested, result, *, captured=0) -> str:
    """Disclose IMAGE evidence that never came back -- on EITHER intake channel.

    2026-08-09 (multi-image intake): "some came back" is not the same fact as
    "all of them came back". A NUMERIC shortfall -- fewer readable
    `image_descriptions` than this prep's own stamps promised -- now gets its
    own, milder note, so a 3-image prep that returned 1 description no longer
    reads as a clean submit. It is a NOTE only: nothing here refuses, and the
    QA_HOST_IMAGE_REQUIRE_RELEVANT machinery is deliberately untouched.

    The two channels are deliberately kept DISTINGUISHABLE in the text, because
    they carry different evidence:

      * ``attached_image_count`` is host-ATTESTED. No bytes ever reached this
        server, so IMAGE_JOB's returned ``image_descriptions`` is the only
        verification tell there is.
      * ``captured`` counts DEVICE screens this server itself HANDED to the chat
        client (`qa_capture_screens`). The bytes are real here; what is missing
        is evidence the host's model read them. Saying "you told me" about those
        would be an over-claim in the other direction. It is deliberately NOT
        phrased as "the host received N": the per-result byte/count budget in
        _select_prepare_images runs after the stamp and NAMES anything it drops,
        so this note points at that disclosure instead of contradicting it.

    Until 2026-08-09 this bailed on ``attested <= 0``, which made the strong
    warning STRUCTURALLY UNREACHABLE on a capture-only run -- the stamp read
    ``attached_image_count: 0`` while ``host_image_job: true`` (the TICKET-5646
    defect). The two counts are PER-CHANNEL and may describe the same physical
    screens (a `jira_both` plan), which the both-channel wording says out loud
    rather than implying 2N distinct screens. Silent when nothing was attested
    OR captured and silent when descriptions did come back, so a normal submit is
    byte-identical, and ``captured`` defaults to 0 so an OLD prep envelope
    without the new stamp behaves exactly as before. Both counts are coerced
    INSIDE this try, so a garbage stamp cannot raise at the call site. Never
    raises."""
    try:
        return _image_gap_note_text(attested, result, captured)
    except Exception:  # pragma: no cover - a disclosure never breaks a submit
        logger.debug("_attested_image_gap_note failed", exc_info=True)
        return ""


def _image_gap_note_text(attested, result, captured) -> str:
    """The gap note body; may raise, its only caller swallows."""
    _att = image_gate._clamped_count(attested, lo=0, hi=99)
    _cap = image_gate._clamped_count(captured, lo=0, hi=99)
    if (_att + _cap) <= 0 or result is None:
        return ""
    _readable = (
        len(list(getattr(result, "images", []) or []))
        if getattr(result, "ran", False)
        else 0
    )
    # NUMERIC reconciliation (2026-08-09). Both sides are UNTRUSTED -- a
    # host-authored list and a prep stamp -- so the counts are clamped
    # before any wording sees them. _promised is at least 1 because the
    # guard above already established that something was attested or
    # captured.
    _promised = image_gate._clamped_count(_att + _cap, lo=1, hi=99, default=1)
    if _readable >= _promised:
        return ""
    _what, _budget = _image_gap_channels(_att, _cap)
    return _image_gap_message(_what, _budget, _readable, _promised)


def _image_gap_channels(att: int, cap: int) -> tuple[str, str]:
    """(what the image evidence was, the budget sentence) for the gap note."""
    budget = " Any captured screen dropped for size is named in the prepare reply."
    if att and cap:
        what = (
            f"You told me {att} screenshot(s) were attached to the chat, and "
            f"this server captured {cap} device screen(s) and handed them to "
            "your chat client as image content (up to the reply size budget; "
            "the two counts are per-CHANNEL and may describe the same physical "
            "screens)"
        )
    elif cap:
        what = (
            f"This server captured {cap} device screen(s) with "
            "`qa_capture_screens` and handed them to your chat client as image "
            "content (up to the reply size budget)"
        )
    else:
        what = f"You told me {att} screenshot(s) were attached to the chat"
        budget = ""
    return what, budget


def _image_gap_message(what: str, budget: str, readable: int, promised: int) -> str:
    """The warning text: a subset came back, or no readable description did."""
    if readable:
        return (
            f"> ⚠️  {what}, but readable `image_descriptions` came back "
            f"for only {readable} of {promised} -- so this suite may be "
            "grounded on a SUBSET of the screens, and this server made no "
            "vision call of its own. If the screens with no description "
            "carry requirements, supply them again and prepare again."
            f"{budget}\n\n"
        )
    return (
        f"> ⚠️  {what}, but the submission came back with NO readable "
        "`image_descriptions` -- so there is NO evidence any image was "
        "actually read, and this server made no vision call of its own. "
        "Treat this suite as generated from the ticket TEXT: if those "
        "screens carry requirements, attach them again or re-capture them "
        f"with `qa_capture_screens`, then prepare again.{budget}\n\n"
    )


# ATTACHED keys. This is a WHITELIST, so anything not named here is silently
# dropped on the oversized-payload path -- which for a BLOCKING step-zero job
# (the ambiguity preflight) would mean the safety check vanishes on exactly the
# biggest tickets. None-valued keys are filtered out, so a payload without them
# renders byte-identically to before.
_SPLIT_META_KEYS = (
    "version",
    "task",
    "prep_id",
    "untrusted_data_notice",
    "instructions",
    "image_context",
    "jobs_to_run",
    "ambiguity_job",
    "acceptance_criteria_job",
    "orchestration",
    "jobs",
)


def _split_prepare_text_blocks(payload: dict, prep_id: str, notice: str) -> list[str]:
    """Split a too-large payload into labeled, self-contained text blocks WITHOUT
    truncating any field. The host reassembles ONE JSON object from the fragments.
    """
    cats = payload.get("categories") or []
    meta = {k: payload.get(k) for k in _SPLIT_META_KEYS if payload.get(k) is not None}
    header = [
        f"## Host-mode generation payload -- delivered across {4 + len(cats)} labeled parts",
        "",
        f"**prep_id:** `{prep_id}` (pass this to `qa_submit_suite`).",
        *_next_call_block(payload, str(prep_id or "")),
        "",
        FLOW_BRIEF.strip(),
        "",
        "This grounded payload exceeded the single-block size budget, so it is "
        "split across the labeled blocks below WITHOUT truncation. Reassemble ONE "
        "JSON object with top-level keys `system_prompt`, `user_context`, "
        "`untrusted_data_notice`, `categories` (an array -- one entry per "
        "`categories[i]` block) and `response_schema`; generate the suite; then "
        "call `qa_submit_suite` with this prep_id and your merged JSON.",
        "",
        "### meta",
        "```json",
        json.dumps(meta, ensure_ascii=False, indent=2),
        "```",
    ]
    if notice:
        header += ["", notice]
    return ["\n".join(header), *_split_body_blocks(payload, cats)]


def _split_body_blocks(payload: dict, cats: list) -> list[str]:
    """The system_prompt, user_context, response_schema and per-category blocks."""
    blocks = []
    blocks.append(
        "### system_prompt\n```\n" + str(payload.get("system_prompt") or "") + "\n```"
    )
    blocks.append(
        "### user_context\n```\n" + str(payload.get("user_context") or "") + "\n```"
    )
    blocks.append(
        "### response_schema\n```json\n"
        + json.dumps(payload.get("response_schema") or {}, ensure_ascii=False, indent=2)
        + "\n```"
    )
    for i, cat in enumerate(cats):
        name = cat.get("name", "?") if isinstance(cat, dict) else "?"
        blocks.append(
            f"### categories[{i}]: {name}\n```json\n"
            + json.dumps(cat, ensure_ascii=False, indent=2)
            + "\n```"
        )
    return blocks


def assemble_prepare_payload(
    result: generate.PreparePayloadResult,
) -> tuple[list[str], list[dict]]:
    """Turn a PreparePayloadResult into (text_blocks, image_specs) for the MCP
    tool layer. text_blocks is ONE legacy-identical block when the payload fits
    _MAX_TEXT_BLOCK_BYTES, else the split set (item 5); image_specs is the
    byte-budget-capped {filename, mime, data} list (item 6). Any image dropped for
    size is disclosed in an extra text block. Pure -- imports no fastmcp/mcp, so
    it is fully unit-testable; the tool layer converts the specs to ImageContent.
    """
    if result.clarify or not result.payload:
        return ([render_prepare_payload(result)], [])
    image_specs, disclosure = _select_prepare_images(result)
    single = render_prepare_payload(result)
    if len(single.encode("utf-8")) <= _MAX_TEXT_BLOCK_BYTES:
        text_blocks = [single]
    else:
        text_blocks = _split_prepare_text_blocks(
            result.payload, result.prep_id, result.notice
        )
        # The split path does NOT go through render_prepare_payload, so the
        # handover-cost block has to be attached here as well -- otherwise the
        # LARGEST payloads, the ones actually worth disclosing, are the only
        # ones that disclose nothing. Single-block payloads get it from the
        # renderer, so it is never added twice.
        _split_img_n, _split_img_b = _kept_image_bytes(result.images)
        text_blocks = [
            *text_blocks,
            handover_cost.handover_line(
                result.payload,
                image_count=_split_img_n,
                image_bytes=_split_img_b,
            ),
        ]
    if disclosure:
        text_blocks = [*text_blocks, disclosure]
    return (text_blocks, image_specs)
