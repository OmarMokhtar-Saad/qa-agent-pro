"""Moved from handlers (refactor P2): host_check."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from typing import Any

from config.settings import settings
from handlers import _common, export, generate
from tools import host_privileges
from tools.jira_mcp import _ac_field_discovery_on

logger = logging.getLogger("tools.mcp_handlers")


async def handle_host_check(refresh: bool = False) -> str:
    """What OS is this, and can this account elevate? ADVISORY, never a refusal.

    Rendered from ``tools.host_privileges`` -- the ONE producer of the verdict,
    so this report, the mobile preflight advisory, the virtualization fix text
    and the tooling install rows cannot disagree. Nothing here blocks anything:
    the point is that the calling agent stops proposing an elevated command to
    a tester who cannot run it, and stops sending a tester who CAN elevate off
    to IT.

    Never raises. An ``error`` verdict degrades to one advisory line, because a
    failed probe must not read as a denial.
    """
    reply = host_privileges.probe(refresh=bool(refresh))
    if reply.get("error") or not reply.get("content"):
        return (
            "ℹ️ **Host privileges: could not be determined.** "
            + str(reply.get("error") or "the probe returned nothing")
            + ". This is UNDETERMINED, not a lack of administrator rights, and "
            "it blocks nothing -- prefer the admin-free install route and say "
            "so rather than assuming either answer."
        )
    content = reply["content"]
    lines = _host_check_header(content)
    for row in content.get("steps") or []:
        lines.extend(_host_check_step_lines(row))
    return "\n".join(lines)


def _host_check_header(content: dict) -> list[str]:
    """Report lines above the per-step list."""
    lines = [
        "## Host check",
        "",
        "- **OS:** "
        + host_privileges.DISPLAY.get(str(content.get("os")), "unknown")
        + "  |  **arch:** "
        + str(content.get("arch") or "unknown"),
        "- **Verdict:** " + str(content.get("summary") or ""),
        "- **How it was checked:** " + str(content.get("method") or ""),
    ]
    if content.get("cached"):
        lines.append("- **Cached:** yes. " + str(content.get("refresh_hint") or ""))
    else:
        lines.append(
            "- **Cached:** no, probed just now. "
            + str(content.get("refresh_hint") or "")
        )
    lines += [
        "",
        "This is advisory. It does not block a run, a provision or an install.",
        "",
        "### Steps, admin-free route first",
        "",
    ]
    return lines


def _host_check_step_lines(row: dict) -> list[str]:
    """Report lines for one install step."""
    lines = [
        "- **"
        + str(row.get("step"))
        + "** -- attemptable on this account: `"
        + str(row.get("attemptable"))
        + "`"
        + (" (needs administrator rights)" if row.get("needs_admin") else ""),
        "  - " + str(row.get("admin_free_route")),
    ]
    if row.get("needs_network"):
        # `attemptable` is a PRIVILEGE answer and nothing else. Saying only
        # "yes" to a tester behind an authenticating proxy or a
        # TLS-inspecting middlebox sends them to retry a download that
        # cannot work and tells them nothing about why -- the rights really
        # are fine. Nothing here probes reachability, so this says what was
        # checked rather than implying a check nobody ran.
        lines.append(
            "  - This step downloads from the internet. Nothing here checked "
            "that the download can reach you: on a managed machine a proxy "
            "or TLS inspection can refuse it while your account rights are "
            "fine. If it fails with a certificate or proxy error, that is "
            "not a permissions problem and elevating will not fix it."
        )
    return lines


# What each optional binary is FOR, and how to get it per platform. A row with no
# command for this platform prints no command -- offering `brew` on Windows is
# worse than offering nothing, and `xcrun` cannot exist off macOS at all.
_TOOL_INFO: dict[str, dict] = {
    "adb": {
        # NAMED FIRST in the rendered row, deliberately: an account that cannot
        # elevate has no use for the package-manager line, and a tester reads
        # the first option. Prose, because it is a route rather than a command
        # -- and therefore outside the backticks.
        "admin_free": {
            "win32": "Unpack Google's platform-tools into your home directory "
            "and add it to PATH (no administrator rights)",
            "darwin": "Unpack Google's platform-tools into your home directory "
            "and add it to PATH (no administrator rights)",
        },
        # Deliberately NOT naming `qa_list_devices` here: a guard test asserts
        # that string never appears in the setup report, because the report must
        # not read as though it had enumerated the tester's devices.
        "purpose": "Android device listing and screen capture",
        "install": {
            # ADMIN-FREE ROUTE FIRST, then the package manager, then the
            # official URL -- and the URL comes from tools.host_privileges so a
            # stale link is fixed in ONE place. `--scope user` is what makes
            # the winget line runnable on a locked-down corporate laptop; the
            # literal prefix before it is pinned by a test, so it stays.
            # A RUNNABLE COMMAND ONLY. `_binary_line` renders this inside
            # backticks after the word "Install:", and its docstring promises
            # the install command for THIS platform -- so a non-technical
            # tester copy-pastes whatever is here. This carried a prose
            # paragraph with two alternatives and a URL, which pastes into a
            # shell error. The admin-free unzip route is not lost: it is the
            # `admin_free_route` of the platform-tools step in
            # `host_privileges.STEPS`, which is where a route rather than a
            # command belongs. Linux has no entry at all and degrades to a row
            # with no command clause; this now matches that shape.
            "win32": "winget install --id Google.PlatformTools -e --scope user",
            "darwin": "brew install --cask android-platform-tools",
            # No `linux` row, and its absence is the honest answer rather than
            # an omission: the hosts in scope are macOS and Windows, mirroring
            # tools/mobile/platform_info.SUPPORT. The deleted row read
            # `sudo apt install android-tools-adb` -- an elevated command, for
            # an unsupported host, handed to every tester regardless of
            # whether they could run it. `_binary_line` prints no command when
            # a platform has no row, which is what should happen here.
        },
    },
    "xcrun": {
        "purpose": "iOS Simulator, macOS only — ships with Xcode",
        "install": {"darwin": "xcode-select --install"},
    },
}


def _optional_tool_paths() -> dict[str, str | None]:
    """WHERE each optional command-line tool is on this machine, or ``None``.

    THE ONE LOOKUP, and the reason it exists as a function. Two consumers read
    it -- the "Command-line tooling" rows and the headline's "not available on
    this machine" list -- and neither calls ``shutil.which`` again. Asking the
    same binary twice in two places is the mirrored-condition defect CLAUDE.md
    names: the day one of them grows an SDK fallback, the report contradicts
    its own headline and nothing fails.

    ``xcrun`` is asked for on macOS only, for the reason ``_tooling_lines``
    states: it can never exist elsewhere, so a cross there is noise, not news.
    That rule lives HERE, once, because this dict is now what decides which
    rows render.
    """
    names = ["adb"] + (["xcrun"] if sys.platform == "darwin" else [])
    paths = {name: shutil.which(name) for name in names}
    if not paths["adb"]:
        # The lane drives the SDK's adb when PATH has none; saying "adb is not
        # installed" while the lane runs on it is the D1 contradiction. A
        # filesystem lookup only: it starts no adb server. A build without the
        # mobile modules keeps the PATH answer.
        try:
            from tools.mobile import sdk_locator
        except ImportError:
            return paths
        found = sdk_locator.resolve_adb()
        paths["adb"] = found if os.path.isabs(found) and os.path.exists(found) else None
    return paths


def _tooling_gaps(paths: dict[str, str | None]) -> list[str]:
    """One phrase per optional tool that is ABSENT, naming what it would serve.

    Reads the purpose out of ``_TOOL_INFO`` -- the same field the row prints --
    so the headline and the row can never describe one binary two ways.

    Feeds NO count in ``_overall_verdict``, deliberately: a missing optional
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


