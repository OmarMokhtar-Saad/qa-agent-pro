"""qa-doctor's .env self-heal, flag-expiry and connect-hint items (moved from setup_check)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from config.settings_guard import never_raise
from tools import clock, env_heal

# tools/flag_registry.py is private-repo only (DIST_EXCLUDED): a public dist
# ships without it, so only its own absence reads as "no flag registry".
try:
    import tools.flag_registry as flag_registry
except ModuleNotFoundError as _exc:
    if _exc.name != "tools.flag_registry":
        raise
    flag_registry = cast("Any", None)

logger = logging.getLogger("tools.mcp_handlers")


# Read-only default: look, never write, and stay silent when
# nothing is outdated.
async def _env_heal_fetch(install_dir: Path, fix: bool, recommended: list[str]) -> dict:
    """Heal (fix=True) or only look for (fix=False) superseded .env defaults."""

    if fix:
        _heal = await asyncio.to_thread(env_heal.heal_env, Path(install_dir))
    else:
        _heal = await asyncio.to_thread(env_heal.find_superseded, Path(install_dir))
        if _heal.get("changed"):
            _stale = ", ".join(
                f"`{_key}` (`{_old}` → `{_new}`)"
                for _key, _old, _new, _why in _heal["changed"]
            )
            recommended.append(
                f"Outdated .env setting(s): {_stale}. Call qa-doctor with "
                "fix=true to update them (the old .env is backed up to "
                ".env.bak-*)."
            )
            _heal = {"error": _heal.get("error")}
    return _heal


def _repaired_section(heal: dict, heal_lines: list[str]) -> None:
    heal_lines.append("### Configuration repaired")
    heal_lines.append("")
    for _key, _old, _new, _why in heal["changed"]:
        heal_lines.append(f"- `{_key}`: `{_old}` → `{_new}` — {_why}")
    heal_lines.append("")
    heal_lines.append(
        f"_Backup: `{heal.get('backup') or 'n/a'}`. These take effect "
        "when the MCP server restarts — quit and reopen your editor._"
    )
    heal_lines.append("")


def _env_heal_report(heal: dict, heal_lines: list[str], recommended: list[str]) -> None:
    """Render a repaired .env (or a failed check) into the report lists."""
    if heal.get("changed"):
        _repaired_section(heal, heal_lines)
        recommended.append(
            "Restart the MCP server (quit + reopen your editor) so the "
            f"{len(heal['changed'])} repaired setting(s) take effect."
        )
    elif heal.get("error"):
        recommended.append(
            f"Could not check the .env for stale settings: {heal['error']}"
        )


# Repair superseded .env defaults BEFORE the verdict, so the report
# reflects the file as it now stands. Never fatal: a failure is reported
# as a recommendation and the rest of the check proceeds.
async def _env_heal_lines(
    install_dir: Path, fix: bool, recommended: list[str]
) -> list[str]:
    """Run the .env self-heal step; never fatal, partial lines survive."""
    heal_lines: list[str] = []
    with never_raise(logger, "env self-heal step skipped", exc_info=True):
        _heal = await _env_heal_fetch(install_dir, fix, recommended)
        _env_heal_report(_heal, heal_lines, recommended)
    return heal_lines


# Flag-governance disclosure (2026-08-13): tools/flag_registry.py is a
# PRIVATE-repo-only module (not in scripts/build_dist.TOOL_FILES), so a
# public qa-agent-pro install never runs this repo's own test suite and
# would otherwise never learn that an `experiment` flag's review_by
# date has passed -- CLAUDE.md says "holding a flag at OFF indefinitely
# is not an option", but nothing besides pytest enforced that until
# now. Optional, never blocking: an expired review date is maintainer
# housekeeping, never something that should stop a tester from
# generating test cases today. The import is optional so a public dist
# build (which never ships this module) skips the note,
# and the catch is broad -- matching the env self-heal block right
# above -- so a bug in flag_registry degrades to a skipped optional
# line instead of escaping to this function's OUTER except Exception
# and discarding the whole report.
def _flag_expiry_item(optional: list[str]) -> None:
    """Add an optional note when feature flags are past their review-by date."""
    if flag_registry is None:
        return
    with never_raise(logger, "flag-registry expiry check skipped", exc_info=True):
        _expired = flag_registry.expiring_on_or_before(clock.today().isoformat())
        if _expired:
            _names = ", ".join(f"`{name.upper()}`" for name, _e in _expired[:5])
            _more = f" (+{len(_expired) - 5} more)" if len(_expired) > 5 else ""
            optional.append(
                f"{len(_expired)} feature flag(s) are past their review-by "
                f"date: {_names}{_more}. Promote each to always-on or delete "
                "it per the CLAUDE.md flag policy — see docs/FEATURE_FLAGS.md."
            )


# Fix 7 / M3 (2026-08-03): the ONLY discoverable path to registration.
# QA_AUTO_REGISTER_CLIENTS defaults OFF (it writes outside the install
# dir), and even ON it cannot bootstrap the FIRST client -- if no editor is
# registered, nothing launches this server, so no startup pass ever runs.
# Without this line a tester who installs another editor has no way to
# learn that connect.sh needs re-running, which is the whole gap Fix 7 is
# about. Optional, not a blocker: the reader is, by definition, running in
# a client that IS already registered.
_CONNECT_SCRIPT_HINT: str = (
    "Installed another editor since setting this up? Run "
    "`~/qa-agent-pro/connect.sh` to register this server with it -- "
    "registration otherwise happens only once, during install. It is "
    "idempotent, preserves your other MCP servers, and backs up any file "
    "it changes."
)
