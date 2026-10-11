"""Moved from handlers (refactor P2): mobile_run."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from handlers import _common, _elicit, mobile_gate, mobile_step
from tools.suite_store import load_suite
from tools.tool_briefs import MOBILE_BRIEF
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")


async def _mobile_resume_device_lock(run_id: str, token: str, budget: object) -> str:
    # Function-local imports, like handle_mobile_test: tools.mobile is absent
    # from the distribution build.
    from tools.mobile import render as mobile_render
    from tools.mobile.device_lock import (
        take_device_lock,
    )

    # THE DEVICE LOCK, RE-ACQUIRED ON EVERY RESUME. The MCP process
    # restarts on every code and `.env` edit and a run survives that on
    # disk, so a resumed run has to take the lock again -- the kernel
    # dropped the old one when the old process died. Idempotent for a
    # run this process already holds: no syscall, no write.
    #
    # THE HEARTBEAT STARTS BEFORE THE LOCK, and the order is the
    # fix, not a detail. It used to start after
    # `_mobile_resume_device_stage`, which returns POINTER replies ("the
    # emulator is still starting", a re-run preflight) -- so a resume
    # that hit one of those held the device with `heartbeat._WRITERS`
    # still empty, and nothing in this process could ever give it back.
    # Abandon that chat and the emulator is dead to every other process
    # until the server restarts, which is precisely the hazard this
    # design refuses to accept for the pre-run phase.
    #
    # Starting it first is safe: this chat already holds the run's LEASE
    # (checked by the caller), which is the only thing the writer refreshes.
    mobile_step._mobile_start_heartbeat(run_id, token)
    # The device this run is on comes from its own manifest, inside
    # `take_device_lock`: a resume must hold the device it recorded, not
    # whatever is attached now.
    taken = (take_device_lock(run_id, lease=token) or {}).get("content") or {}
    if not taken.get("acquired"):
        return mobile_render.device_busy_block(taken)
    device = await mobile_step._mobile_resume_device_stage(run_id, budget=budget)
    if device:
        return device
    # A REBOOTED EMULATOR COMES BACK ON A DIFFERENT SERIAL, and the
    # stage above writes the fresh one to the manifest. The lock taken a
    # moment ago names the OLD device, so re-take: the acquire is a
    # no-op when nothing moved, and when something did it takes the real
    # device and drops the stale hold rather than driving a device this
    # run does not own.
    retaken = (take_device_lock(run_id, lease=token) or {}).get("content") or {}
    if not retaken.get("acquired"):
        return mobile_render.device_busy_block(retaken)
    return ""


async def _mobile_release_lost_run(run_id: str, claimed: dict) -> str:
    from tools.mobile import render as mobile_render
    from tools.mobile import run_store
    from tools.mobile.session_finish import (
        finish_device,
    )

    held = claimed.get("content") or {}
    # Displaced. Give the device back NOW rather than waiting
    # for this process's heartbeat writer to notice on its next
    # beat: this is the holder releasing its own lock,
    # synchronously, in the call that learned it lost the run.
    #
    # PRESENT THIS CHAT'S OWN LEASE. This branch has just been told
    # it lost the run, so it must NOT claim holder authority: if the
    # lock is held under this chat's token the release is right, and
    # if it is held under the NEW holder's token it must be refused.
    # Passing nothing used to mean `as_holder=True`, which released
    # the lock the current holder was driving under.
    await finish_device(
        run_id,
        lease=str((claimed.get("content") or {}).get("session_token") or ""),
    )
    return mobile_render.takeover_block(
        run_store.takeover_message(run_id, str(held.get("holder") or "?"))
    )


async def _mobile_resume(
    run_id: str,
    session_token: str,
    *,
    continue_run: bool,
    budget: object,
    progress: _common.ProgressCb,
) -> str:
    # An existing run: lease first, then the device, then the next packet.
    from tools.mobile import render as mobile_render
    from tools.mobile import run_store
    from tools.mobile.session_core import claim
    from tools.mobile.session_runs import (
        resolve,
    )
    from tools.untrusted import single_line as _safe

    claimed = claim(run_id, session_token, force=not session_token)
    if claimed.get("error"):
        return "\u26a0\ufe0f " + _safe(claimed["error"], 300)
    held = claimed.get("content") or {}
    if held.get("state") != run_store.HELD:
        return await _mobile_release_lost_run(run_id, claimed)
    token = str(held.get("session_token") or "")
    # A resume can arrive days later, in a new chat, on a machine that
    # has rebooted since: check the DEVICE before producing a packet for
    # it. Deliberately AFTER the lease check, so a displaced chat can
    # never boot an emulator it does not own or take its lock.
    blocked = await _mobile_resume_device_lock(run_id, token, budget)
    if blocked:
        return blocked
    resolved = resolve(run_id, token)
    if resolved.get("error"):
        return "\u26a0\ufe0f " + _safe(resolved["error"], 400)
    body = resolved["content"] or {}
    if body.get("gate") and not continue_run:
        return mobile_render.gate_block(
            {
                "done": body.get("done"),
                "total": body.get("total"),
                "failed": body.get("failed"),
            }
        )
    # `past_gate` rides along: without it the gate is re-decided inside
    # _mobile_next, which does not know the tester already answered, and
    # the run never advances past case 20.
    return await mobile_step._mobile_next(
        run_id, token, progress=progress, past_gate=continue_run, budget=budget
    )


async def handle_mobile_test(
    source: str = "",
    suite_id: str = "",
    goal: str = "",
    cases: str = "",
    app: str = "",
    package: str = "",
    run_id: str = "",
    session_token: str = "",
    apply: bool = False,
    continue_run: bool = False,
    serial: str = "",
    avd: str = "",
    new_run: bool = False,
    locale: str = "",
    capture: str = "",
    capture_ack: bool = False,
    reset_app: bool = False,
    charter: str = "",
    *,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
) -> str:
    """Drive the mobile emulator lane. Returns markdown; never raises.

    Gates, delegates, renders and audits; the notes above this function say
    why (guards, function-local ``tools.mobile`` imports, the ``_safe`` import).
    """
    from tools.untrusted import single_line as _safe

    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()

    request = _MobileTestRequest.from_call(locals())
    try:
        return await _mobile_test_route(request, run_id, session_token, continue_run)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp mobile_test failed")
        _common._capture_error(exc, "qa_mobile_test")
        return "⚠️ The mobile run could not continue: " + _safe(str(exc), 200)


async def _mobile_test_route(
    request: "_MobileTestRequest", run_id: str, session_token: str, continue_run: bool
) -> str:
    """Audit the call, then resume an existing run or start a new one."""
    from tools.mobile.session_audit import audit_detail
    from tools.mobile.session_core import new_budget

    budget = new_budget()
    await _common._audit(
        "mcp_mobile_test",
        entity_id=run_id or None,
        detail=audit_detail(
            run_id=run_id, step=(request.source or "menu"), apply=request.apply
        ),
    )
    # --- an existing run: lease first, then the next packet ------------
    if run_id:
        return await _mobile_resume(
            run_id,
            session_token,
            continue_run=continue_run,
            budget=budget,
            progress=request.progress,
        )
    # --- a new run: device, app, preflight, then the menu --------------
    return await _mobile_new_run(request)


# ---------------------------------------------------------------------------
# Notes for ``handle_mobile_test`` (moved out of the function to keep it short)
#
# The state machine itself lives in the ``tools/mobile/session_*.py`` modules and every
# string in ``tools/mobile/render.py`` -- the handler gates, delegates,
# renders and audits, and holds no state of its own. That matters because the
# MCP process restarts on every code and `.env` edit: any state kept there
# would be lost by exactly the event that makes a tester resume.
#
# Two independent guards, like ``handle_push_suite``: the lane's kill-switch
# AND ``apply=true`` on any step that installs, downloads or launches. A
# flag-OFF call carrying ``apply=true`` REFUSES BY NAME rather than quietly
# doing a dry run, because a success-shaped reply for a step that never ran is
# the worse failure.
#
# Every ``tools.mobile`` import is function-local, exactly as
# ``handle_push_suite`` imports its pushers: those modules are absent from the
# distribution build, so a module-scope import would break it at import time
# on an install no test in this repo can observe.
#
# tools.untrusted, NOT agents.api_test_agent: `_safe` there is a bare alias
# of `single_line` (api_test_agent.py:423) and that module is deliberately
# absent from scripts/build_dist.AGENT_FILES. The import was harmless only
# while the lane could not register on a distribution build; fixing that
# (2026-09-04) made all three mobile tools raise ModuleNotFoundError on a
# dist -- confirmed by calling them on the published v1.77.0 install.
#
# The new-run body lives in `_mobile_new_run`; the notes on WHY the lock is
# taken where it is stay here, with the call that starts the sequence.
# THE LOCK IS TAKEN BEFORE THE DEVICE STAGE. Booting, downloading and
# installing are device-touching too, and they run before any run_id
# exists -- so the lock is held under a label THIS CALL mints, and
# handed to the run inside `_mobile_start` the moment there is a run to
# hand it to.
#
# PER CALL, not per process. It was per process, and `acquire` grants a
# matching label reentrantly, so two concurrent new-run calls in one MCP
# server both got `acquired=True` and both drove the emulator -- and
# whichever returned first released the other's hold in its `finally`.
# An owner label must identify exactly ONE holder.
#
# HELD FOR EXACTLY THIS ONE CALL, and that is the deliberate part. A
# placeholder owner has no run and no lease heartbeat, so nothing could
# release it if this chat vanished mid-provisioning, and the device
# would stay dead until the editor was restarted. Once the run is
# planned the identity IS durable -- on disk, resumable,
# heartbeat-backed -- and the lock is then kept across calls.
#
# Holding it ACROSS the pointer replies would not close the double-spawn
# it looks like it should: the next call decides from the same
# `adb devices` answer, so serialising two calls that read the same
# state produces the same outcome twice. See
# `.claude/plans/plan-emulator-lock-2026-09-04.md` §5.2.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MobileTestRequest:
    """Every start argument of ``handle_mobile_test``, for the new-run helpers."""

    source: str
    suite_id: str
    goal: str
    cases: str
    app: str
    package: str
    apply: bool
    serial: str
    avd: str
    new_run: bool
    locale: str
    capture: str
    capture_ack: bool
    reset_app: bool
    charter: str
    choose: _common.ChooseCb
    progress: _common.ProgressCb

    @classmethod
    def from_call(cls, params: dict) -> "_MobileTestRequest":
        """Build from a handler's ``locals()``; names that are no field are ignored."""
        return cls(**{k: v for k, v in params.items() if k in cls.__dataclass_fields__})

    def source_menu(self) -> str:
        """The source menu (or the narrow conflict question) for these arguments."""
        from tools.mobile import render as mobile_render

        return mobile_render.source_menu_markdown(
            conflict=mobile_render.implied_sources(
                suite_id=self.suite_id, goal=self.goal, cases=self.cases
            ),
            unmatched=self.source,
        )

    def start_kwargs(self) -> dict:
        """The request-owned keyword arguments ``_mobile_start`` takes."""
        return {
            "suite_id": self.suite_id,
            "goal": self.goal,
            "cases": self.cases,
            "charter": self.charter,
            "progress": self.progress,
            "new_run": self.new_run,
        }


