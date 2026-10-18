"""Part of the mobile_run split (see handlers/mobile/run.py): app."""

from __future__ import annotations

import tools.mobile.session_device as session_device
from handlers import _common
from handlers.mobile import run_home, run_skipped
from handlers.mobile.run_app_tester import installed_tester
from handlers.mobile.run_install import (
    _app_stage_not_installed,
    _app_stage_open_store,
    _local_apk_stage,
)
from handlers.mobile.run_request import _AppCall, _mobile_ask
from tools.mobile import (
    app_alias,
    app_stage_pick,
    prerun_timing,
    resolve_cache,
    run_store,
)
from tools.mobile import render as mobile_render
from tools.mobile.install_source_infer import infer_install_source


# Notes for ``_installed_package_target`` (moved out of the body to keep it short):
# A REMEMBERED RESOLUTION, tried before the raw hint. A rerun of the
# same goal used to re-send the same free-text app name and re-walk
# the fuzzy-match path every time; a cache hit here is still VERIFIED
# by the same `session.install_state` probe every other path below
# takes -- this only skips guessing the bundle id again, never the
# "is it actually installed" check.
#
# RESOLVE-OR-ASK over what is installed on THIS device; an empty
# outcome keeps the old raw-hint path (and its suggestions).
# Notes for ``_installed_package_target`` (moved out of the body to keep it short):
#
#     The remembered resolution comes before the picker; a non-empty ``menu``
#     means the picker needs the tester's answer and the caller returns it.
#
async def _installed_package_target(call: _AppCall) -> tuple:
    """Resolve the package for the ``installed_package`` source: ``(target, menu)``."""

    value = call.value
    # PER SERIAL: a package chosen on one device is never replayed on another.
    answer_key = resolve_cache.cache_key(call.serial, value) if value else ""
    cached_target = run_store.read_resolved_app(answer_key) if value else {}
    target = str(cached_target.get("package") or "")
    if target:
        return target, ""

    opts = app_stage_pick.StageOpts(ask=_mobile_ask(call.choose))
    picked = await app_stage_pick.pick_installed(call.serial, value, opts)
    if picked.menu:
        return "", picked.menu
    if picked.said:
        await _common.emit(call.progress, picked.said)
    return picked.package or value, ""


def _remember_installed(call: _AppCall, target: str) -> None:
    """Remember *target* as the resolution of the app hint, when there is one."""

    if call.value:
        app_alias.remember_alias(call.serial, call.value, target)


def _note_skipped_install(call: _AppCall, target: str) -> None:
    """Remember an install the goal or source asked for but the probe skipped."""
    label = mobile_render.install_source_for_label(str(call.source or "").strip())
    asked = label or _inferred_source(call)
    if asked not in ("", "installed_package"):
        run_skipped.mark_skipped(call.serial, target, asked)


def _probe_outcome(call: _AppCall, target: str, body: dict) -> tuple | None:
    """The finished ``(markdown, package)`` pair for an installed or pending app."""
    if body.get("installed"):
        _remember_installed(call, target)
        _note_skipped_install(call, target)
        return "", target
    if body.get("pending"):
        return (
            "## The install is still running\n\n`"
            + target
            + "` has not appeared on the device yet. Nothing in this call "
            "is waiting on it — call `qa_mobile_status` in a few seconds.",
            target,
        )
    return None


# Notes for ``_app_stage_probe`` (moved out of the body to keep it short):
# WHETHER THE DEVICE ANSWERED, read from the one witness that knows:
# `session.install_state` publishes `probed` on its CONTENT, not on the
# envelope's `error` (set only in a defensive branch, so it read True on
# exactly the adb failure it was written to catch). `install_state` runs
# only for a non-empty target, so a first call leaves this False.
# Notes for ``_app_stage_probe`` (moved out of the body to keep it short):
#
#     ``done`` is the finished ``(markdown, package)`` pair when the app is
#     already installed or its install is still running, else ``None``.
#
async def _app_stage_probe(call: _AppCall, target: str) -> tuple:
    """Probe the device for *target*: ``(done_or_None, probed, suggestions)``."""

    if not target:
        return None, False, []
    state = await session_device.install_state(call.serial, target)
    body = state.get("content") or {}
    probed = bool(body.get("probed"))
    suggestions = [str(s) for s in (body.get("suggestions") or [])][:5]
    return _probe_outcome(call, target, body), probed, suggestions


