"""Moved from handlers (refactor P2): mobile_content."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Callable

from handlers import _common, mobile_gate, mobile_run, mobile_step

logger = logging.getLogger("tools.mcp_handlers")


async def handle_mobile_notes(
    action: str = "list",
    package: str = "",
    run_id: str = "",
    note_id: int = 0,
    reason: str = "",
) -> str:
    """``qa_mobile_notes``: list or retire the notes saved about one app.

    A thin alias kept for one release; ``qa_mobile_knowledge`` is the full tool
    (``handle_mobile_knowledge``). Output is unchanged. Never raises."""
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.mobile import knowledge_tool

        verb = str(action or "list").strip().lower()
        if verb not in ("list", "retire"):
            return "\u26a0\ufe0f `action` must be `list` or `retire`."
        pkg, rid, refusal = mobile_step._mobile_note_target(run_id, package)
        if refusal:
            return "\u26a0\ufe0f " + refusal + "."
        if verb == "list":
            # SQLite with a busy timeout: off the event loop, like the note intake.
            return await asyncio.to_thread(knowledge_tool.notes_list, pkg)
        if not note_id:
            return "\u26a0\ufe0f `retire` needs `note_id`."
        return await asyncio.to_thread(
            knowledge_tool.notes_retire, pkg, note_id, str(reason or ""), rid
        )
    except Exception:
        logger.exception("mobile notes failed")
        return "\u26a0\ufe0f The notes store could not be read."


async def handle_mobile_knowledge(
    action: str = "list",
    package: str = "",
    item_id: int | str = 0,
    run_id: str = "",
    data: dict | None = None,
) -> str:
    """``qa_mobile_knowledge``: one app's saved knowledge. A shim over
    ``tools.mobile.knowledge_tool.handle``. Never raises."""
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.mobile import knowledge_tool

        pkg, _rid, refusal = mobile_step._mobile_note_target(run_id, package)
        if refusal:
            return "\u26a0\ufe0f " + refusal + "."
        return await asyncio.to_thread(
            knowledge_tool.handle,
            action,
            pkg,
            item_id,
            run_id,
            data,
        )
    except Exception:
        logger.exception("mobile knowledge failed")
        return "\u26a0\ufe0f The knowledge store could not be read."


def _mobile_arg(args: tuple, kwargs: dict, name: str):
    """*name*'s RAW value in a ``handle_mobile_test`` call, however it was passed.

    Unlike ``_call_arg`` (which returns ``str``) the type survives, so
    ``apply=False`` stays ``False`` instead of becoming the truthy ``"False"``.
    Found by the handler's own signature, not by position. ``None`` when absent.
    """
    if name in kwargs:
        return kwargs[name]

    names = list(inspect.signature(mobile_run.handle_mobile_test).parameters)
    index = names.index(name)
    return args[index] if index < len(args) else None


async def _mobile_avd_reply(
    action: str, args: tuple, kwargs: dict, system_image: str, confirm: object
) -> str:
    """``qa_mobile_test(emulator=...)``: list, boot, create or delete an AVD. Never raises.

    The lane gate comes first. The logic lives in ``tools/mobile/avd_manage``
    (function-local import, like every ``tools.mobile`` module here). ``apply``
    and ``avd`` are read from the ORIGINAL call; ``confirm`` reaches the module
    only as the literal ``True``.
    """
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.mobile import avd_manage

        apply = _mobile_arg(args, kwargs, "apply") is True
        avd = str(_mobile_arg(args, kwargs, "avd") or "").strip()
        await _common._audit(
            "mcp_mobile_avd",
            detail={
                "action": action[:12],
                "avd": avd[:64],
                "apply": apply,
                "confirmed": confirm is True,
            },
        )
        return await avd_manage.manage(
            action,
            avd=avd,
            system_image=system_image,
            apply=apply,
            confirm_destructive=confirm is True,
        )
    except Exception:
        logger.exception("mobile avd action failed")
        return "\u26a0\ufe0f The emulator action failed; nothing further was run."


def _mobile_parse_env(raw: object) -> tuple:
    """``(env, error)``; the refusal never repeats the value it refused."""
    try:
        from tools.mobile import knowledge_run

        return knowledge_run.parse_env_checked(raw)
    except mobile_step._MOBILE_TOOL_FAILURES:
        return {}, "env could not be read"


async def _mobile_note_intake_line(args: tuple, kwargs: dict, note: object) -> str:
    """The note-intake line for a ``mobile_test`` call (empty when no note)."""
    # The store is SQLite with a busy timeout: off the event loop. Values
    # the tester typed into an existing run are refused through run_id
    # (held inputs); this tool takes no tester value of its own.
    return await asyncio.to_thread(
        mobile_step.mobile_note_intake,
        note,
        run_id=mobile_step._call_arg(
            mobile_run.handle_mobile_test, args, kwargs, "run_id"
        ),
        package=mobile_step._call_arg(
            mobile_run.handle_mobile_test, args, kwargs, "package"
        )
        or mobile_step._call_arg(mobile_run.handle_mobile_test, args, kwargs, "app"),
    )


async def handle_mobile_test_content(*args, **kwargs) -> tuple:
    """``handle_mobile_test``'s text PLUS the image specs of the screens it took.

    ``(markdown, [{filename, mime, data}, ...])`` -- the shape
    ``mcp_server._image_content_blocks`` consumes, and the shape
    ``handle_capture_screens`` already returns. THE entry point for the tool
    layer; the ``str``-returning name stays exactly what it was for every other
    caller and is this function's text, projected.

    Opening the sink is what ARMS the capture: see ``_MOBILE_IMAGE_SPECS``.

    ``emulator`` (list/boot/create/delete an AVD) is answered BEFORE the sink
    opens: it is not a run step, takes no screenshot and shares no state with one.
    """
    avd_action = str(kwargs.pop("emulator", "") or "").strip()
    system_image = str(kwargs.pop("system_image", "") or "")
    confirm = kwargs.pop("confirm_destructive", False)
    if avd_action:
        reply = await _mobile_avd_reply(avd_action, args, kwargs, system_image, confirm)
        return reply, []
    run_env, env_error = _mobile_parse_env(kwargs.pop("env", ""))
    if env_error:
        return "Not started: " + env_error + ".", []
    want = mobile_step._MOBILE_SHOT_WANT.set(
        {"asked": bool(kwargs.pop("screenshot", False))}
    )
    token = mobile_step._MOBILE_IMAGE_SPECS.set([])
    env_token = mobile_step._MOBILE_RUN_ENV.set(run_env)
    try:
        note = kwargs.pop("note", "")
        note_line = await _mobile_note_intake_line(args, kwargs, note)
        text = note_line + await mobile_run.handle_mobile_test(*args, **kwargs)
        return text, list(mobile_step._MOBILE_IMAGE_SPECS.get() or [])
    finally:
        mobile_step._MOBILE_RUN_ENV.reset(env_token)
        mobile_step._MOBILE_IMAGE_SPECS.reset(token)
        mobile_step._MOBILE_SHOT_WANT.reset(want)


_FLOW_WARN = chr(0x26A0) + chr(0xFE0F) + " "


def _mobile_flow_names(args: tuple, kwargs: dict) -> set:
    """Secret FIELD NAMES the tester supplied on this call or holds for the run. Names only."""
    from tools.mobile import held_inputs

    names: set = set()
    field = mobile_step._call_arg(
        mobile_step.handle_submit_mobile_step, args, kwargs, "tester_input_field"
    ).strip()
    if field:
        names.add(field)
    try:
        loaded = json.loads(
            mobile_step._call_arg(
                mobile_step.handle_submit_mobile_step, args, kwargs, "tester_inputs"
            )
            or "{}"
        )
        if isinstance(loaded, dict):
            names.update(str(key) for key in loaded)
    except Exception:
        logger.debug("tester_inputs arg unreadable", exc_info=True)
    try:
        run_id = mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, args, kwargs, "run_id"
        )
        names.update(str(key) for key in held_inputs.recall(run_id))
    except Exception:
        logger.debug("held tester inputs unreadable", exc_info=True)
    return names


def _with_script(args: tuple, kwargs: dict, script: str) -> tuple:
    """``(args, kwargs)`` with the script slot (third positional, or the keyword) replaced."""
    if len(args) > 2:
        return args[:2] + (script,) + args[3:], kwargs
    return args, {**kwargs, "script": script}


def _flow_prepare_checks(name: str, save_name: str, script: str) -> str:
    """The refusal text for a flow call whose arguments contradict, or ``""``."""
    if name and save_name:
        return "Pass `flow` (replay a saved flow) OR `save_flow` (save this script as one), not both."
    if name and script.strip():
        return "`flow` replays a saved flow; leave `script` empty."
    if save_name and not script.strip():
        return "`save_flow` saves this call's `script`; pass one."
    return ""


def _mobile_prepare_target(
    args: tuple, kwargs: dict, raw_params: object, label: str
) -> tuple[str, object, str]:
    """``(package, params, refusal)`` for a flow or route call; *label* is the noun."""
    pkg, _rid, refusal = mobile_step._mobile_note_target(
        mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, args, kwargs, "run_id"
        ),
        "",
    )
    if refusal:
        return "", {}, label + " not run: " + refusal + "."
    params: object = {}
    if str(raw_params or "").strip():
        try:
            params = json.loads(str(raw_params))
        except ValueError:
            return pkg, {}, "`flow_params` is not valid JSON."
    return pkg, params, ""


def _mobile_flow_prepare(
    args: tuple, kwargs: dict, name: str, raw_params: object, save_name: str
) -> dict:
    """Turn a flow call into the script to replay, or ``{'refusal': text}`` when nothing may
    run. Every refusal comes BEFORE the device is touched. Never raises."""
    from tools.mobile import app_flows
    from tools.untrusted import single_line

    def stop(why: str) -> dict:
        return {"refusal": _FLOW_WARN + single_line(why, 400) + " Nothing was touched."}

    try:
        script = mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, args, kwargs, "script"
        )
        why = _flow_prepare_checks(name, save_name, script)
        if why:
            return stop(why)
        pkg, params, why = _mobile_prepare_target(args, kwargs, raw_params, "Flow")
        if why:
            return stop(why)
        got = (
            app_flows.get_flow(pkg, name)
            if name
            else app_flows.template_from_script(script)
        )
        if not name and not app_flows.valid_name(save_name):
            return stop("`save_flow` is not a valid flow name.")
        if got.get("error"):
            return stop("Flow not run: " + str(got["error"]) + ".")
        template = got["content"]
        if save_name:
            problem = app_flows.check_save(pkg, save_name, template)
            if problem:
                return stop("Flow not run: " + problem + ".")
        built = app_flows.expand(template, params, _mobile_flow_names(args, kwargs))
        if built.get("error"):
            return stop("Flow not run: " + str(built["error"]) + ".")
        return {
            "script": built["content"],
            "name": name or save_name,
            "package": pkg,
            "save": bool(save_name),
            "steps": len(template["steps"]),
            "template": template,
        }
    except Exception:
        logger.exception("mobile flow: prepare failed")
        return stop("Flow not run: the flow store is unavailable.")


def _mobile_flow_finish(plan: dict, held: dict) -> str:
    """The line a flow call prepends to the step reply. Saves a new flow ONLY after a clean
    replay. Never raises."""
    from tools.mobile import app_flows
    from tools.untrusted import single_line

    try:
        count = int(plan.get("steps") or 0)
        case = held.get("case") if isinstance(held.get("case"), dict) else {}
        clean = bool(held.get("held")) and app_flows.clean_replay(case, count)
        if plan.get("save"):
            if not held.get("held"):
                return "Flow not saved: this call did not run a step.\n\n"
            if not clean:
                return (
                    "Flow `%s` not saved: the replay did not finish cleanly.\n\n"
                    % plan["name"]
                )
            saved = app_flows.save_flow(plan["package"], plan["name"], plan["template"])
            if saved.get("error"):
                return "Flow not saved: " + single_line(saved["error"], 300) + "\n\n"
            return "Saved flow `%s` for `%s` (%d steps).\n\n" % (
                plan["name"],
                plan["package"],
                count,
            )
        if not held.get("held") or clean:
            return ""
        return app_flows.stop_line(plan["name"], case, count) + "\n\n"
    except Exception:
        logger.exception("mobile flow: finish failed")
        return ""


def _route_prepare_checks(name: str, save_name: str, script: str) -> str:
    """The refusal text for a route call whose arguments contradict, or ``""``."""
    from tools.mobile import app_flows

    if name and save_name:
        return "Pass `route` (replay a saved route) OR `save_route` (save this script as one), not both."
    if name and script.strip():
        return "`route` replays a saved route; leave `script` empty."
    if save_name and not script.strip():
        return "`save_route` saves this call's `script`; pass one."
    if save_name and not app_flows.valid_name(save_name):
        return "`save_route` is not a valid route name."
    return ""


def _mobile_route_prepare(
    args: tuple, kwargs: dict, name: str, raw_params: object, save_name: str
) -> dict:
    """Turn a route call into the script to replay, or ``{'refusal': text}`` when nothing may
    run. Every refusal comes BEFORE the device is touched. Never raises. A replay carries the
    saved screens as ``expect``; a save carries none (it records them from the trace)."""
    from tools.mobile import app_flows, app_routes
    from tools.untrusted import single_line

    def stop(why: str) -> dict:
        return {"refusal": _FLOW_WARN + single_line(why, 400) + " Nothing was touched."}

    try:
        script = mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, args, kwargs, "script"
        )
        why = _route_prepare_checks(name, save_name, script)
        if why:
            return stop(why)
        pkg, params, why = _mobile_prepare_target(args, kwargs, raw_params, "Route")
        if why:
            return stop(why)
        got = (
            app_routes.get_route(pkg, name)
            if name
            else app_flows.template_from_script(script)
        )
        if got.get("error"):
            return stop("Route not run: " + str(got["error"]) + ".")
        template = got["content"]
        if save_name:
            problem = app_routes.check_save(pkg, save_name, template)
            if problem:
                return stop("Route not run: " + problem + ".")
        built = app_flows.expand(template, params, _mobile_flow_names(args, kwargs))
        if built.get("error"):
            return stop("Route not run: " + str(built["error"]) + ".")
        return {
            "script": built["content"],
            "name": name or save_name,
            "package": pkg,
            "save": bool(save_name),
            "steps": len(template["steps"]),
            "template": template,
            "expect": [] if save_name else list(template["screens"]),
            "end": "" if save_name else str(template["end"]),
        }
    except Exception:
        logger.exception("mobile route: prepare failed")
        return stop("Route not run: the route store is unavailable.")


def _mobile_route_finish(plan: dict, held: dict) -> str:
    """The line a route call prepends to the step reply. Saves a new route ONLY after a clean
    replay whose every step recorded the screens it started and ended on. Never raises."""
    from tools.mobile import app_routes
    from tools.untrusted import single_line

    try:
        count = int(plan.get("steps") or 0)
        case = held.get("case") if isinstance(held.get("case"), dict) else {}
        name = plan["name"]
        if plan.get("save"):
            if not held.get("held"):
                return "Route not saved: this call did not run a step.\n\n"
            recorded = app_routes.record_from_case(case, count)
            if recorded.get("error"):
                return "Route `%s` not saved: %s.\n\n" % (
                    name,
                    single_line(recorded["error"], 300),
                )
            seen = recorded["content"]
            saved = app_routes.save_route(
                plan["package"], name, plan["template"], seen["screens"], seen["end"]
            )
            if saved.get("error"):
                return "Route not saved: " + single_line(saved["error"], 300) + "\n\n"
            return "Saved route `%s` for `%s` (%d steps).\n\n" % (
                name,
                plan["package"],
                count,
            )
        if not held.get("held"):
            return ""
        if app_routes.replay_matched(case, count, plan.get("expect"), plan.get("end")):
            return (
                "Route `%s` replayed: every screen matched the saved route (%d steps).\n\n"
                % (name, count)
            )
        return app_routes.stop_line(name, case) + "\n\n"
    except Exception:
        logger.exception("mobile route: finish failed")
        return ""


async def _mobile_routes_manage(verb: str, pkg: str, name: str) -> str:
    """``qa_mobile_flows`` with ``kind='route'``: list, show or delete one app's routes."""
    from tools.mobile import app_routes
    from tools.untrusted import single_line

    if verb == "list":
        listed = await asyncio.to_thread(app_routes.list_routes, pkg)
        rows = listed.get("content") or []
        if not rows:
            return "No saved routes for `%s`." % pkg
        lines = ["Saved routes for `%s`:" % pkg]
        for row in rows:
            lines.append(
                "- `%s`: %d steps; params: %s; secret fields: %s"
                % (
                    row["name"],
                    row["steps"],
                    ", ".join(row["params"]) or "none",
                    ", ".join(row["fields"]) or "none",
                )
            )
        return "\n".join(lines)
    if verb == "show":
        got = await asyncio.to_thread(app_routes.get_route, pkg, name)
        if got.get("error"):
            return _FLOW_WARN + single_line(got["error"], 300) + "."
        route = got["content"]
        lines = ["Route `%s` for `%s`:" % (name, pkg)]
        for index, step in enumerate(route["steps"], 1):
            lines.append(
                "%d. %s (starts on screen %s)"
                % (
                    index,
                    single_line(json.dumps(step, sort_keys=True), 300),
                    route["screens"][index - 1],
                )
            )
        lines.append("Ends on screen %s." % route["end"])
        return "\n".join(lines)
    gone = await asyncio.to_thread(app_routes.delete_route, pkg, name)
    if gone.get("error"):
        return _FLOW_WARN + single_line(gone["error"], 300) + "."
    return "Deleted route `%s` for `%s`." % (name, pkg)