async def _mobile_pick_or_menu(req: _MobileTestRequest) -> tuple[str, str]:
    """``(picked, menu_reply)``: the source key, or the menu to send back.

    No device work happens here, and the menu is shown before any lock."""
    from tools.mobile import render as mobile_render

    # THE SOURCE IS PICKED BEFORE ANY DEVICE WORK (fix round 2, item 3).
    # The menu is a question about the arguments, not the device, and it
    # used to be asked LAST -- after the device was selected and locked,
    # the locale read, the app resolved and launched and the preflight run
    # -- so a bare "what should the emulator run?" cost 50 s on the Air,
    # and up to the boot wait on a cold emulator. `_mobile_pick_source`
    # touches no device (it elicits through `choose` at most), so moving it
    # here changes the order of questions only: source, then device.
    picked = await _mobile_pick_source(
        req.source,
        suite_id=req.suite_id,
        goal=req.goal,
        cases=req.cases,
        choose=req.choose,
    )
    # An INSTALL-menu key in `source` (`play_store`, `installed_package`,
    # ...) answers the app question, not this one: that call still runs
    # the device and app stages first and is asked this menu after them,
    # exactly as before the move.
    install_only = bool(
        mobile_render.install_source_for_label(str(req.source or "").strip())
    )
    if not picked and not install_only:
        # Two start arguments implying different lanes ask the NARROW
        # question; nothing given asks the menu. One entry point, so a
        # conflict cannot be rendered by an untested path.
        # `source` reaches here ONLY when it matched no option key --
        # `_mobile_pick_source` returns the key for everything it
        # recognises -- so passing it cannot mislabel a good answer.
        return picked, req.source_menu()
    return picked, ""


async def _mobile_claim_device(req: _MobileTestRequest, owner: str) -> tuple[str, str]:
    """``(reply, serial)``: stage and lock the device, then give selection back.

    An empty ``serial`` means stop and send ``reply``. Otherwise ``reply`` is
    the idle-release note to put above the run-start reply (often empty)."""
    from tools.mobile import render as mobile_render
    from tools.mobile.device_lock import (
        take_device_lock,
    )
    from tools.mobile.session_finish import (
        release_select_lock,
    )

    staged, serial = await _mobile_device_stage(
        req.apply,
        serial=req.serial,
        avd=req.avd,
        locale=req.locale,
        choose=req.choose,
        progress=req.progress,
    )
    if staged:
        return staged, ""
    held = (take_device_lock(owner, serial=serial) or {}).get("content") or {}
    held, idle_note = await _mobile_retake_after_idle(held, owner, serial)
    if not held.get("acquired"):
        return mobile_render.device_busy_block(held), ""
    # Given back BEFORE the long steps, not after, or the concurrency
    # this sequencing exists for would be nominal: install and preflight
    # are the minutes, and they touch only the device now named.
    release_select_lock(owner)
    return idle_note, serial


async def _mobile_launch_target(serial: str, target: str) -> None:
    """Bring the confirmed app to the foreground; best-effort."""
    if target:
        # D3: bring the confirmed app to the foreground before the
        # preflight/first packet, so the first screen shown is the
        # app the tester asked to test rather than whatever the
        # emulator already had up -- matters most for
        # `installed_package`, whose app never goes through an
        # install step that would have launched it. Best-effort: a
        # launch failure here is not fatal, preflight still runs.
        from tools.mobile import adb as mobile_adb

        await mobile_adb.launch(serial, target)


async def _mobile_preflight_reply(serial: str, target: str) -> str:
    """The reply that stops the run when preflight fails, or ``""``."""
    from tools.mobile import preflight as mobile_preflight
    from tools.mobile import render as mobile_render
    from tools.mobile.session_finish import (
        preflight_for,
    )
    from tools.untrusted import single_line as _safe

    checked = await preflight_for({"package": target, "serial": serial})
    if checked.get("error"):
        return "⚠️ " + _safe(checked["error"], 300)
    pf = checked.get("content") or {}
    if not pf.get("ok"):
        return mobile_render.preflight_block(pf, mobile_preflight.render(pf))
    return ""


async def _mobile_offer_u2(req: _MobileTestRequest, serial: str) -> None:
    """Offer the helper app on an applying call."""
    if req.apply:
        from tools.mobile import u2_consent

        # Item 4: asked once per device per server, only when the helper
        # is importable and not yet on; a no never blocks the run.
        await u2_consent.offer(serial, _mobile_ask(req.choose))


async def _mobile_prepare_app(
    req: _MobileTestRequest, serial: str, owner: str
) -> tuple[str, object]:
    """``(stop_reply, prepared)``: locale, capture, app, launch, reset, preflight.

    ``prepared`` is ``(target, locale_taken, capture_result, reset_result)``;
    a non-empty ``stop_reply`` means send it back and stop."""
    # THE LANGUAGE, applied and CONFIRMED before anything is installed or
    # replayed. A run whose report says Arabic over English screens is
    # the success-shaped reply this lane refuses everywhere else, so a
    # request that did not take stops here instead. Inside the same
    # `try`, so the `finally` below still gives the device back.
    locale_stage, locale_taken = await _mobile_locale_stage(
        serial, req.locale, req.apply
    )
    if locale_stage:
        return locale_stage, None
    # T6.1: BETWEEN locale and install -- the device is addressable,
    # the language is settled, and nothing has been installed yet, so
    # a tester who declines here has spent no minutes. Inside the same
    # `try` as every other stage, so the `finally` below still gives
    # the device back.
    capture_stage, capture_result = await _mobile_capture_stage(
        serial, req.apply, req.capture, req.capture_ack, owner=owner
    )
    if capture_stage:
        return capture_stage, None
    await _mobile_offer_u2(req, serial)
    app_stage, target = await _mobile_app_stage(
        serial,
        req.package,
        req.source,
        req.app,
        req.apply,
        progress=req.progress,
        choose=req.choose,
    )
    if app_stage:
        return app_stage, None
    await _mobile_launch_target(serial, target)
    reset_stage, reset_result = await _mobile_reset_app_stage(
        serial, target, req.reset_app, req.apply
    )
    if reset_stage:
        return reset_stage, None
    failed = await _mobile_preflight_reply(serial, target)
    return failed, (target, locale_taken, capture_result, reset_result)


async def _mobile_finish_pre_run(owner: str) -> None:
    """Give back this call's own pre-run hold, for a call that handed nothing off."""
    from tools.mobile.session_finish import (
        finish_device,
    )

    # Only ever THIS call's own label, so a sibling call's hold
    # cannot be released here. `as_holder` is stated because the
    # placeholder has no lease to present -- this call minted the
    # label and is unambiguously its owner.
    # The pre-run placeholder never typed, so the restore
    # inside this is a reported no-op. It goes through the
    # same door anyway, so the "no direct release in this
    # file" pin can be absolute rather than a list of
    # exceptions somebody has to keep correct.
    await finish_device(owner, as_holder=True)


async def _mobile_new_run(req: _MobileTestRequest) -> str:
    """The new-run path: pick the source, claim a device, prepare, start."""
    from tools.mobile import render as mobile_render
    from tools.mobile.device_lock import new_provisioning_owner
    from tools.mobile.session_finish import take_select_lock

    picked, menu_reply = await _mobile_pick_or_menu(req)
    if menu_reply:
        return menu_reply
    pre_run_owner = new_provisioning_owner()
    # SELECTION FIRST, THEN THE DEVICE. Choosing is a question about the
    # machine -- it reads `adb devices` and may spawn the shared emulator --
    # so it is serialised under the lane's select lock, which is the lock
    # this line has always taken. Driving is a question about ONE device, so
    # the moment the stage names one this call takes that device's own lock
    # and hands selection back. Two runs on two devices then overlap through
    # the install, the preflight and the whole run; two runs on ONE device
    # still serialise, on the device lock, exactly as they did before.
    taken = (take_select_lock(pre_run_owner) or {}).get("content") or {}
    if not taken.get("acquired"):
        return mobile_render.device_busy_block(taken)
    handed_off = False
    try:
        idle_note, serial = await _mobile_claim_device(req, pre_run_owner)
        if not serial:
            return idle_note
        stopped, prepared = await _mobile_prepare_app(req, serial, pre_run_owner)
        if stopped:
            return stopped
        target, locale_taken, capture_result, reset_result = prepared
        if not picked:
            # Only an install-only call gets here (see the source pick
            # above): its run menu was deferred past the app stage.
            return req.source_menu()
        reply, handed_off = await _mobile_start(
            picked,
            **req.start_kwargs(),
            package=target,
            serial=serial,
            pre_run_owner=pre_run_owner,
            locale=locale_taken,
            capture=capture_result,
            reset_app=reset_result,
        )
        return idle_note + await _mobile_heavy_avd_note(serial) + reply
    finally:
        if not handed_off:
            await _mobile_finish_pre_run(pre_run_owner)


