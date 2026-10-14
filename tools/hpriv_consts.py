"""The constants of the host-privilege probe: os names, markers, refresh hint."""

# Prose of the original tools/host_privileges.py module docstring, kept verbatim.
# Top level, deliberately NOT under ``tools/mobile/``: ``mcp_handlers._TOOL_INFO``
# and qa-doctor are not mobile, and a test-cases-only edition ships no
# ``tools/mobile/`` on disk at all, so a mobile-resident producer would be
# unreachable exactly where plain ``adb`` install advice is printed.
#
# ONE PRODUCER, ONE MEANING, EVERY CONSUMER. ``content`` carries EXACTLY these
# ten fields and nothing else, and the enumeration here is the contract -- a
# consumer rendering a field this list does not declare is the half-wired-sentinel
# failure, so the list is complete rather than representative:
#
#     os, arch, elevated, can_elevate, undetermined, method,
#     cached, refresh_hint, summary, steps
#
# The first six are the verdict; ``cached``/``refresh_hint`` are the staleness
# disclosure; ``summary`` is the one tester-facing sentence (rendered by
# :func:`verdict_sentence` so three consumers cannot word it differently) and
# ``steps`` is the per-step attemptability list from :func:`attemptability`. The
# consumers that branch on these names: the ``qa_host_check`` tool (renders
# ``summary`` and ``steps``), the mobile preflight advisory record (``summary``,
# ``undetermined``, ``can_elevate``), the virtualization fix text, and the setup
# report's install rows. Adding a fifth consumer means handling every field there
# or saying why its ``else`` is right.
#
# THREE PROPERTIES THAT ARE NOT NEGOTIABLE:
#
# 1. **Advisory only.** Nothing here blocks a run or a provision. It changes what
#    the tester is TOLD and gives the calling agent fields to branch on.
# 2. **Never prompting.** ``sudo -n`` refuses rather than asking, and the Windows
#    probe READS group membership rather than requesting elevation. A probe that
#    popped a password prompt inside an MCP stdio server would hang the client
#    with no visible cause.
# 3. **"Cannot determine" reads as UNDETERMINED, never as "no admin".** Answering
#    ``can_elevate=False`` on an unparsable probe would newly refuse installs that
#    work today -- a regression dressed as a safety feature. ``undetermined`` is a
#    first-class value for that reason, and the rendered text says the word.
#
# The verdict is cached per process: the probe spawns a subprocess and the reply
# is rendered on the qa-doctor and preflight paths. But the reply STATES that it
# is cached and how to re-probe, and ``probe(refresh=True)`` really does re-probe.
# The MCP server is long-lived and IT can grant rights mid-session; a silently
# stale machine record is a defect class this repo already shipped once.

from __future__ import annotations

#: Internal os names. Lowercase, matching what ``mobile.platform_info`` returns,
#: because consumers branch on them; only prose uses the display spellings.
MACOS = "macos"
WINDOWS = "windows"
OTHER = "other"

DISPLAY = {MACOS: "macOS", WINDOWS: "Windows", OTHER: "this OS"}

#: The built-in Administrators group SID. Used rather than the group NAME, which
#: is localised -- a name match would report "no admin" on every non-English
#: Windows install, which is the false negative this module exists to avoid.
ADMIN_SID = "S-1-5-32-544"

#: ``sudo -n`` failing with one of these means the account IS a sudoer and was
#: merely refused a NON-INTERACTIVE elevation. Treating that as "no admin" is
#: the exact false negative that would make this module worse than the
#: hardcoded line it replaces, so it gets its own clause and its own fixture.
_SUDOER_MARKERS = (
    "password is required",
    "a terminal is required",
    "askpass",
    "no tty present",
)

#: The only stderr that is real evidence of a DETERMINED refusal.
_DENIED_MARKERS = (
    "not in the sudoers file",
    "not allowed to run sudo",
    "is not allowed to execute",
)

REFRESH_HINT = (
    "This verdict was probed once for this server process and reused since. "
    "Call qa_host_check with refresh=true to probe again -- rights granted by "
    "IT mid-session are not picked up otherwise."
)
