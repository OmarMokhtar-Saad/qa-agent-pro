"""File a finished run's evidence under the app that was tested (B06).

A run pinned at start to an installer, launcher or App Tester package would
title its report, pick its event profile and read its version from that
package. At close the pin is replaced by the explore target or the newest
non-transient screen package. Knows no app.
"""

from __future__ import annotations

from pathlib import Path

from tools.mobile import knowledge_run, run_consts, run_io, run_store, target_pin


def _explore_app(manifest: dict) -> str:
    explore = manifest.get("explore")
    app = explore.get("app") if isinstance(explore, dict) else ""
    return str(app or "").strip()


def _screen_package(run_id: str) -> str:
    """The package of the newest screen of the run (by write time) that is a real app."""
    if not run_io.valid_run_id(run_id):
        return ""
    directory = run_io.run_path(run_id) / run_consts.SCREENS_DIR
    files = sorted(directory.glob("*.json"), key=_written, reverse=True)
    for path in files:
        body = run_io._read_json(path)
        package = str((body or {}).get("package") or "").strip()
        if target_pin.real_app(package):
            return package
    return ""


def _written(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def real_package(run_id: str, manifest: dict) -> str:
    """The app under test: explore.app, else the newest real screen package."""
    app = _explore_app(manifest)
    return app if target_pin.real_app(app) else _screen_package(run_id)


async def refile(run_id: str, manifest: dict) -> dict:
    """Return *manifest* re-pinned to the real app, saved; unchanged when fine."""
    if not target_pin.transient(manifest.get("package")):
        return manifest
    package = real_package(run_id, manifest)
    if not package:
        return manifest
    manifest["package"] = package
    version, _code = await knowledge_run.read_app_version(
        str(manifest.get("serial") or ""), package
    )
    if version:
        manifest["app_version"] = version
    run_store.write_manifest(run_id, manifest)
    return manifest