def _mobile_retake(device_lock, owner: str, serial: str, held: dict) -> dict:
    """The lock answer once the holder was freed; ``held`` (busy) if the retake fails."""
    try:
        taken = device_lock.take_device_lock(owner, serial=serial) or {}
    except mobile_step._MOBILE_TOOL_FAILURES:
        logger.warning("retake of device %s failed", serial, exc_info=True)
        return held
    return taken.get("content") or held


async def _mobile_retake_after_idle(held: dict, owner: str, serial: str) -> tuple:
    """``(held, note)``: end an IDLE run that holds the device, then retake it.

    Only a holder shaped like a run id this lane mints is considered; a
    pre-run label or a lease from another process is left alone. The reaper
    decides idleness (``lock_reaper.release_if_idle``) and a holder with no known
    activity is never freed. The note names the old run, so it is wrapped as
    untrusted text. Never raises: a failure keeps the busy answer."""
    holder = str(held.get("holder") or "")
    if held.get("acquired") or not holder:
        return held, ""
    try:
        from tools.mobile import app_tools, device_lock, lock_reaper, run_store
        from tools.mobile.session_finish import (
            awaits_tester,
            idle_ports,
            last_checkpoint_ts,
            run_ended,
        )

        if not run_store.looks_like_a_run_id(holder):
            return held, ""
        if awaits_tester(holder):
            # Waiting on the TESTER (a credential, a confirm) is not abandonment:
            # the agent has nothing to submit until they answer.
            return held, ""
        ports = idle_ports()
        notice = await lock_reaper.release_if_idle(
            holder, serial, ports, persisted_ts=last_checkpoint_ts(holder)
        )
        if notice is None:
            # A holder the reaper just ended but could not report freed may
            # have a free lock: ask once instead of answering a stale busy.
            if run_ended(holder):
                return _mobile_retake(device_lock, owner, serial, held), ""
            return held, ""
        cap = app_tools.MAX_TOOL_TEXT_CHARS
        text = wrap_untrusted("mobile_release", notice.message(), cap)
    except mobile_step._MOBILE_TOOL_FAILURES:
        logger.warning("idle release of %s failed", holder, exc_info=True)
        return held, ""
    return _mobile_retake(device_lock, owner, serial, held), text + "\n"


async def _mobile_heavy_avd_note(serial: str) -> str:
    """The heavy-AVD warning to put above a run-start reply, or ``""``.

    Fix round 2, item 7: the Air drove a 1440x3120 / 600 dpi AVD on a host at
    load ~3 per core and nothing said so. ``display_size`` and
    ``display_density`` are cached per serial. Advice only -- never raises,
    never turns a started run into an error.
    """
    if not serial:
        return ""
    try:
        from tools.mobile import adb as mobile_adb
        from tools.mobile import avd_guard

        size = (await mobile_adb.display_size(serial) or {}).get("content") or [0, 0]
        density = (await mobile_adb.display_density(serial) or {}).get("content")
        note = avd_guard.assess(size[0], size[1], density, avd_guard.host_load())
        return ("⚠️ " + note + "\n\n") if note else ""
    except Exception:
        logger.exception("mobile heavy-AVD check failed")
        return ""


# A `given` source may be answering a DIFFERENT question that already consumed
# it earlier in this same call -- e.g. `source="installed_package"` is a valid
# INSTALL-menu key, `_mobile_app_stage` already used it, and it matches no
# RUN-menu key. Falling straight to "ask" ignored `goal`/`cases`/`suite_id`
# even when one of them already named the run lane -- the D3 field defect, so
# `_mobile_pick_source` falls through to the implied-sources check. With no
# implied source either it asks, as for a genuinely unmatched answer.
async def _mobile_pick_source(
    source: str,
    *,
    suite_id: str = "",
    goal: str = "",
    cases: str = "",
    choose: _common.ChooseCb = None,
) -> str:
    """Resolve the start choice: an argument, what the tester already said, a
    dialog, or ``""``.

    ``""`` means "ask", and the markdown menu is the tested path: MCP
    elicitation arrives collapsed-and-required in some editors, so the dialog is
    an optimisation and the menu is the product.

    **A NUMBER is no longer accepted.** The host re-presents that menu in its
    own question UI, which relabels (a/b/c/d) and reorders it, so a returned
    POSITION selected a lane by index -- "explore" shown first started
    ``current_suite``, and a tester saw "4 2 1 3". Only a key identifies an
    option, and ``render.source_for_label`` accepts the key or the exact line
    and nothing else.

    **An argument the tester has ALREADY given decides the source.** This read
    only ``source``, so a tester who pasted their own cases or described a goal
    was shown the six-way menu anyway while the answer sat unread in the call.
    ``cases``, ``goal`` and ``suite_id`` each name exactly one lane
    (``render.SOURCE_IMPLICATIONS``), so the menu is asked only when NONE of
    them is set. When two disagree this returns ``""`` and the caller renders
    the NARROWER question rather than the menu -- guessing which one the tester
    meant is the failure this whole function exists to avoid.
    """
    from tools.mobile import render as mobile_render

    given = str(source or "").strip()
    matched = mobile_render.source_for_label(given) if given else ""
    if matched:
        return matched
    implied = mobile_render.implied_sources(suite_id=suite_id, goal=goal, cases=cases)
    if len(implied) == 1:
        return implied[0]
    if implied or given:
        return ""
    return await _mobile_pick_source_dialog(choose)


async def _mobile_pick_source_dialog(choose: _common.ChooseCb) -> str:
    """The run-menu dialog: the chosen source key, or ``""`` to show the menu."""
    from tools.mobile import render as mobile_render

    picked = await _elicit._elicit_choice(
        choose, "What should the emulator run?", mobile_render.source_labels()
    )
    if picked.status == _common.CHOSEN:
        # Which path answered is logged (fix round 3, item 6); the label is a
        # server-authored menu entry, not tester text.
        logger.info("mcp emulator source picked via dialog: %r", picked.value)
        return mobile_render.source_for_label(picked.value or "")
    logger.info(
        "mcp emulator source: %s",
        "dialog timed out, text menu shown"
        if getattr(picked, "timed_out", False)
        else "no dialog answer, text menu shown",
    )
    return ""


async def _mobile_ensure_ready(serial: str, avd: str = "") -> tuple:
    """``(markdown_to_send_back, serial)`` for one named device: an empty
    markdown means the device is ready."""
    from tools.mobile import render as mobile_render
    from tools.mobile.session_device import ensure_device

    ready = await ensure_device(serial=serial, avd=avd)
    if ready.get("error"):
        return "⚠️ " + str(ready["error"])[:300], ""
    state = ready.get("content") or {}
    picked_serial = str(state.get("serial") or "")
    if str(state.get("state") or "") != "ready":
        return mobile_render.device_pending_block(state), picked_serial
    return "", picked_serial


async def _device_stage_pick(booted: list, choose: _common.ChooseCb) -> tuple:
    """Several emulators are running: let the tester pick one, then ready it."""

    labels = [
        str(item.get("serial") or "")
        + " ("
        + (str(item.get("avd") or "") or "unknown AVD")
        + ")"
        for item in booted
    ]
    picked = await _elicit._elicit_choice(
        choose,
        "Several emulators are running. Which one should this run use?",
        labels,
    )
    if picked.status != _common.CHOSEN:
        menu = "\n".join(str(i + 1) + ". " + label for i, label in enumerate(labels))
        return (
            "## Several emulators are running\n\n"
            + menu
            + "\n\nCall `qa_mobile_test` again with `serial` set to the "
            "one you want.",
            "",
        )
    # The label came back from the CLIENT, so the serial in it is not
    # evidence. Only a serial we actually enumerated may be driven.
    chosen_serial = str(picked.value or "").split(" ", 1)[0].strip()
    if chosen_serial not in {str(item.get("serial") or "") for item in booted}:
        return (
            "## That device is not one of the running emulators\n\n"
            "Call `qa_mobile_test` again with `serial` set to one of: "
            + ", ".join("`" + str(i.get("serial") or "") + "`" for i in booted),
            "",
        )
    return await _mobile_ensure_ready(chosen_serial)


def _avd_choice_pointer(wanted: str, avds: list) -> str:
    """The pointer reply when the AVD choice is unknown or ambiguous, else ``""``."""
    if wanted and wanted not in avds:
        return (
            "## That emulator (AVD) does not exist on this machine\n\n"
            "Call `qa_mobile_test` again with `avd` set to one of: "
            + ", ".join("`" + name + "`" for name in avds)
        )
    if not wanted and len(avds) > 1:
        menu = "\n".join(str(i + 1) + ". `" + name + "`" for i, name in enumerate(avds))
        return (
            "## Several emulators (AVDs) are configured\n\n"
            + menu
            + "\n\nCall `qa_mobile_test` again with `avd` set to the one you want."
        )
    return ""


