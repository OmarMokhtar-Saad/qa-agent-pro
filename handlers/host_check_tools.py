"""Optional command-line tooling rows and gaps for the host-check report."""

from __future__ import annotations

import os
import shutil
import sys

from handlers import _lane
from handlers.host_check_toolinfo import _TOOL_INFO
from handlers.seam_alias import alias


# THE ONE LOOKUP, and the reason it exists as a function. Two consumers read
# it -- the "Command-line tooling" rows and the headline's "not available on
# this machine" list -- and neither calls ``shutil.which`` again. Asking the
# same binary twice in two places is the mirrored-condition defect CLAUDE.md
# names: the day one of them grows an SDK fallback, the report contradicts
# its own headline and nothing fails.
#
# ``xcrun`` is asked for on macOS only, for the reason ``tooling_lines``
# states: it can never exist elsewhere, so a cross there is noise, not news.
# That rule lives HERE, once, because this dict is now what decides which
# rows render.
def optional_tool_paths() -> dict[str, str | None]:
    """WHERE each optional command-line tool is on this machine, or ``None``."""
    names = ["adb"] + (["xcrun"] if sys.platform == "darwin" else [])
    paths = {name: shutil.which(name) for name in names}
    if not paths["adb"]:
        # The lane drives the SDK's adb when PATH has none; saying "adb is not
        # installed" while the lane runs on it is the D1 contradiction. A
        # filesystem lookup only: it starts no adb server. A build without the
        # mobile modules keeps the PATH answer.
        if _lane.sdk_locator is None:
            return paths
        found = _lane.sdk_locator.resolve_adb()
        paths["adb"] = found if os.path.isabs(found) and os.path.exists(found) else None
    return paths


def tooling_gaps(paths: dict[str, str | None]) -> list[str]:
    """One phrase per optional tool that is ABSENT, naming what it would serve.

    Reads the purpose out of ``_TOOL_INFO`` -- the same field the row prints --
    so the headline and the row can never describe one binary two ways.

    Feeds NO count in ``overall_verdict``, deliberately: a missing optional
    tool must never become a blocker, because nothing here is needed to
    generate test cases. It changes what the headline SAYS, not which verdict
    it reaches.
    """
    out: list[str] = []
    for name, path in paths.items():
        if path:
            continue
        purpose = str((_TOOL_INFO.get(name) or {}).get("purpose") or name)
        out.append(f"{purpose} (`{name}` is not installed)")
    return out


# 2026-08-04. This printed a red cross and the words "not found", nothing more.
# On a Windows test-cases-only install that rendered as three failures of which
# exactly one (`adb`) could matter, with no indication of what to type next --
# so a correctly-installed machine looked broken. Every row now carries its
# purpose, and a missing one carries the install command for THIS platform.
# Never raises: an unknown name degrades to the old bare line.
#
# THE ROUTE IS PROSE AND THE COMMAND IS A COMMAND, and the route comes
# FIRST. Both halves were previously one string inside the backticks, which
# made the thing a tester copy-pastes a paragraph with two alternatives and
# a URL in it -- it pastes into a shell error, and `_binary_line` promises
# "the install command for THIS platform". Splitting them keeps the
# ordering the locked-down case needs (winget alone is a dead end for an
# account that cannot elevate, so the admin-free route must be read first)
# without putting unrunnable text where a command is promised.
#
# The path is PASSED IN, never looked up here: ``optional_tool_paths`` is
# the one lookup, and a second ``shutil.which`` in this function is exactly
# the drift that would let a row and the headline disagree about one
# binary. A required parameter rather than an optional one, so a caller
# that forgets it fails loudly instead of silently re-deriving.
def _binary_line(name: str, path: str | None) -> str:
    """One tooling row: state, what it is FOR, and how to install it here."""
    info = _TOOL_INFO.get(name) or {}
    purpose = str(info.get("purpose") or "")
    tail = f" ({purpose})" if purpose else ""
    route = str((info.get("admin_free") or {}).get(sys.platform) or "")
    if path:
        return f"- ✅ `{name}`{tail} — {path}"
    cmd = str((info.get("install") or {}).get(sys.platform) or "")
    return (
        f"- ❌ `{name}`{tail} — not installed"
        + (f". {route}" if route else "")
        + (f". Or install: `{cmd}`" if cmd else "")
    )


# A function rather than an inline list literal in the report builder, because
# the RELEVANCE rules below are the part that silently mis-reports a healthy
# machine, and inline they could only be tested by rendering the entire setup
# report. Same reason the header now says "optional" out loud: a tester read
# three crosses as three things they had to fix.
#
# A binary earns a row only if something here USES it. The `cursor-agent`
# row was dropped on 2026-08-20: it served the `cursor` server-side LLM
# backend, which P2-G deleted on 2026-08-16, so the row was offering an
# install command for a capability no edition has. Its gate was an EDITION
# check, which is why it kept rendering on a full checkout long after the
# backend was gone.
#
# adb stays in both editions (qa_list_devices ships in the dist too), and
# xcrun can NEVER exist off macOS so a cross there is noise, not news --
# both rules now live in ``optional_tool_paths``, which is what decides
# which binaries were asked about at all. This loop renders whatever that
# ONE lookup found, in its order, and derives nothing of its own.
def tooling_lines(paths: dict[str, str | None]) -> list[str]:
    """The whole "Command-line tooling" section, as report lines."""
    rows: list[str] = [_binary_line(name, path) for name, path in paths.items()]
    return [
        "### Command-line tooling (all optional)",
        "_None of this is needed to generate test cases. A ❌ limits only the "
        "capability named on its own line._",
        *rows,
    ]


_optional_tool_paths = alias(sys.modules[__name__], "optional_tool_paths")
_tooling_gaps = alias(sys.modules[__name__], "tooling_gaps")
_tooling_lines = alias(sys.modules[__name__], "tooling_lines")