def _binary_line(name: str, path: str | None) -> str:
    """One tooling row: state, what it is FOR, and how to install it here.

    2026-08-04. This printed a red cross and the words "not found", nothing more.
    On a Windows test-cases-only install that rendered as three failures of which
    exactly one (`adb`) could matter, with no indication of what to type next --
    so a correctly-installed machine looked broken. Every row now carries its
    purpose, and a missing one carries the install command for THIS platform.
    Never raises: an unknown name degrades to the old bare line.
    """
    info = _TOOL_INFO.get(name) or {}
    purpose = str(info.get("purpose") or "")
    tail = f" ({purpose})" if purpose else ""
    # THE ROUTE IS PROSE AND THE COMMAND IS A COMMAND, and the route comes
    # FIRST. Both halves were previously one string inside the backticks, which
    # made the thing a tester copy-pastes a paragraph with two alternatives and
    # a URL in it -- it pastes into a shell error, and `_binary_line` promises
    # "the install command for THIS platform". Splitting them keeps the
    # ordering the locked-down case needs (winget alone is a dead end for an
    # account that cannot elevate, so the admin-free route must be read first)
    # without putting unrunnable text where a command is promised.
    route = str((info.get("admin_free") or {}).get(sys.platform) or "")
    # The path is PASSED IN, never looked up here: ``_optional_tool_paths`` is
    # the one lookup, and a second ``shutil.which`` in this function is exactly
    # the drift that would let a row and the headline disagree about one
    # binary. A required parameter rather than an optional one, so a caller
    # that forgets it fails loudly instead of silently re-deriving.
    if path:
        return f"- ✅ `{name}`{tail} — {path}"
    cmd = str((info.get("install") or {}).get(sys.platform) or "")
    return (
        f"- ❌ `{name}`{tail} — not installed"
        + (f". {route}" if route else "")
        + (f". Or install: `{cmd}`" if cmd else "")
    )