async def _mobile_flows_action(verb: str, pkg: str, name: str) -> str:
    """Run one validated ``qa_mobile_flows`` flow action: list, show or delete."""
    from tools.mobile import app_flows
    from tools.untrusted import single_line

    if verb == "list":
        listed = await asyncio.to_thread(app_flows.list_flows, pkg)
        rows = listed.get("content") or []
        if not rows:
            return "No saved flows for `%s`." % pkg
        lines = ["Saved flows for `%s`:" % pkg]
        for row in rows:
            lines.append(
                "- `%s`: %d steps; params: %s; secret fields: %s"
                % (
                    row["name"],
                    row["steps"],
                    ", ".join(row["params"]) or "none",
                    ", ".join(row["fields"]) or "none",
                )
            )
        return "\n".join(lines)
    if verb == "show":
        got = await asyncio.to_thread(app_flows.get_flow, pkg, name)
        if got.get("error"):
            return _FLOW_WARN + single_line(got["error"], 300) + "."
        lines = ["Flow `%s` for `%s`:" % (name, pkg)]
        for index, step in enumerate(got["content"]["steps"], 1):
            lines.append(
                "%d. %s" % (index, single_line(json.dumps(step, sort_keys=True), 300))
            )
        return "\n".join(lines)
    gone = await asyncio.to_thread(app_flows.delete_flow, pkg, name)
    if gone.get("error"):
        return _FLOW_WARN + single_line(gone["error"], 300) + "."
    return "Deleted flow `%s` for `%s`." % (name, pkg)