async def _device_stage_boot(avd: str, apply: bool, locale: str) -> tuple:
    """Nothing is booted: show the setup guide, or boot the tester's own AVD."""
    # Nothing booted at all. This server downloads no SDK and makes no AVD unasked
    # (docs/RETIRED_CAPABILITIES.md -> 6), so a machine without them gets the
    # setup guide, and only an AVD the TESTER created can be booted. The one
    # question the guide turns on -- is there an emulator binary -- is asked of
    # the same `locate_sdk` every other SDK consumer asks.
    from tools.mobile import emulator, sdk_locator
    from tools.mobile import render as mobile_render
    from tools.mobile.session_device import ensure_device

    located = (sdk_locator.locate_sdk() or {}).get("content") or {}
    if not str((located.get("tools") or {}).get("emulator") or ""):
        guide = mobile_render.setup_guide(mobile_render.SETUP_NO_SDK)
        return mobile_render.setup_guide_block(guide), ""
    listed = await emulator.list_avds()
    if listed.get("error"):
        return "⚠️ " + str(listed["error"])[:300], ""
    avds = [str(name) for name in (listed.get("content") or []) if str(name)]
    if not avds:
        guide = mobile_render.setup_guide(mobile_render.SETUP_NO_AVD)
        return mobile_render.setup_guide_block(guide), ""
    wanted = str(avd or "").strip()
    pointer = _avd_choice_pointer(wanted, avds)
    if pointer:
        return pointer, ""
    wanted = wanted or avds[0]
    if not apply:
        return (
            mobile_render.apply_refusal(
                "Starting the " + wanted + " emulator",
                "one detached process",
            ),
            "",
        )
    # THE ONLY BRANCH THAT SPAWNS, so the only one that can set the language
    # from the first frame. Every branch above ADOPTED a device somebody else
    # booted; `_mobile_locale_stage` handles those by reading back.
    ready = await ensure_device(avd=wanted, locale=str(locale or ""))
    if ready.get("error"):
        return "⚠️ " + str(ready["error"])[:300], ""
    state = ready.get("content") or {}
    picked_serial = str(state.get("serial") or "")
    if str(state.get("state") or "") != "ready":
        return mobile_render.device_pending_block(state), picked_serial
    return "", picked_serial


async def _mobile_device_stage(
    apply: bool,
    *,
    serial: str = "",
    avd: str = "",
    locale: str = "",
    choose: _common.ChooseCb = None,
    progress: _common.ProgressCb = None,
) -> tuple:
    """Device selection, then -- only if nothing is already booted --
    the tester's own AVD, or the setup guide. Returns
    ``(markdown_to_send_back, serial)``.

    A tuple rather than a string because the caller needs the serial and asking
    for it twice would mean two device probes per call -- and, worse, two
    answers that could disagree.

    Device selection runs FIRST: an explicit ``serial`` or an already-booted
    emulator means there is nothing to boot. Only when NOTHING is connected
    does this look for the tester's AVDs, and spawning still needs
    ``apply=true``.

    Neither the spawn nor a boot-wait is allowed to block this call: the
    emulator is started and
    then polled with an explicit bounded budget, so the reply is always a
    pointer to ``qa_mobile_status`` rather than a tool call the client kills
    at its own timeout.
    """
    from tools.mobile import emulator

    given_serial = str(serial or "").strip()
    if given_serial:
        # BEFORE the device is touched: a serial the tester copied out of
        # `qa_list_devices` may name an iOS simulator or phone, which this lane
        # cannot drive. Fails open -- see `_mobile_non_android`.
        refusal = await mobile_gate._mobile_non_android(given_serial)
        if refusal:
            return refusal, ""
        return await _mobile_ensure_ready(given_serial, str(avd or ""))

    running = await emulator.list_running()
    if running.get("error"):
        return "⚠️ " + str(running["error"])[:300], ""
    booted = running.get("content") or []
    if len(booted) == 1:
        only = booted[0]
        return await _mobile_ensure_ready(str(only.get("serial") or ""))
    if len(booted) > 1:
        return await _device_stage_pick(booted, choose)
    return await _device_stage_boot(avd, apply, locale)


async def _mobile_locale_stage(serial: str, locale: str, apply: bool) -> tuple:
    """Put the device into the requested language. ``(markdown_or_empty, record)``.

    Three outcomes:

    * nothing requested -> empty markdown, and a record saying what the device
      was already in, so the report can still state the language;
    * requested and confirmed -> empty markdown and the record;
    * requested and NOT confirmed -> a refusal that NAMES what the device is
      actually in and the two ways to fix it. This is the ORDINARY outcome on an
      emulator the tester started themselves: `persist.sys.locale` is a root
      property on the `google_apis_playstore` user build, so the write does not
      land, and letting the run proceed would produce a report headed with a
      language the screenshots are not in.

    Changing the device's language is an effect on the tester's device, so a
    REQUEST needs `apply=true`, exactly as an install does. A call with no
    `locale` touches nothing and needs nothing.
    """
    from tools.mobile import render as mobile_render
    from tools.mobile.session_records import apply_locale

    wanted = str(locale or "").strip()
    if wanted and not apply:
        return (
            mobile_render.apply_refusal(
                "Setting the device language to " + wanted,
                "one property write on the emulator",
            ),
            None,
        )
    applied = await apply_locale(serial, wanted)
    if applied.get("error"):
        return "\u26a0\ufe0f " + str(applied["error"])[:300], None
    record = applied.get("content") or {}
    if wanted and not record.get("matched"):
        return _locale_mismatch_block(wanted, record), None
    return "", record


def _locale_mismatch_block(wanted: str, record: dict) -> str:
    """The refusal naming the language the device is actually in."""
    actual = str(record.get("actual") or "")
    detail = str(record.get("detail") or "")
    return (
        "## The emulator is not in "
        + wanted
        + "\n\nIt is in `"
        + (actual or "a language it would not report")
        + "`"
        + ((" (" + detail + ")") if detail else "")
        + ". Nothing was run: a report headed "
        + wanted
        + " over screens in another language is worse than no run at "
        + "all.\n\nTwo ways forward:\n\n"
        + "1. Let the lane boot its own emulator with the language set from "
        + "the first frame -- call `qa_mobile_test` with `locale` and no "
        + "`serial`, with no emulator already running. That is the only "
        + "mechanism that works without root.\n"
        + "2. Set it by hand on this emulator (Settings > System > "
        + "Languages) and call `qa_mobile_test` again."
    )


async def _mobile_reset_app_stage(
    serial: str, package: str, reset_app: bool, apply: bool
) -> tuple:
    """Wipe THIS run's own app before its first packet.

    ``(markdown_or_empty, record_or_none)`` -- the same shape
    `_mobile_locale_stage`/`_mobile_capture_stage` return, and gated on
    `apply` alone for the same reason: this runs after `_mobile_app_stage`
    confirms `package` but still before `session.preflight_for`, so no
    device screen exists yet for the destructive guard to judge. There is no
    "actuated node" here any more than there is for locale or capture, so
    `apply=true` plus the confirmed, run-scoped `package` IS the guard
    equivalent at this point in the flow -- exactly as it already is for
    those two stages. `qa_submit_mobile_step`'s `clear_app_data` op is the
    SAME primitive (`adb.clear_app_data` then `adb.launch`), guarded by the
    ordinary destructive-guard check instead, because a script action runs
    against a device that already has a screen to judge it against.

    A clear failure returns a refusal here and nothing else: the caller must
    return this stage's markdown straight back and never reach
    `preflight_for`, so a run is never planned against an app this call
    could not actually reset.
    """
    if not reset_app:
        return "", None
    from tools.mobile import render as mobile_render

    if not apply:
        return (
            mobile_render.apply_refusal(
                "Resetting the app's data",
                "pm clear on this run's own app, then relaunching it",
            ),
            None,
        )
    from tools.mobile import adb as mobile_adb

    cleared = await mobile_adb.clear_app_data(serial, package)
    if cleared.get("error"):
        return (
            "\u26a0\ufe0f Could not reset the app's data: "
            + wrap_untrusted("device_pm_clear", str(cleared["error"]), limit=400),
            None,
        )
    await mobile_adb.launch(serial, package)
    return "", {"requested": True, "package": str(package or ""), "cleared": True}


def offer_capture(serial: str) -> bool:
    """True when this device has never been answered about.

    Read by the run reply so a tester learns the feature exists, WITHOUT the
    run stopping to ask. Silence is not consent and it is not refusal either --
    it just means nobody has been asked yet, so the offer is worth making once.
    """
    try:
        from tools.mobile_capture import ledger as capture_ledger

        seen = capture_ledger.asked_before(serial)
        if seen.get("error"):
            return False
        return not (seen.get("content") or {}).get("asked")
    except Exception:  # pragma: no cover - the offer is never the run
        logger.exception("mobile_capture: could not decide whether to offer capture")
        return False


def _remember_capture_decline(serial: str) -> None:
    """Record a declined device so the menu is not shown again. Never raises.

    A decline that is not written down is a decline the tester gets asked about
    again on the next run, which is how a one-time question becomes a nag.
    """
    from tools.mobile_capture import ca as capture_ca
    from tools.mobile_capture import ladder
    from tools.mobile_capture import ledger as capture_ledger

    try:
        made = capture_ca.ensure_ca()
        fingerprint = str(((made.get("content") or {}).get("fingerprint")) or "")
        if fingerprint:
            capture_ledger.record(
                serial, fingerprint, ladder.TIER_NONE, note="declined by the tester"
            )
    except Exception:  # pragma: no cover - defensive; the record is not the run
        logger.exception("mobile_capture: could not record the capture decline")