def _tooling_lines(paths: dict[str, str | None]) -> list[str]:
    """The whole "Command-line tooling" section, as report lines.

    A function rather than an inline list literal in the report builder, because
    the RELEVANCE rules below are the part that silently mis-reports a healthy
    machine, and inline they could only be tested by rendering the entire setup
    report. Same reason the header now says "optional" out loud: a tester read
    three crosses as three things they had to fix.
    """
    # A binary earns a row only if something here USES it. The `cursor-agent`
    # row was dropped on 2026-08-20: it served the `cursor` server-side LLM
    # backend, which P2-G deleted on 2026-08-16, so the row was offering an
    # install command for a capability no edition has. Its gate was an EDITION
    # check, which is why it kept rendering on a full checkout long after the
    # backend was gone.
    #
    # adb stays in both editions (qa_list_devices ships in the dist too), and
    # xcrun can NEVER exist off macOS so a cross there is noise, not news --
    # both rules now live in ``_optional_tool_paths``, which is what decides
    # which binaries were asked about at all. This loop renders whatever that
    # ONE lookup found, in its order, and derives nothing of its own.
    rows: list[str] = [_binary_line(name, path) for name, path in paths.items()]
    return [
        "### Command-line tooling (all optional)",
        "_None of this is needed to generate test cases. A ❌ limits only the "
        "capability named on its own line._",
        *rows,
    ]


def _ac_field_section() -> list[str]:
    """Disclose which Jira field is configured as the acceptance-criteria source.

    ``settings.jira_ac_field`` is a per-instance GUESS. It USED to default to
    ``customfield_10016``, which is a DATE field on at least one real workspace,
    and the timestamp it returned became a suite's only "acceptance criterion"
    -- which also suppressed the host job that would otherwise have derived real
    ones. The failure was invisible until someone read 98 generated cases.

    2026-08-31: that default is GONE -- the field is unset unless an install
    names one, so the shipped behaviour no longer guesses. This section says so
    rather than warning about a default that no longer exists, and explains
    what unset MEANS, which is not "broken": the description scan, the use-case
    table and the host AC job all still run.

    Static text only: no ticket is fetched here, so this cannot fail or slow
    the report down.
    """
    try:
        raw_field = str(getattr(settings, "jira_ac_field", "") or "").strip()
        out = [
            "### Acceptance-criteria field",
            "",
        ]
        out += _ac_field_configured_lines(raw_field)
        out.append(_ac_field_discovery_line(_ac_field_discovery_on()))
        out.append(
            "- **No action needed.** A wrong id costs nothing: it is detected and "
            "discarded, and the criteria are read from the description instead. "
            "Setting `JIRA_AC_FIELD` to your instance's real Acceptance Criteria "
            "field is an accuracy hint, not a fix -- worth doing only if your "
            "tickets keep their criteria in a custom field rather than in the "
            "description or a use-case table."
        )
        return out
    except Exception:
        logger.exception("_ac_field_section failed - omitting it")
        return []