async def handle_mobile_flows(
    action: str = "list",
    package: str = "",
    run_id: str = "",
    name: str = "",
    kind: str = "flow",
) -> str:
    """``qa_mobile_flows``: list, show or delete the flows (``kind='route'``: the routes) saved
    for one app. Never raises."""
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.untrusted import single_line

        verb = str(action or "list").strip().lower()
        if verb not in ("list", "show", "delete"):
            return _FLOW_WARN + "`action` must be `list`, `show` or `delete`."
        pkg, _rid, refusal = mobile_step._mobile_note_target(run_id, package)
        if refusal:
            return _FLOW_WARN + single_line(refusal, 300) + "."
        kind_name = str(kind or "flow").strip().lower()
        if kind_name not in ("flow", "route"):
            return _FLOW_WARN + "`kind` must be `flow` or `route`."
        if kind_name == "route":
            return await _mobile_routes_manage(verb, pkg, name)
        return await _mobile_flows_action(verb, pkg, name)
    except Exception:
        logger.exception("mobile flows failed")
        return _FLOW_WARN + "the flow store is unavailable."


def _pop_flow_route_args(kwargs: dict) -> tuple:
    """Pop the flow/route/finding kwargs, in the order the tool reads them."""
    flow_name = str(kwargs.pop("flow", "") or "").strip()
    flow_params = kwargs.pop("flow_params", "")
    save_flow = str(kwargs.pop("save_flow", "") or "").strip()
    route_name = str(kwargs.pop("route", "") or "").strip()
    save_route = str(kwargs.pop("save_route", "") or "").strip()
    finding = str(kwargs.pop("finding", "") or "").strip()
    return flow_name, flow_params, save_flow, route_name, save_route, finding