def _capture_unset_stage(serial: str) -> tuple:
    """``capture`` unset: ask ONCE PER DEVICE, or carry the offer as a record."""
    from tools.mobile_capture import ladder
    from tools.mobile_capture import ledger as capture_ledger

    # A device we have already answered about keeps its answer -- asking every
    # run would put a blocking menu in front of every existing caller forever,
    # and never asking would make the run-integrated half of this feature
    # undiscoverable.
    seen = capture_ledger.asked_before(serial)
    if seen.get("error") or (seen.get("content") or {}).get("asked"):
        return None, None
    if offer_capture(serial):
        # The OFFER, as a record rather than a menu: the run continues and
        # the status line carries one line telling the tester the feature
        # exists. Returning (None, None) here is what shipped, and it made
        # the whole lane invisible -- no record, no line, no capture, on
        # every run.
        return None, ladder.record(
            {
                "tier": ladder.TIER_NONE,
                "reason": ladder.REASON_NOT_OFFERED,
                "serial": serial,
                "offer": True,
            }
        )
    # Never returns markdown here: an unset `capture` means the caller said
    # nothing about capture, and a run they did not ask to change must not
    # stop for a question. Only an explicit capture="on" without an ack pauses
    # for consent.
    return None, None


def _capture_declined_stage(serial: str) -> tuple:
    """``capture="off"``: remember the decline and record it at ``TIER_NONE``."""
    from tools.mobile_capture import ladder

    # Remember the decline, so the tester is not asked again on this
    # device. Recorded under the CURRENT fingerprint, but read back by
    # asked_before, which ignores the fingerprint on purpose: a new CA
    # must not reopen a closed conversation.
    _remember_capture_decline(serial)
    return None, ladder.record(
        {
            "tier": ladder.TIER_NONE,
            "reason": ladder.REASON_NO_CONSENT,
            "serial": serial,
        }
    )


async def _mobile_capture_stage(
    serial: str, apply: bool, capture: str, capture_ack: bool, *, owner: str
) -> tuple:
    """Between locale and install (T6.1): ask consent for API capture, or
    act on it. ``(markdown_or_none, capture_result_or_none)``.

    Declining, or leaving `capture` unset, never stops the run -- the ONLY
    markdown this returns is the consent MENU itself, when the tester has
    said `capture="on"` but not yet confirmed with `capture_ack=true`. Every
    other outcome, including any refusal reached while calling
    ``mobile_capture.prepare``, is folded into `capture_result` at
    `TIER_NONE` with its reason code, and the caller continues the run
    (brief decision 2).
    """
    from tools import mobile_capture as api_capture
    from tools.mobile import render as mobile_render
    from tools.mobile_capture import ladder

    wanted = str(capture or "").strip().lower()
    if wanted not in ("on", "off"):
        return _capture_unset_stage(serial)
    if wanted == "off":
        return _capture_declined_stage(serial)
    if not capture_ack:
        return mobile_render.capture_menu_markdown(), None
    prepared = await api_capture.prepare(serial, owner=owner, apply_=apply)
    result = (
        (prepared.get("content") or {})
        if not prepared.get("error")
        else ladder.record(
            {
                "tier": ladder.TIER_NONE,
                "reason": ladder.REASON_DEVICE_GONE,
                "serial": serial,
            }
        )
    )
    return None, result


def _mobile_ask(choose: _common.ChooseCb):
    """The tester-question callback the mobile resolve-or-ask helpers take, or
    None when this client cannot elicit. After one timed-out question the rest
    of that client's questions skip the wait (``tools/mobile/mobile_elicit``)."""
    from tools.mobile import mobile_elicit

    return mobile_elicit.make_mobile_ask(
        choose, _elicit._elicit_choice, client_key=_elicit._elicit_client_key
    )


async def _mobile_not_installed_reply(
    serial: str, target: str, suggestions: list[str]
) -> str:
    """The "not installed" reply, with whatever hints the device can give."""
    from tools import device_manager as _device_manager
    from tools.mobile import run_store as _run_store

    hint = (
        (" Closest installed match: `" + "`, `".join(suggestions) + "`.")
        if suggestions
        else ""
    )
    try:
        installed = await _device_manager.list_installed_apps(
            {"id": serial, "platform": "android"}
        )
        names = [
            str(app.get("id") or "")
            for app in (installed.get("content") or [])
            if isinstance(app, dict) and app.get("id")
        ]
        if names:
            hint += "\n\nInstalled third-party packages: " + ", ".join(names[:20])
        last = _run_store.last_used_package(serial)
        if last:
            hint += "\n\nLast used on this device: `" + last + "`"
    except Exception:
        logger.debug("not-installed hint lookup failed", exc_info=True)
    return (
        "⚠️ `"
        + (target or "(none given)")
        + "` is not installed on the emulator, so there is nothing to run."
        + hint
        + "\n\nPick another install source, or install it and try again."
    )


async def _app_stage_not_installed(
    serial: str, target: str, probed: bool, suggestions: list[str]
) -> tuple:
    """The ``installed_package`` source found no such app: say so, with hints."""
    # The SECOND absence assertion on this path, and it is reachable with an
    # unanswered probe exactly as the menu was. Same rule: only a probe that
    # answered may say the app is not there.
    if not probed:
        return (
            "⚠️ Could not check whether `"
            + (target or "(none given)")
            + "` is on the emulator -- the device did not answer, so this "
            "server will not say either way. Check the emulator is still "
            "running (`qa_list_devices`) and call `qa_mobile_test` again. "
            "Nothing was installed or started.",
            target,
        )
    return await _mobile_not_installed_reply(serial, target, suggestions), target


async def _app_stage_open_store(
    serial: str, install_source: str, value: str, target: str
) -> tuple:
    """Open the Play Store page (or Firebase App Tester) inside the emulator."""
    from tools.mobile import adb as mobile_adb
    from tools.mobile import render as mobile_render

    url = value
    if install_source == "play_store":
        url = (
            value
            if value.startswith("market://")
            else "market://details?id=" + (value or target)
        )
    if install_source == "app_tester":
        url = "market://details?id=com.google.android.apps.firebase.appdistribution"
    opened = await mobile_adb.open_url(serial, url)
    if opened.get("error"):
        return "⚠️ " + str(opened["error"])[:300], target
    return (
        "## Opened on the emulator\n\nFinish the install in the emulator's own "
        "UI, then call `qa_mobile_test` again with the app's `package` name and "
        "`source=installed_package`. Nothing was downloaded by this server."
        + (
            "\n\n" + mobile_render.app_tester_hint(mobile_render.APP_TESTER_PACKAGES[0])
            if install_source == "app_tester"
            else ""
        ),
        target,
    )


async def _installed_package_target(
    serial: str,
    value: str,
    answer_key: str,
    choose: _common.ChooseCb,
    progress: _common.ProgressCb,
) -> tuple:
    """Resolve the package for the ``installed_package`` source: ``(target, menu)``.

    The remembered resolution comes before the picker; a non-empty ``menu``
    means the picker needs the tester's answer and the caller returns it.
    """
    from tools.mobile import run_store

    # A REMEMBERED RESOLUTION, tried before the raw hint. A rerun of the
    # same goal used to re-send the same free-text app name and re-walk
    # the fuzzy-match path every time; a cache hit here is still VERIFIED
    # by the same `session.install_state` probe every other path below
    # takes -- this only skips guessing the bundle id again, never the
    # "is it actually installed" check.
    cached_target = run_store.read_resolved_app(answer_key) if value else {}
    target = str(cached_target.get("package") or "")
    if target:
        return target, ""
    # RESOLVE-OR-ASK over what is installed on THIS device; an empty
    # outcome keeps the old raw-hint path (and its suggestions).
    from tools.mobile import app_stage_pick

    picked = await app_stage_pick.pick_installed(serial, value, _mobile_ask(choose))
    if picked.menu:
        return "", picked.menu
    if picked.said:
        await _common._emit(progress, picked.said)
    return picked.package or value, ""


async def _local_apk_stage(
    serial: str, value: str, target: str, progress: _common.ProgressCb
) -> tuple:
    """The ``local_apk`` source: start the install outside this call."""
    from tools.mobile.session_device import start_install

    if not target:
        return (
            "⚠️ Give the app's `package` name as well as the .apk path: it "
            "is what the preflight and every relaunch check, and this "
            "server cannot read it out of the file. Nothing was installed.",
            "",
        )
    await _common._emit(progress, "\U0001f4e6 Starting the install…")
    started = start_install(serial, value, target)
    if started.get("error"):
        return "⚠️ " + str(started["error"])[:300], target
    return (
        "## Installing `"
        + target
        + "`\n\nThe install runs outside this call (a large APK takes "
        "longer than a tool call may last). Call `qa_mobile_status`; when "
        "the package shows as installed, call `qa_mobile_test` again.",
        target,
    )


async def _app_stage_probe(serial: str, target: str, value: str) -> tuple:
    """Probe the device for *target*: ``(done_or_None, probed, suggestions)``.

    ``done`` is the finished ``(markdown, package)`` pair when the app is
    already installed or its install is still running, else ``None``.
    """
    from tools.mobile import resolve_cache, run_store
    from tools.mobile.session_device import install_state

    # WHETHER THE DEVICE ANSWERED, read from the one witness that knows:
    # `session.install_state` publishes `probed` on its CONTENT, not on the
    # envelope's `error` (set only in a defensive branch, so it read True on
    # exactly the adb failure it was written to catch). `install_state` runs
    # only for a non-empty target, so a first call leaves this False.
    if not target:
        return None, False, []
    state = await install_state(serial, target)
    body = state.get("content") or {}
    probed = bool(body.get("probed"))
    suggestions = [str(s) for s in (body.get("suggestions") or [])][:5]
    if body.get("installed"):
        if value:
            answer_key = resolve_cache.cache_key(serial, value)
            run_store.write_resolved_app(answer_key, target, serial, {})
        return ("", target), probed, suggestions
    if body.get("pending"):
        pending = (
            "## The install is still running\n\n`"
            + target
            + "` has not appeared on the device yet. Nothing in this call "
            "is waiting on it — call `qa_mobile_status` in a few seconds.",
            target,
        )
        return pending, probed, suggestions
    return None, probed, suggestions