async def _app_stage_remembered(call: _AppCall) -> tuple[str, str]:
    """The remembered package for the app hint: ``(menu_or_empty, target)``."""
    # A REMEMBERED RESOLUTION, tried before the raw hint; a cache hit is
    # still VERIFIED by `session.install_state`, never skipped.
    target, menu = await _installed_package_target(call)
    return menu, target


def _inferred_source(call: _AppCall) -> str:
    """The source named by the app text, or by the goal for the lanes that need no value.

    A link or path found only in the goal is never opened (``call.value`` is), so
    ``download_url`` and ``local_apk`` are inferred from ``app`` alone.
    """
    from_app = infer_install_source(call.value)
    if from_app:
        return from_app
    from_goal = infer_install_source(call.source_text)
    return "" if from_goal in ("download_url", "local_apk") else from_goal


async def _only_viable_source(call: _AppCall, target: str) -> str:
    """``app_tester`` when App Tester is installed on the device and nothing else
    names a source: the one route that needs no link, file or store login, so the
    start does not stop to ask for it. ``""`` when it is not installed, or when no
    app is named yet (the menu asks which app, so the device is not probed)."""
    if not target:
        return ""
    return "app_tester" if await installed_tester(call.serial) else ""


async def _app_stage_install(call: _AppCall, st: tuple, probe: tuple) -> tuple:
    """The install branches once the probe found no finished answer."""

    install_source, target = st
    probed, suggestions = probe
    # An empty source is inferred from the app text; ask only if that is unclear.
    install_source = install_source or _inferred_source(call)
    install_source = install_source or await _only_viable_source(call, target)
    if not install_source:
        return mobile_render.install_menu_markdown(target, probed=probed), target
    if not call.apply:
        refusal = "Installing the app (" + install_source + ")"
        return mobile_render.apply_refusal(refusal, call.value), target
    if install_source == "local_apk":
        return await _local_apk_stage(call, target)
    if install_source == "installed_package":
        return await _app_stage_not_installed(call, target, (probed, suggestions))
    return await _app_stage_open_store(call, install_source, target)


# Notes for ``_mobile_app_stage`` (moved out of the body to keep it short):
# A KEY only. This used to try a POSITIONAL lookup first, and the start menu
# shares this argument, so a start-menu answer of "3" resolved here to
# `app_tester`.
#
#     The resolved package comes back because the ``installed_package`` source
#     carries it in ``app`` rather than in ``package``: deriving it twice is how
#     the preflight ends up checking a different app from the one installed.
#     Every branch that touches the device needs ``apply=true``, and the refusal
#     names the step. The Play-Store branches only OPEN a URL inside the emulator;
#     Firebase App Tester is never redistributed by this project.
#
async def _mobile_app_stage(call: _AppCall) -> tuple:
    """Get the app under test onto the device. ``(markdown_or_empty, package)``."""

    await run_home.begin_stage(call.serial, call.goal)
    label = str(call.source or "").strip()
    install_source = mobile_render.install_source_for_label(label)
    target = call.typed_package
    if not target and install_source == "installed_package":
        menu, target = await _app_stage_remembered(call)
        if menu:
            return menu, ""
    done, probed, suggestions = await _app_stage_probe(call, target)
    if done is not None:
        return done
    stage = _app_stage_install(call, (install_source, target), (probed, suggestions))
    return await prerun_timing.timed(call.serial, "install", stage)