async def _mobile_plan_flow_route(
    args: tuple, kwargs: dict, flow: tuple, route: tuple
) -> tuple[dict, dict, tuple, dict, tuple | None]:
    """Plan the flow, then the route; ``refusal`` is the reply tuple of the
    first plan that refused (the route is not planned after a flow refusal)."""
    flow_plan, args, kwargs = await _mobile_plan_one(
        _mobile_flow_prepare, args, kwargs, flow
    )
    if flow_plan.get("refusal"):
        return flow_plan, {}, args, kwargs, (flow_plan["refusal"], [])
    route_plan, args, kwargs = await _mobile_plan_one(
        _mobile_route_prepare, args, kwargs, route
    )
    if route_plan.get("refusal"):
        return flow_plan, route_plan, args, kwargs, (route_plan["refusal"], [])
    return flow_plan, route_plan, args, kwargs, None


async def handle_submit_mobile_step_content(*args, **kwargs) -> tuple:
    """``handle_submit_mobile_step``'s text plus its screens. See above.

    The ``note`` is saved AFTER the step and only when the step held its run
    (``_MOBILE_STEP_HELD``): a refused or raising call leaves no note.

    ``flow`` / ``flow_params`` / ``save_flow`` are popped and resolved into the script BEFORE
    the step, so a refused flow call never reaches the device."""
    flow_name, flow_params, save_flow, route_name, save_route, finding = (
        _pop_flow_route_args(kwargs)
    )
    if (route_name or save_route) and (flow_name or save_flow):
        return _FLOW_WARN + "Pass a flow OR a route, not both. Nothing was touched.", []
    flow_plan, route_plan, args, kwargs, refusal = await _mobile_plan_flow_route(
        args,
        kwargs,
        (flow_name, flow_params, save_flow),
        (route_name, flow_params, save_route),
    )
    if refusal is not None:
        return refusal
    args, kwargs, finding_note = await _mobile_fold_finding(args, kwargs, finding)
    want = mobile_step._MOBILE_SHOT_WANT.set(
        {"asked": bool(kwargs.pop("screenshot", False))}
    )
    token = mobile_step._MOBILE_IMAGE_SPECS.set([])
    held = {"held": False, "route_expect": list(route_plan.get("expect") or [])}
    held_token = mobile_step._MOBILE_STEP_HELD.set(held)
    try:
        note = kwargs.pop("note", "")
        kwargs.pop("env", None)
        run_id = mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, args, kwargs, "run_id"
        )
        # Copied BEFORE the step: a step that reaches the report forgets the
        # run's held values, and the note must still be checked against them.
        earlier = (
            mobile_step._mobile_held_values(run_id) if str(note or "").strip() else []
        )
        body = await mobile_step.handle_submit_mobile_step(*args, **kwargs)
        note_line = await _mobile_note_line(held, note, earlier, args, kwargs)
        plan_lines = await _mobile_plan_lines(flow_plan, route_plan, held)
        return finding_note + plan_lines + note_line + body, list(
            mobile_step._MOBILE_IMAGE_SPECS.get() or []
        )
    finally:
        mobile_step._MOBILE_STEP_HELD.reset(held_token)
        mobile_step._MOBILE_IMAGE_SPECS.reset(token)
        mobile_step._MOBILE_SHOT_WANT.reset(want)