async def _app_stage_remembered(
    serial: str, value: str, choose: _common.ChooseCb, progress: _common.ProgressCb
) -> tuple[str, str]:
    """The remembered package for *value*: ``(menu_or_empty, target)``."""
    from tools.mobile import resolve_cache

    # PER SERIAL: a package chosen on one device is never replayed on another.
    answer_key = resolve_cache.cache_key(serial, value) if value else ""
    # A REMEMBERED RESOLUTION, tried before the raw hint; a cache hit is
    # still VERIFIED by `session.install_state`, never skipped.
    target, menu = await _installed_package_target(
        serial, value, answer_key, choose, progress
    )
    return menu, target


async def _mobile_app_stage(
    serial: str,
    package: str,
    source: str,
    app: str,
    apply: bool,
    *,
    progress: _common.ProgressCb = None,
    choose: _common.ChooseCb = None,
) -> tuple:
    """Get the app under test onto the device. ``(markdown_or_empty, package)``.

    The resolved package comes back because the ``installed_package`` source
    carries it in ``app`` rather than in ``package``: deriving it twice is how
    the preflight ends up checking a different app from the one installed.
    Every branch that touches the device needs ``apply=true``, and the refusal
    names the step. The Play-Store branches only OPEN a URL inside the emulator;
    Firebase App Tester is never redistributed by this project.
    """
    from tools.mobile import render as mobile_render

    value = str(app or "").strip()
    # A KEY only. This used to try a POSITIONAL lookup first, and the start menu
    # shares this argument, so a start-menu answer of "3" resolved here to
    # `app_tester`.
    install_source = mobile_render.install_source_for_label(str(source or "").strip())
    target = str(package or "").strip()
    if not target and install_source == "installed_package":
        menu, target = await _app_stage_remembered(serial, value, choose, progress)
        if menu:
            return menu, ""
    done, probed, suggestions = await _app_stage_probe(serial, target, value)
    if done is not None:
        return done
    if not install_source:
        return mobile_render.install_menu_markdown(target, probed=probed), target
    if not apply:
        return (
            mobile_render.apply_refusal(
                "Installing the app (" + install_source + ")", value
            ),
            target,
        )
    if install_source == "local_apk":
        return await _local_apk_stage(serial, value, target, progress)
    if install_source == "installed_package":
        return await _app_stage_not_installed(serial, target, probed, suggestions)
    return await _app_stage_open_store(serial, install_source, value, target)


async def _mobile_avd_of(serial: str) -> str:
    """The AVD name behind *serial*, or ``""`` (2026-09-03, D1 follow-up).

    ``plan_suite_run`` / ``plan_explore_run`` default a missing ``avd`` to
    ``provisioner.AVD_NAME``, and that default is what made D1 come BACK on
    resume: a run that had adopted the tester's own emulator wrote
    ``avd: "qa-agents-api35"`` into its manifest, so when that device was gone
    ``session.ensure_run_device`` re-booted the hardcoded AVD instead of the
    one the run was actually driving. Asking the device settles it once, at the
    only moment the answer is both knowable and needed. Never raises: an
    unanswerable probe returns ``""`` and the planners keep their old default.
    """
    from tools.mobile import emulator

    try:
        named = await emulator.avd_name_of(str(serial or ""))
        return str((named or {}).get("content") or "")
    except Exception:  # pragma: no cover - the planners default without it
        logger.debug("mobile: could not read the AVD name for a serial")
        return ""


def _mobile_resume_hint() -> str:
    """The reply for the "resume" start-menu choice: a pointer, never a run."""
    from tools.mobile.session_runs import (
        list_runs,
    )
    from tools.untrusted import single_line as _safe

    return "⚠️ To resume, call `qa_mobile_test` with the `run_id`. " + _safe(
        (
            "Recent runs: "
            + ", ".join(
                "`" + str(row.get("run_id")) + "`"
                for row in ((list_runs(5) or {}).get("content") or [])
            )
        )
        or "No runs have been recorded on this machine yet.",
        400,
    )


@dataclass(frozen=True)
class _MobileStartContext:
    """What `_mobile_start` has already resolved by the time a path runs."""

    package: str
    serial: str
    avd: str
    device_facts: dict
    locale: object
    capture: object
    reset_app: object
    owner: str
    progress: _common.ProgressCb = None


def _mobile_charter_intake(parsed: dict, package: str) -> str:
    """The charter-intake packet plus the once-per-chat tool brief."""
    from tools.mobile import render as mobile_render
    from tools.mobile import run_packets as mobile_run_agent

    # The intake is the FIRST mobile packet and has no run to memoise
    # a briefing on, so it always carries the mobile brief (same
    # heading `_mobile_packet_text` uses).
    return (
        mobile_render.packet_block(
            mobile_run_agent.build_charter_intake(
                package=package, charter=parsed.get("raw") or {}
            )
        )
        + "\n\n### Tool brief (sent once per chat)\n"
        + MOBILE_BRIEF.strip()
    )


def _mobile_plan_explore(goal: str, parsed: dict, ctx: _MobileStartContext) -> dict:
    """Plan the explore run from the RAW charter body (see the note below)."""
    from tools.mobile.session_runs import (
        plan_explore_run,
    )

    # THE RAW (schema-stripped) BODY, never the normalised `terms`.
    # `new_state` normalises what it is given, and `normalize` STRIPS
    # DEFAULTS_KEY/REJECTED_KEY (the anti-spoof strip) and then re-derives
    # the verdict against a body whose every field is already filled -- so
    # handing it `terms` records "no defaults used" for EVERY run started
    # through this handler, including the goal-only start whose whole
    # justification is that the report names every default it used.
    # Measured on the merge tree: `terms` -> [], the raw body ->
    # ['depth','scope','destructive','budget','stop_on']. `parse` already
    # injected the caller's `goal` into this body, so nothing is lost by
    # passing it. Normalise ONCE, at the one writer. Graded at the HANDLER
    # by M14 / test_a_goal_only_start_through_the_handler_names_its_defaults.
    return plan_explore_run(
        goal,
        charter=parsed.get("raw") or {},
        package=ctx.package,
        serial=ctx.serial,
        avd=ctx.avd,
        device=ctx.device_facts,
        locale=ctx.locale,
        capture=ctx.capture,
        reset_app=ctx.reset_app,
    )


async def _mobile_start_explore(
    charter: str, goal: str, ctx: _MobileStartContext
) -> tuple[str, bool]:
    """The "explore" start-menu choice: parse the charter, then plan and hand off."""
    from tools.mobile import charter as mobile_charter
    from tools.untrusted import single_line as _safe

    # THE CHARTER (step 4). `charter.parse` is the only reader of the
    # host-submitted string: size-capped, json.loads only, every field
    # coerced to the server's typed schema.
    parsed = mobile_charter.parse(charter, goal=goal)
    if parsed.get("error"):
        return "⚠️ " + _safe(parsed["error"], 300), False
    terms = parsed.get("content") or {}
    raw_charter = parsed.get("raw") or {}
    # Audit item 4: the destructive hint reaches the LIVE reply. (Item 5,
    # an out-of-enum value, never gets this far: `charter.parse` above
    # refuses it by name, listing the valid choices.) Read off
    # `raw_charter`, never `terms`: same M11 reason `normalize`'s own
    # docstring gives.
    disclosures = mobile_charter.destructive_hint(
        raw_charter, terms.get("goal") or goal
    )
    if not str(terms.get("goal") or ""):
        # Neither a charter nor a goal: ASK, once, with the SERVER's own
        # questions, as a packet KIND on this same return path -- never a
        # tool, because intake is only ever the first packet of a run.
        # This replaces the bare "give the exploratory goal in a sentence"
        # refusal. A start that already carries a goal is untouched: it
        # runs under the default charter, whose defaults the report NAMES.
        # THE RAW BODY, never `terms`: normalisation fills every field, so
        # handing the builder the normalised charter makes four of the five
        # questions read as ANSWERED and the intake asks ONE -- measured.
        return _mobile_charter_intake(parsed, ctx.package), False
    planned = _mobile_plan_explore(terms.get("goal") or goal, parsed, ctx)
    if planned.get("error"):
        return "⚠️ " + _safe(planned["error"], 300), False
    handed_text, handed_started = await _mobile_hand_off(
        str((planned.get("content") or {}).get("run_id") or ""),
        pre_run_owner=ctx.owner,
        progress=ctx.progress,
    )
    return (
        (disclosures + "\n\n" + handed_text) if disclosures else handed_text
    ), handed_started


async def _mobile_device_probe(serial: str) -> tuple[dict, str]:
    """``(device_facts, avd)`` for *serial*, read once at run creation."""
    # ONE PRODUCER, at the one moment a run is created. Kind, model and API
    # level are read here and STORED on the manifest; every later reader --
    # status, the report, a resume in another chat -- reads that record instead
    # of asking the device again, so two readers cannot disagree and a finished
    # run still says what it ran on after the device is gone.
    from tools.mobile import adb as mobile_adb

    # The AVD name is asked FOR THE SAME DEVICE AT THE SAME TIME (S13): both
    # are read-only probes, and asking one after the other doubled the round
    # trips before the first screen. `_mobile_avd_of` never raises, so a
    # `device_facts` failure propagates exactly as it did alone.
    facts_result, avd = await asyncio.gather(
        mobile_adb.device_facts(serial), _mobile_avd_of(serial)
    )
    return (facts_result or {}).get("content") or {}, avd