def _ac_field_configured_lines(raw_field: str) -> list[str]:
    """The 'Configured field' bullets for a set or unset JIRA_AC_FIELD."""
    if raw_field:
        return [
            f"- Configured field: `{raw_field}` (`JIRA_AC_FIELD`)",
            "- This id differs per Jira instance. It is used ONLY when its "
            "value reads like requirement text -- a date, a bare number or a "
            "single token is rejected, because on one workspace a guessed id "
            "turned out to be a DATE field and the timestamp became the "
            "suite's only acceptance criterion.",
        ]
    return [
        "- Configured field: **none** (`JIRA_AC_FIELD` is unset -- the "
        "shipped default since 2026-08-31).",
        "- This is not a gap. No id is guessed, so nothing is requested "
        "from Jira that this install has not named, and no wrong field's "
        "value can be mistaken for requirements. Criteria come from the "
        "ticket description and its use-case table, and your chat model "
        "derives them when neither yields any.",
    ]


def _ac_field_discovery_line(discovery: bool) -> str:
    """The custom-field discovery bullet."""
    if discovery:
        return (
            "- Custom-field discovery is **on**: when the configured field "
            "holds nothing usable, other custom fields are searched for one "
            "whose value reads like requirements. The choice is logged."
        )
    return (
        "- Custom-field discovery is off (default). When the configured "
        "field holds nothing usable, the ticket description is parsed "
        "instead -- an 'Acceptance Criteria' heading, or a use-case "
        "table. If neither yields anything, your chat model is asked to "
        "derive the criteria, so traceability still works."
    )


def _jira_in_use(
    workspace_roots: list | None = None, verdict_view: object = None
) -> bool:
    """True when Jira is ALREADY part of this install. Read-only, never raises.

    qa-doctor checks and mentions Atlassian/Jira only then. Any one is enough: a
    stored Atlassian verdict (so a known-broken connection is never hidden), a
    non-empty ``settings.jira_base_url`` or ``settings.jira_ac_field``, or an
    ``atlassian`` entry on disk for a file-configured client. A failed lookup
    answers False, which only ever removes Jira lines.
    """
    try:
        if verdict_view:
            return True
        if str(settings.jira_base_url or "").strip():
            return True
        if str(settings.jira_ac_field or "").strip():
            return True
        from tools.jira_mcp import atlassian_entry_configured

        return atlassian_entry_configured(workspace_roots)
    except Exception:
        logger.debug("jira-in-use check failed", exc_info=True)
        return False


# Design notes for _atlassian_autofix (kept here so the function stays short).
#
# WHY THIS LIVES IN THE SETUP CHECK AT ALL. v1.42.0 taught connect.sh/.ps1 to
# write the entry, but connect runs only from install.ps1 or by hand, the
# launcher's startup pass registers THIS server and nothing else, and the updater
# never calls connect -- so an install that AUTO-UPDATED into v1.42.0 received
# the code and none of the behaviour. Observed on a Windows machine that went
# 1.41.2 -> 1.42.0 and still reported "Not connected" while telling the tester to
# hand-edit ~/.cursor/mcp.json. Every UPGRADING install was unreachable; only
# fresh ones were fixed, which is the opposite of where the users are.
#
# WHY THIS IS ALLOWED TO WRITE OUTSIDE THE INSTALL DIR, when
# QA_AUTO_REGISTER_CLIENTS defaults OFF for doing the same class of thing: that
# flag guards an UNATTENDED startup pass -- "a server that inserts itself into
# other editors' configs whenever it starts" -- and that pass is untouched here.
# This runs only because a tester invoked this tool BY NAME, and the write is
# disclosed in the very response they are already reading. Secondarily it is the
# same disclosure shape QA_ENV_SELFHEAL_ENABLED already uses when this tool
# repairs the install's own .env, though that file is INSIDE the install dir, so
# that precedent supports the shape, not the location.
#
# It writes the entry; it does NOT authorize it. OAuth is the tester's click and
# cannot be observed from a stdio subprocess, so nothing here may report Jira as
# connected -- the same line tools/jira_mcp.connect_hint_line() already walks.
#
# No pre-check on purpose: register_atlassian() already returns PRESENT for an
# existing entry, so calling it unconditionally is both idempotent and free of a
# check-then-act race. Never raises -- a failure costs one advisory line, never
# the report.
#
# An advisory is NOT verdict-neutral: the verdict below reads "Ready, with
# warnings" whenever `recommended` is non-empty. That is intended here, because
# an unwritable entry costs the tester Jira grounding and comes with a concrete
# paste-this action. It is never a BLOCKER -- a Jira convenience cannot make an
# otherwise healthy install report "Not ready".
#
# QA_REGISTER_ATLASSIAN_MCP was DELETED on 2026-08-13 (flag-surface reduction,
# batch 6) and hardcoded ON, so writing itself is unconditional once invoked.
# P2-10 (2026-09-25) added a caller-side gate instead: qa-doctor only passes
# fix=True when the tester asked for repairs, so a plain qa-doctor call never
# reaches register_atlassian at all.
async def _atlassian_autofix(fix: bool = False) -> tuple[list[str], list[str]]:
    """Write the hosted `atlassian` MCP entry when missing. (report_lines, advisories)."""
    try:
        if not fix:
            return [], [
                "Jira MCP entry not checked -- call qa-doctor with fix=true "
                "to write it if missing."
            ]
        from tools.client_registry import register_atlassian

        # to_thread because it takes a file lock, exactly as heal_env is called.
        results = await asyncio.to_thread(register_atlassian)
        return _atlassian_report(results)
    except Exception:
        logger.debug("atlassian autofix failed - skipping it", exc_info=True)
        return [], []