async def _mobile_plan_one(
    prepare: Callable, args: tuple, kwargs: dict, spec: tuple
) -> tuple[dict, tuple, dict]:
    """Run one flow/route *prepare* for ``spec = (name, params, save_name)``.

    Returns ``(plan, args, kwargs)``: ``{}`` when neither name was given, the
    refusal plan untouched, else the plan with its script spliced into the call."""
    name, params, save_name = spec
    if not (name or save_name):
        return {}, args, kwargs
    plan = await asyncio.to_thread(prepare, args, kwargs, name, params, save_name)
    if plan.get("refusal"):
        return plan, args, kwargs
    args, kwargs = _with_script(args, kwargs, plan["script"])
    return plan, args, kwargs


async def _mobile_note_line(
    held: dict, note: object, earlier: list, args: tuple, kwargs: dict
) -> str:
    """The line a ``note`` adds to the step reply: saved, refused, or empty."""
    if held["held"]:
        run_id = mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, args, kwargs, "run_id"
        )
        return await asyncio.to_thread(
            mobile_step.mobile_note_intake,
            note,
            run_id=run_id,
            secrets=earlier
            + mobile_step._mobile_call_secrets(
                mobile_step._call_arg(
                    mobile_step.handle_submit_mobile_step, args, kwargs, "tester_input"
                ),
                mobile_step._call_arg(
                    mobile_step.handle_submit_mobile_step, args, kwargs, "tester_inputs"
                ),
            ),
        )
    if str(note or "").strip() and _common._mobile_lane_enabled():
        return "Note not saved: this call did not run a step.\n\n"
    return ""