async def _mobile_start(
    picked: str,
    *,
    suite_id: str,
    goal: str,
    cases: str,
    charter: str = "",
    package: str,
    serial: str,
    pre_run_owner: str,
    progress: _common.ProgressCb = None,
    new_run: bool = False,
    locale: object = None,
    capture: object = None,
    reset_app: object = None,
) -> tuple:
    """Turn a start-menu choice into a planned run and its first packet.

    Returns ``(markdown, handed_off)``. ``handed_off`` is True once the device
    lock has been RELABELLED from *pre_run_owner* to this run's id -- the
    caller's signal to stop owning it, because the run does now and keeps it
    across calls. Every other return (also a FAILED relabel) leaves it False so
    the caller's ``finally`` gives the device straight back and no lock is
    stranded. The relabel happens HERE because ``_mobile_next`` may release the
    lock, and a release under a stale owner label would silently leak the hold.
    """
    if picked == "resume":
        return _mobile_resume_hint(), False
    device_facts, avd = await _mobile_device_probe(serial)
    ctx = _MobileStartContext(
        package=package,
        serial=serial,
        avd=avd,
        device_facts=device_facts,
        locale=locale,
        capture=capture,
        reset_app=reset_app,
        owner=pre_run_owner,
        progress=progress,
    )
    if picked == "explore":
        return await _mobile_start_explore(charter, goal, ctx)

    reply, collected, filters = await _mobile_collect_cases(
        picked, suite_id, cases, progress
    )
    if reply:
        return reply, False
    return await _mobile_start_suite(picked, collected, filters, ctx, new_run)


def _mobile_rerun_failures_cases() -> tuple[str, list, dict]:
    """The "rerun_failures" choice: the failed cases of the latest run."""
    from tools.mobile.session_runs import (
        list_runs,
        load_run_cases,
    )
    from tools.untrusted import single_line as _safe

    runs = (list_runs(1) or {}).get("content") or []
    if not runs:
        return (
            ("\u26a0\ufe0f There is no previous run to re-run failures from."),
            [],
            {},
        )
    previous = str(runs[0].get("run_id") or "")
    loaded = load_run_cases(previous)
    if loaded.get("error"):
        return "\u26a0\ufe0f " + _safe(loaded["error"], 300), [], {}
    return (
        "",
        list(loaded.get("content") or []),
        {"failed_last_run": previous},
    )


async def _mobile_collect_cases(
    picked: str, suite_id: str, cases: str, progress: _common.ProgressCb
) -> tuple[str, list, dict]:
    """The cases a start-menu choice names: ``(reply, collected, filters)``.

    A non-empty *reply* is the whole answer (a menu, a refusal); an empty one
    means *collected* and *filters* are ready to plan.
    """
    from tools.mobile import render as mobile_render

    if picked in ("stored_suite", "current_suite"):
        return await _mobile_suite_cases(suite_id)
    if picked == "own_cases":
        return await _mobile_own_cases(cases, progress)
    if picked == "rerun_failures":
        return _mobile_rerun_failures_cases()
    return mobile_render.source_menu_markdown(), [], {}


async def _mobile_suite_cases(suite_id: str) -> tuple[str, list, dict]:
    """The "stored_suite" / "current_suite" choice: the cases of one suite."""
    from tools.untrusted import single_line as _safe

    # ONE branch for two menu options, deliberately. This server has no
    # notion of "this chat": the suite generated a moment ago is reached by
    # the same suite_id as one from last week, and the recent-suites picker
    # lists it first. Two code paths would only differ in their wording,
    # and would drift.
    if not suite_id:
        return await _elicit._recent_suites_markdown("qa_mobile_test"), [], {}
    loaded = await load_suite(suite_id)
    if loaded.get("error") or loaded.get("content") is None:
        return (
            "\u26a0\ufe0f Could not load suite `" + _safe(suite_id, 80) + "`.",
            [],
            {},
        )
    return "", list(getattr(loaded["content"], "test_cases", []) or []), {}


async def _mobile_own_cases(
    cases: str, progress: _common.ProgressCb
) -> tuple[str, list, dict]:
    """The "own_cases" choice: the cases the tester pasted in."""
    from tools.mobile import importers as mobile_importers
    from tools.mobile.session_runs import (
        import_cases,
    )
    from tools.untrusted import single_line as _safe

    imported = import_cases(cases)
    if imported.get("error"):
        return "\u26a0\ufe0f " + _safe(imported["error"], 400), [], {}
    body = imported.get("content") or {}
    collected = list(body.get("cases") or [])
    rejections = mobile_importers.render_rejections(body)
    if not collected:
        return (
            (
                "\u26a0\ufe0f No case could be read from that, so no run was "
                "created.\n\n" + rejections
            ),
            [],
            {},
        )
    if rejections:
        await _common._emit(progress, "\u26a0\ufe0f Some rows were not imported.")
    return "", collected, {}


def _mobile_resumable_run(package: str, collected: list) -> dict | None:
    """The earlier run of the SAME cases against this app, if there is one."""
    from tools.mobile.session_runs import case_signature, find_resumable_run

    return find_resumable_run(case_signature(package, collected))


async def _mobile_start_suite(
    picked: str,
    collected: list,
    filters: dict,
    ctx: _MobileStartContext,
    new_run: bool,
) -> tuple[str, bool]:
    """Offer the earlier run, else plan the suite run and hand it off."""
    from tools.mobile.session_runs import plan_suite_run
    from tools.untrusted import single_line as _safe

    # E5 (2026-09-04): the same cases against the same app, again. Eight runs
    # of ONE case were started in seventeen minutes because nothing said that
    # the run just abandoned was the one to go back to. Answer that here,
    # before a ninth is planned -- and only for a start that carries cases, so
    # an explore run and a resume are untouched.
    # Every case-bearing start, filters included.
    #
    # A re-run of failures is covered without a special case: the run it would
    # duplicate stores the cases it KEPT, so a second re-run hashes that same
    # set and matches `case_signature`. An earlier version of this comment said
    # the match came from `source_signature`; that clause was proved dead and
    # deleted, and the sentence is corrected rather than left describing code
    # that is gone.
    if not new_run:
        existing = _mobile_resumable_run(ctx.package, collected)
        if existing:
            return _mobile_repeat_offer(existing), False

    planned = plan_suite_run(
        collected,
        package=ctx.package,
        serial=ctx.serial,
        source=picked,
        filters=filters,
        avd=ctx.avd,
        device=ctx.device_facts,
        locale=ctx.locale,
        capture=ctx.capture,
        reset_app=ctx.reset_app,
    )
    if planned.get("error"):
        return "\u26a0\ufe0f " + _safe(planned["error"], 400), False
    return await _mobile_hand_off(
        str((planned.get("content") or {}).get("run_id") or ""),
        pre_run_owner=ctx.owner,
        progress=ctx.progress,
    )


def _mobile_repeat_offer(existing: dict) -> str:
    """These cases are already a run. Say which, and how to pick it up.

    It offers rather than refuses: a tester who really does want a fresh run
    says so with `new_run=true`, which is named here so the next call needs no
    guesswork.
    """
    run_id = str(existing.get("run_id") or "")
    state = str(existing.get("state") or "unknown")
    done = int(existing.get("done") or 0)
    total = int(existing.get("total") or 0)
    return (
        "## These cases are already a run\n\n"
        "`" + run_id + "` is running the same cases against the same app and is "
        "not finished — state **"
        + state
        + "**, "
        + str(done)
        + " of "
        + str(total)
        + " case(s) done. Nothing new was started.\n\n"
        '- **Carry on with it** — call `qa_mobile_test` with `run_id="'
        + run_id
        + '"`. Every case it already finished is on disk and is not '
        "handed out again.\n"
        "- **See where it stands first** — `qa_mobile_status` with that run id "
        "reads it back from disk and changes nothing.\n"
        "- **Start a fresh run anyway** — the same call again with "
        "`new_run=true`. Do that only if the tester asked for it: a second run "
        "of the same cases produces a second report of the same work."
    )


async def _mobile_hand_off(
    run_id: str, *, pre_run_owner: str, progress: _common.ProgressCb = None
) -> tuple:
    """Claim a freshly planned run and give it the device lock. ``(markdown, True)``.

    ONE place, because the explore and suite branches above both reach it and
    two copies of a lock hand-off is how the two drift -- the same argument
    ``session.claim``'s docstring makes about the two handlers.

    Three things happen here, in this order, and the order matters:

    1. ``claim`` -- a fresh run, so this always succeeds and mints the token.
    2. ``relabel_device_lock`` -- the run exists on disk now, so its id is a
       DURABLE identity: it can be found again from any chat, and its heartbeat
       can give the device back. The lock therefore moves from the caller's
       per-process placeholder to the run, keeping the SAME file descriptor and
       so the same kernel lock -- not released and retaken, which would open a
       window for another process.
    3. ``_mobile_start_heartbeat`` -- a brand-new run had NO heartbeat writer
       until now, which left its device lock with no agent that could ever
       release it. Starting it here is what makes the lock's lifetime the
       writer's lifetime.

    The relabel comes BEFORE ``_mobile_next``, which may release the lock when a
    run lands straight on its report: a release under the placeholder label
    would silently fail and leak the hold.
    """
    token, refusal = await _mobile_claim_fresh_run(run_id)
    if refusal:
        return refusal, False
    refused = _mobile_take_device_lock(run_id, token, pre_run_owner)
    if refused:
        return refused, False
    mobile_step._mobile_start_heartbeat(run_id, token)
    notes = await _mobile_first_stage_notes(run_id)
    rendered = await mobile_step._mobile_next(
        run_id, token, progress=progress, first_packet=True
    )
    for note in notes:
        if note:
            rendered = note + "\n\n" + rendered
    return rendered, True