def _atlassian_report(results: list) -> tuple[list[str], list[str]]:
    """Fold register_atlassian results into (report_lines, advisories)."""
    from tools.client_registry import ADDED, ERROR

    lines: list[str] = []
    advisories: list[str] = []
    for label, status, detail in results:
        if status == ADDED:
            if not lines:
                # Header hoisted out of the per-client body: one target exists
                # today, but a second would otherwise repeat the heading.
                lines.append("### Jira connection configured")
                lines.append("")
            lines.append(
                f"- Added the `atlassian` MCP entry for {label} \u2014 "
                f"`{detail}`. Your previous file was backed up alongside it "
                "as `.bak`, and any other MCP servers in it were left alone."
            )
        elif status == ERROR:
            advisories.append(
                f"Could not add the `atlassian` MCP entry for {label} "
                f"({detail}). Add it under `mcpServers` yourself, then restart: "
                '`"atlassian": {"type": "http", "url": '
                '"https://mcp.atlassian.com/v1/mcp/authv2"}`'
            )
    if lines:
        lines.append("")
        lines.append(
            "_One step is still yours: **restart your editor**, then paste "
            "a ticket URL \u2014 the first one opens the Atlassian sign-in "
            "prompt. I can write the entry, but I cannot sign you in, and "
            "I cannot see from here whether it worked._"
        )
        lines.append("")
    return lines, advisories


def _update_rate_limit_advisory(status: str) -> str:
    """The qa-doctor line for an update check GitHub refused on quota.

    D4 (2026-08-21, TICKET-5138) shipped the detection half: run_update_check
    returns "rate-limited" instead of folding a spent quota into "error", so a
    403 is no longer indistinguishable from a DNS failure in the log. Nothing
    told the TESTER, which was the half that mattered -- the live Cursor run at
    21:33:55 logged the 403 and started the version already on disk, so the
    install was silently BLIND to a newer release with the remedy nowhere in
    front of anyone.

    Returns "" for every other status, so it is used as a guard exactly like
    _embeddings_backend_advisory above: a line is printed only where the loss is
    real. RECOMMENDED rather than a blocker -- generation, export and every
    other tool are unaffected, and the quota is per hour, so this cannot make an
    otherwise healthy install report "Not ready". It is deliberately NOT
    `optional` either: unlike the always-true retirement note above, this fires
    only on a run whose quota really was spent, so "Ready, with warnings" is
    accurate for that run and gone by the next one.

    No try/except, unlike its neighbour: this reads no module and no setting, so
    there is no failure mode to swallow -- and therefore no defensive branch that
    would sit uncovered in a directory held at 90%. Never raises."""
    if status != "rate-limited":
        return ""
    return (
        "GitHub RATE-LIMITED this install's update check, so auto-update is "
        "blind right now: the server could not see whether a newer release "
        "exists and is running the version already on disk. This is not a "
        "network failure -- GitHub was reached and refused on quota. No action "
        "is needed to recover: the quota is per hour and clears by itself, so "
        "the next check succeeds. Set `GITHUB_TOKEN` in the install's `.env` "
        "only if you keep seeing this -- an unauthenticated check shares 60 "
        "requests/hour with everything else on this machine, a token gets 5,000."
    )