async def _mobile_plan_lines(flow_plan: dict, route_plan: dict, held: dict) -> str:
    """The route line then the flow line, each empty when its plan is."""
    flow_line = ""
    if flow_plan:
        flow_line = await asyncio.to_thread(_mobile_flow_finish, flow_plan, held)
    route_line = ""
    if route_plan:
        route_line = await asyncio.to_thread(_mobile_route_finish, route_plan, held)
    return route_line + flow_line


def _mobile_merge_finding(args: tuple, kwargs: dict, finding: object) -> tuple:
    """``(args, kwargs, note)`` with ``finding`` folded into the step script.

    Explore lane only (``step_finding.merge_finding``); on any other lane the
    script is untouched and the note says the finding was left out. An
    unreadable run or script is left alone: the step itself reports that."""
    from tools.mobile import actions, run_store, step_finding
    from tools.mobile.session_core import LANE_SUITE

    run_id = mobile_step._call_arg(
        mobile_step.handle_submit_mobile_step, args, kwargs, "run_id"
    )
    manifest = run_store.read_manifest(run_id).get("content")
    script = mobile_step._call_arg(
        mobile_step.handle_submit_mobile_step, args, kwargs, "script"
    )
    payload = actions.decode_reply(script)
    if not isinstance(manifest, dict) or (script.strip() and not payload):
        return args, kwargs, ""
    merged, refusal = step_finding.merge_finding(
        payload, finding, lane=str(manifest.get("lane") or LANE_SUITE)
    )
    if refusal:
        return args, kwargs, refusal + "\n\n"
    if not script.strip():
        merged["actions"] = []
    args, kwargs = _with_script(args, kwargs, json.dumps(merged))
    return args, kwargs, ""


async def _mobile_fold_finding(args: tuple, kwargs: dict, finding: str) -> tuple:
    """``_mobile_merge_finding`` off the event loop; no finding, no change."""
    if not finding:
        return args, kwargs, ""
    return await asyncio.to_thread(_mobile_merge_finding, args, kwargs, finding)