async def _mobile_claim_fresh_run(run_id: str) -> tuple[str, str]:
    """Claim a freshly planned run: ``(token, "")`` or ``("", refusal_markdown)``."""
    from tools.mobile import knowledge_run
    from tools.mobile.session_core import claim

    await asyncio.to_thread(
        knowledge_run.init_manifest, run_id, mobile_step._MOBILE_RUN_ENV.get()
    )
    claimed = claim(run_id, "", force=True)
    token = str((claimed.get("content") or {}).get("session_token") or "")
    if claimed.get("error") or not token:
        # A claim on a freshly planned run is documented to always succeed, so
        # this is a "cannot happen" -- which is exactly why it was never
        # inspected, and exactly the shape that strands a device: with no token
        # the heartbeat refuses to start, and a lock with no writer and no lease
        # authority is held until the process exits. Give the device back and
        # say so rather than proceeding on an empty authority.
        logger.warning(
            "mcp mobile: could not claim %s after planning it (%s)",
            run_id,
            str(claimed.get("error") or "no session token")[:200],
        )
        return (
            "",
            "\u26a0\ufe0f Run `"
            + run_id
            + "` was planned but could not be claimed, so nothing was started "
            "and the emulator was not taken. Call `qa_mobile_test` with that "
            "`run_id` to pick it up.",
        )
    return token, ""


def _mobile_take_device_lock(run_id: str, token: str, pre_run_owner: str) -> str:
    """Move the device lock to the run: ``""`` on success, else a refusal."""
    from tools.mobile import render as mobile_render
    from tools.mobile.device_lock import (
        relabel_device_lock,
        take_device_lock,
    )

    moved = (relabel_device_lock(pre_run_owner, run_id) or {}).get("content") or {}
    if moved.get("relabelled"):
        # The lock is the RUN's now, so record the lease it is held under:
        # the writer started below is the only thing that may release it, and a
        # later chat's writer for the same run must not be able to.
        take_device_lock(run_id, lease=token)
    if not moved.get("relabelled"):
        # THE HAND-OFF IS CHECKED. It used to return True unconditionally, so a
        # failed relabel left the caller's `finally` skipped and the lock held
        # under a label nothing would ever release again -- while the run drove
        # the device owning nothing. Reporting False hands the device back and
        # refuses the run, which is the truthful answer: this call could not
        # take ownership of the device it was about to drive.
        logger.warning(
            "mcp mobile: could not hand the emulator lock to %s (%s)",
            run_id,
            moved.get("reason") or "no reason given",
        )
        return mobile_render.device_busy_block(
            {
                # The real owner LABEL, which `relabel` now returns. The
                # reason string used to go here and the reply then told the
                # tester to pass it as a `run_id`.
                "holder": str(moved.get("holder") or ""),
                "same_process": True,
                "reason": "handoff_failed",
            }
        )
    return ""


async def _mobile_first_stage_notes(run_id: str) -> tuple[str, str, str]:
    """The notes of the three pre-dump device stages: ``(a11y, animations, keyboard)``."""
    # THE KEYBOARD, BEFORE THE FIRST DUMP EXISTS. This is the last point at
    # which the run holds the device and no screen has been handed to the model
    # yet -- `_mobile_next` below produces that screen. Doing it here rather
    # than at the first `type` op closes a window rather than a bug: selecting
    # an input method can CHANGE THE SCREEN (the old keyboard goes, ours may
    # come up), so an ensure that runs mid-run replays a plan against a screen
    # that is no longer the one it was planned on.
    #
    # Not fatal. Typing may be impossible while tapping and asserting are fine,
    # and refusing a whole run for that would be worse than the defect -- so the
    # outcome is REPORTED and the per-case hook still refuses the individual
    # case that types.
    #
    # ANIMATIONS OFF, BEFORE THE FIRST DUMP for the same reason: the first
    # screen handed to the model is the first one that must be settled. The two
    # stages run TOGETHER (S13): they touch different settings, and neither
    # raises, so neither can cancel the other.
    keyboard_note, animations_note = await asyncio.gather(
        _mobile_keyboard_stage(run_id), _mobile_animations_stage(run_id)
    )
    # After the gather, not inside it: a parity dump taken mid IME switch would
    # fail for the wrong reason.
    a11y_note = await _mobile_a11y_stage(run_id)
    return a11y_note, animations_note, keyboard_note


async def _mobile_keyboard_stage(run_id: str) -> str:
    """Sweep a crashed run's keyboard, make ours ready, and say what happened.

    Returns markdown for the run's FIRST reply, or ``""`` when there is nothing
    a tester needs to know -- which is the ordinary case: a keyboard that was
    already ours, or a sweep that found nothing, is silence.

    Never raises and never stops a run. Both halves are best-effort by design;
    what they must not be is silent about a change they made to the tester's
    device.
    """
    from tools.mobile.device_lock import (
        serial_of,
    )

    lines: list = []
    serial = ""
    try:
        serial = str(serial_of(run_id) or "")
    except Exception:
        logger.debug("mobile keyboard stage: no serial for the run", exc_info=True)
    if not serial:
        return ""
    await _keyboard_sweep_note(serial, run_id, lines)
    await _keyboard_ready_note(serial, run_id, lines)
    return "\n\n".join(lines)


async def _keyboard_sweep_note(serial: str, run_id: str, lines: list) -> None:
    """Restore a crashed run's keyboard; append a note to *lines* if it matters."""
    from tools.mobile import ime_session

    try:
        # THE CRASHED RUN'S KEYBOARD FIRST. Doing it after our own ensure would
        # find a device already on the QA keyboard and record THAT as the thing
        # to go back to -- the tester would keep it forever, which is the defect
        # being fixed, reproduced by the fix.
        swept = (await ime_session.restore_stale(serial, skip_run_id=run_id) or {}).get(
            "content"
        ) or {}
        # A SWEEP THAT TRIED AND FAILED IS REPORTED TOO. It used to be silent,
        # which left the one person who could fix it in Settings unaware there
        # was anything to fix. A sweep that found nothing to do stays silent --
        # that is the ordinary case and a note on every run start is noise.
        if swept.get("restored"):
            lines.append("\u2139\ufe0f " + str(swept.get("detail") or "")[:300])
        elif swept.get("previous"):
            lines.append("\u26a0\ufe0f " + str(swept.get("detail") or "")[:300])
    except Exception:
        logger.debug("mobile keyboard stage: stale sweep skipped", exc_info=True)


async def _keyboard_ready_note(serial: str, run_id: str, lines: list) -> None:
    """Make our keyboard ready; append the fallback notice to *lines* on error."""
    from tools.mobile import ime_session

    try:
        ready = await ime_session.ensure_ready(serial, run_id)
        if ready.get("error"):
            # R1d: the run CONTINUES. Typing falls back to `adb shell input text`
            # and this says so, why, and the exact fix (the run is not blocked).
            from tools.mobile import ime as mobile_ime

            lines.append(
                "\u26a0\ufe0f "
                + mobile_ime.fallback_notice(str(ready["error"])[:300], serial)
            )
    except Exception:
        logger.debug("mobile keyboard stage: ensure skipped", exc_info=True)


async def _mobile_a11y_stage(run_id: str) -> str:
    """Give back a crashed run's accessibility settings, enable the fast dump, say what happened.

    ``""`` on success AND on a legacy APK (nothing to say). A failure is one line: the
    run carries on with uiautomator. Never raises.
    """
    from tools.mobile import a11y
    from tools.mobile.device_lock import (
        serial_of,
    )

    lines: list = []
    serial = ""
    try:
        serial = str(serial_of(run_id) or "")
    except Exception:
        logger.debug("mobile a11y stage: no serial for the run", exc_info=True)
    if not serial:
        return ""
    try:
        swept = (await a11y.restore_stale(serial, skip_run_id=run_id) or {}).get(
            "content"
        ) or {}
        if swept.get("detail"):
            mark = "\u2139\ufe0f " if swept.get("restored") else "\u26a0\ufe0f "
            lines.append(mark + str(swept["detail"])[:300])
    except Exception:
        logger.debug("mobile a11y stage: stale sweep skipped", exc_info=True)
    try:
        on = await a11y.enable(serial, run_id)
        if on.get("error"):
            lines.append(
                "\u26a0\ufe0f Fast screen reading is off for this run ("
                + str(on["error"])[:200]
                + "); using uiautomator, which is slower."
            )
    except Exception:
        logger.debug("mobile a11y stage: enable skipped", exc_info=True)
    return "\n\n".join(lines)


async def _mobile_animations_stage(run_id: str) -> str:
    """Restore a crashed run's animation scales, turn ours off, say what happened.

    ``""`` in the ordinary case. Never raises and never stops a run: animations
    left on make steps slower, not wrong, so a failure is reported and the run
    carries on.
    """
    from tools.mobile import animations
    from tools.mobile.device_lock import (
        serial_of,
    )

    lines: list = []
    serial = ""
    try:
        serial = str(serial_of(run_id) or "")
    except Exception:
        logger.debug("mobile animations stage: no serial for the run", exc_info=True)
    if not serial:
        return ""
    try:
        # THE CRASHED RUN'S VALUES FIRST, or our own disable would read 0 and
        # the tester's real scales would never come back.
        swept = (await animations.restore_stale(serial, skip_run_id=run_id) or {}).get(
            "content"
        ) or {}
        if swept.get("detail"):
            mark = "ℹ️ " if swept.get("restored") else "⚠️ "
            lines.append(mark + str(swept["detail"])[:300])
    except Exception:
        logger.debug("mobile animations stage: stale sweep skipped", exc_info=True)
    try:
        off = await animations.disable(serial, run_id)
        if off.get("error"):
            lines.append(
                "⚠️ Could not turn off system animations for this run ("
                + str(off["error"])[:300]
                + "). Steps may be slower to settle; the run continues."
            )
    except Exception:
        logger.debug("mobile animations stage: disable skipped", exc_info=True)
    return "\n\n".join(lines)