def _jira_status_text(state: str, view: dict | None) -> str:
    """The Integrations row for Jira, as one of four states.

    Every other row in that table leads with a state glyph -- Python, Excel
    auto-export, and each feature gate -- so the Jira row's blankness was the
    only cell in a table of verdicts that carried none, and absence reads as
    "nothing to flag". This server genuinely cannot see the tester's Atlassian
    OAuth session, so the honest default is "not checked yet", never "working"
    and never "broken".

    Pure and total: no store read, no clock, no settings, so freshness is
    decided once by the caller and never re-derived here. Never raises.
    """
    template = _JIRA_STATUS_TEMPLATES.get(state, _JIRA_STATUS_UNCHECKED)
    return template.format(
        head="\U0001f517 **Jira** \u2014 ",
        age=_jira_status_age(view),
        tail=_JIRA_STATUS_TAIL,
    )


_JIRA_STATUS_TAIL = (
    "Jira is read through YOUR Atlassian MCP connection (OAuth, Jira "
    "Cloud) \u2014 nothing to configure on this machine."
)

_JIRA_STATUS_UNCHECKED = (
    "{head}\u26a0\ufe0f not checked yet. {tail} I check it the first time "
    "you ask me for a Jira ticket, not before \u2014 it's probably fine; if "
    "you're not signed in then, I'll walk you through authorizing it."
)

# Placeholders: {head} {age} {tail}; any other state falls back to UNCHECKED.
_JIRA_STATUS_TEMPLATES: dict[str, str] = {
    "verified": "{head}\u2705 checked and working ({age} ago). {tail}",
    "stale": (
        "{head}\u26a0\ufe0f checked {age} ago and not re-checked since. "
        "{tail} An OAuth session can expire or be revoked in that time, so "
        "I'll re-check it the next time you ask me for a Jira ticket."
    ),
    "access_unconfirmed": (
        "{head}\u2705 signed in ({age} ago) \u2014 but I could not confirm "
        "it can reach your Jira site. {tail} A ticket URL may still fail; "
        "if one does, that is the first thing to check."
    ),
    "probe_outdated": (
        "{head}\u26a0\ufe0f checked {age} ago, but only that you were "
        "signed in \u2014 not that the account can reach your Jira site. "
        "{tail} I'll re-check it the next time you ask "
        "me for a Jira ticket."
    ),
    "wrong_site": (
        "{head}\u274c signed in ({age} ago), but to an Atlassian account "
        "that cannot reach your Jira site. {tail} Ticket URLs will fail "
        "until you connect the account that has it -- run "
        "`qa_configure_jira` and I'll show the exact steps for your client."
    ),
    "failed": (
        "{head}\u274c the last check ({age} ago) did not come back "
        "connected. {tail} Paste a ticket URL, or run `qa_configure_jira`, "
        "and I'll show the exact connection steps for your client."
    ),
}


