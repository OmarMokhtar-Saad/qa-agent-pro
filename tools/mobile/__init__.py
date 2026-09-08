"""Mobile emulator testing support for qa-agents.

Package marker. It is here because ``[tool.setuptools.packages.find]`` in
pyproject.toml includes ``tools*``: without this file ``tools.mobile`` is not a
package and nothing in it is importable from an installed build.

Phase 0 of the mobile programme puts exactly one module here -- ``ime_manifest``,
the pinned identity of the QA input method APK. Phase 1 added the platform layer
(paths, platform_info, sdk_locator, downloader, provisioner, adb, emulator, ime,
preflight, run_store, locks) and Phase 2 the execution engine (perception,
actions, executor, case_runner, explore_runner, importers, scheduler), whose
prompt builders live in ``agents/mobile_run.py``. Phase 3 added the MCP surface
(``session`` -- the state machine and all of its on-disk state -- and
``render``, every tester-facing string), reached through
``tools/mcp_handlers.py``'s three ``qa_mobile_*`` handlers. Phase 4 added the
report -- ``report`` (the standalone HTML document, built from one run's own
files and nothing else), ``report_selfcheck`` (its CLI gate) and
``open_report`` -- plus the per-run screen library in ``run_store`` that gives
those wireframes their element bounds. Phase 5 added the Windows path and the hardening: ``heartbeat`` (the lease
heartbeat writer), the per-hop redirect allowlist in ``downloader``, the cache
ownership check in ``paths``, the stale-run collector in ``run_store``, the
bounded call budget and the dead-emulator re-boot in ``session``, and the
no-window / PowerShell-resolution seams in ``platform_info``.

The mobile lane's Windows branches are exercised only by tests that fake the
platform; no Windows machine has run the lane. That sentence is DERIVED from
``platform_info.SUPPORT`` and pinned verbatim in four documents by
``tests/mobile/test_mobile_platform_support.py``, so it cannot drift into
implying a validated Windows lane. ``windows_probe`` collects the host facts a
Windows tester can settle in one command; the acceptance checklist in
``docs/MOBILE_TESTING.md`` carries the rows that still need a chat and a device. This file stays deliberately
empty of code: an import here would run on every ``tools.mobile.*`` import, and
``ime_manifest`` must stay import-free.
"""