def _jira_status_age(view: dict | None) -> str:
    """Age label for the Jira row; days past 48 hours, else _ago_label."""
    # _ago_label tops out at hours, and "stale" means a WEEK by definition, so
    # the state this matters most for would have read "168 hour(s) ago". Days
    # are formatted here rather than in the shared helper, whose other callers
    # report minutes-old preps and want its existing behaviour.
    _age_s = float((view or {}).get("age_s") or 0)
    if _age_s >= 48 * 3600:
        _days = int(_age_s // (24 * 3600))
        return f"{_days} day{'s' if _days != 1 else ''}"
    return generate._ago_label(_age_s)


def _overall_verdict(
    blocker_count: int,
    recommended_count: int,
    unverified_count: int,
    *,
    limited: tuple[str, ...] = (),
) -> str:
    """The qa-doctor headline, decided from what the Action items will SAY.

    Pure and total on purpose (D-E1, 2026-09-01). The verdict used to be an
    inline if/elif over ``blockers``/``recommended`` while a third category --
    the unconditional Atlassian probe item -- was appended to ``items``
    downstream of it, so "all required checks passed" shipped four lines above
    an open "Fix now" entry on every run, healthy installs included. A headline
    that its own report contradicts trains the reader to skip the headline.

    ``unverified_count`` is the third category: checks this server cannot
    settle from here and has asked the calling agent to settle for it. They are
    not blockers (nothing is known to be broken) and not warnings (no action is
    recommended to the TESTER), so neither existing branch fits.
    """
    if blocker_count:
        head = (
            f"\u274c **Not ready** \u2014 {blocker_count} blocking issue(s), "
            "see Action items below"
        )
    elif recommended_count:
        head = "\u26a0\ufe0f **Ready, with warnings** \u2014 see Action items below"
    elif unverified_count:
        head = (
            "\u26a0\ufe0f **Ready \u2014 one check unverified**, see Action items below"
            if unverified_count == 1
            else f"\u26a0\ufe0f **Ready \u2014 {unverified_count} checks "
            "unverified**, see Action items below"
        )
    else:
        head = "\u2705 **Ready** \u2014 all required checks passed"
    if not limited:
        return head
    # APPENDED, never counted. `limited` names what this machine cannot do --
    # a missing optional tool, a Jira connection known not to work, an Android
    # lane switched off -- and it must not be able to move the branch above:
    # `adb` is not needed to generate a single test case, so an install without
    # it is READY, and saying otherwise would train the reader to ignore the
    # one line the report leads with. What was wrong before is narrower and is
    # fixed here: the headline said "Ready" and never named what had stopped
    # working, so a machine with no Android tooling and a dead Jira read
    # exactly like a healthy one.
    return head + " \u00b7 Not available on this machine: " + "; ".join(limited)


async def handle_machine_report(section: str = "all") -> str:
    """The machine-readable twin of the prose surfaces, for a non-chat client.

    READ-ONLY, unlike `qa-doctor`: it repairs no `.env` and writes no MCP
    config, so a desktop app may poll it without a tester wondering what a
    refresh changed. Never raises -- an unreadable producer becomes an
    `undetermined` row, because a client that loses the whole report over one
    unreadable file learns less than one that loses a line.
    """
    await _common._audit("mcp_machine_report")
    try:
        from tools import machine_report

        payload = await asyncio.to_thread(
            machine_report.collect, section, edition_probe=export._edition_facts
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("handle_machine_report failed")
        payload = {"error": str(exc)}
    return "```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"


async def handle_selfcheck(*, server: Any) -> str:
    """Invoke every registered handler and report what the replies NAME.

    The composition root for ``tools/selfcheck.py``: this is the only place
    that can supply the LIVE server object, because the Constitution forbids a
    ``tools/`` module from importing ``mcp_server`` and ``qa_selfcheck``'s
    closure is the one thing holding a reference to the built ``FastMCP``.
    Everything the check needs is injected here and nothing is discovered by
    import.

    Read-only for the tester's data and structurally incapable of an external
    write -- see the four layers documented in ``tools/selfcheck``. It answers
    the question ``qa-doctor`` cannot: ``qa-doctor`` reports whether this
    MACHINE is ready, while this reports whether this BUILD's own replies still
    describe it. Never raises.
    """
    await _common._audit("mcp_selfcheck")
    try:
        import llm
        from tools import selfcheck

        report = await selfcheck.run(
            server=server,
            settings_cls=type(settings),
            llm_module=llm,
            root=_common._INSTALL_ROOT,
        )
        await _common._audit(
            "mcp_selfcheck_result",
            detail={
                "findings": len(report.get("findings") or ()),
                "skips": len(report.get("skips") or ()),
                "scanned": report.get("scanned") or {},
                "floors": report.get("floors") or [],
            },
        )
        return selfcheck.render(report)
    except Exception:
        logger.exception("self-check failed")
        return (
            "\u26a0\ufe0f The self-check could not complete, so it has NO "
            "verdict for you -- do not read this as clean. The failure is in "
            "this install's log (`data/logs/`). Nothing was changed."
        )
