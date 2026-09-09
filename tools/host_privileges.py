"""Does THIS host let THIS account elevate -- asked BEFORE anything is proposed.

Top level, deliberately NOT under ``tools/mobile/``: ``mcp_handlers._TOOL_INFO``
and qa-doctor are not mobile, and a test-cases-only edition ships no
``tools/mobile/`` on disk at all, so a mobile-resident producer would be
unreachable exactly where plain ``adb`` install advice is printed.

ONE PRODUCER, ONE MEANING, EVERY CONSUMER. ``content`` carries EXACTLY these
ten fields and nothing else, and the enumeration here is the contract -- a
consumer rendering a field this list does not declare is the half-wired-sentinel
failure, so the list is complete rather than representative:

    os, arch, elevated, can_elevate, undetermined, method,
    cached, refresh_hint, summary, steps

The first six are the verdict; ``cached``/``refresh_hint`` are the staleness
disclosure; ``summary`` is the one tester-facing sentence (rendered by
:func:`verdict_sentence` so three consumers cannot word it differently) and
``steps`` is the per-step attemptability list from :func:`attemptability`. The
consumers that branch on these names: the ``qa_host_check`` tool (renders
``summary`` and ``steps``), the mobile preflight advisory record (``summary``,
``undetermined``, ``can_elevate``), the virtualization fix text, and the setup
report's install rows. Adding a fifth consumer means handling every field there
or saying why its ``else`` is right.

THREE PROPERTIES THAT ARE NOT NEGOTIABLE:

1. **Advisory only.** Nothing here blocks a run or a provision. It changes what
   the tester is TOLD and gives the calling agent fields to branch on.
2. **Never prompting.** ``sudo -n`` refuses rather than asking, and the Windows
   probe READS group membership rather than requesting elevation. A probe that
   popped a password prompt inside an MCP stdio server would hang the client
   with no visible cause.
3. **"Cannot determine" reads as UNDETERMINED, never as "no admin".** Answering
   ``can_elevate=False`` on an unparsable probe would newly refuse installs that
   work today -- a regression dressed as a safety feature. ``undetermined`` is a
   first-class value for that reason, and the rendered text says the word.

The verdict is cached per process: the probe spawns a subprocess and the reply
is rendered on the qa-doctor and preflight paths. But the reply STATES that it
is cached and how to re-probe, and ``probe(refresh=True)`` really does re-probe.
The MCP server is long-lived and IT can grant rights mid-session; a silently
stale machine record is a defect class this repo already shipped once.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

#: PER-SUBPROCESS timeout, passed to every ``_run`` call the way
#: ``mobile.platform_info._run_sync`` takes its ``timeout`` argument -- the
#: pattern this repo already uses, reused rather than reinvented. This bounds
#: ONE call, NOT the probe: the Windows path runs two of them in sequence, so
#: ``PROBE_BUDGET_S`` below is what bounds the wait the caller actually feels.
PROBE_TIMEOUT_S = 5

#: The WHOLE probe's wall-clock budget, and the only number a caller can reason
#: about. Checked against a single deadline set once in :func:`probe`, so adding
#: a third subprocess later cannot widen the wait without changing this line:
#: each ``_run`` is given ``min(PROBE_TIMEOUT_S, remaining budget)`` and a probe
#: with no budget left returns UNDETERMINED instead of starting another call.
#: Smaller than 2x the per-call timeout deliberately -- the aggregate is the
#: promise, and a sum-of-parts bound is not one.
PROBE_BUDGET_S = 8

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

#: ONE producer for the official vendor URLs, so a stale link is fixed in one
#: place -- and the ONLY place a URL lives in this module. Every value here was
#: sourced by research and GRADED before it was pinned; each is a vendor page
#: rather than a direct binary link, because a binary link rots faster and a
#: tester cannot check it. A test asserts every value matches ``^https://``,
#: which is why prose that legitimately carries no URL is kept OUT of this
#: mapping instead of being given an invented one.
#:
#: THREE CLAIMS THIS MODULE MAKES WITH NO URL, DELIBERATELY:
#:   * macOS Hypervisor.framework needs no enabling step and no admin rights --
#:     it is present or it is not. The only source found was an Apple developer
#:     FORUM thread, which is not a vendor doc, so the fact is stated plainly
#:     and ``emulator_accel`` covers the emulator's side of it.
#:   * ``Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform``
#:     has no single canonical Microsoft page. The command text stays (it is
#:     correct); ``win_policy_lockdown`` covers the "IT may have blocked this
#:     even for admins" case. No URL is invented for the cmdlet.
#:   * BIOS/UEFI firmware virtualization has no dedicated official page. The
#:     existing factual sentence stands without a citation.
REFERENCES: dict[str, str] = {
    # Command line tools: unzip into a user directory and run
    # sdkmanager/avdmanager.
    #
    # PROVENANCE, because the distinction decides whether we may say it: the
    # page states that a "command line tools only" ZIP exists and is usable
    # without installing Android Studio -- VERIFIED. It does NOT contain the
    # words "no administrator rights"; that property is OUR inference from the
    # artifact's form, since unzipping into a directory the tester already owns
    # requires no elevation by construction. So the no-admin claim is ours to
    # defend and is phrased as what the tester DOES ("unzip it into your home
    # directory"), never as "Google says you need no admin".
    "cmdline_tools_zip": "https://developer.android.com/studio",
    # platform-tools (adb) is a plain zip -- verified on the page. The landing
    # page rather than the dl.google.com direct link, which rots and cannot be
    # eyeballed. Same provenance split as above: "plain zip" is the page's,
    # "therefore no admin" is ours.
    "platform_tools_zip": "https://developer.android.com/tools/releases/platform-tools",
    "emulator_accel": "https://developer.android.com/studio/run/emulator-acceleration",
    # `--scope` with values `user` or `machine` is documented HERE, on the
    # install-command page -- NOT on the winget landing page, which was cited
    # for it until the page was actually read and does not mention the option
    # at all.
    "winget_scope": "https://learn.microsoft.com/en-us/windows/package-manager/winget/install",
    # And this is the page that says what `--scope user` does NOT do. Microsoft
    # is explicit: "Packages installed in user scope may still require UAC
    # (User Account Control) authorization from an administrator." So scope
    # selects a LOCATION, not an elevation bypass. The first draft of the step
    # text below claimed `--scope user` "needs no elevation for the installer"
    # -- a false promise that would have sent a locked-down tester into a UAC
    # prompt they cannot satisfy, which is the exact failure this module was
    # written to remove. The zip route is the one that cannot prompt.
    "winget_scope_uac": (
        "https://learn.microsoft.com/en-us/windows/package-manager/winget/"
        "troubleshooting"
    ),
    # Homebrew's own install needs sudo and documents no supported non-admin
    # alternative; `brew install` for a regular formula afterwards does not.
    # The key is named for what the page IS, not for a user-prefix route that
    # Homebrew does not support.
    "brew_install": "https://docs.brew.sh/Installation",
    # The sentence the IT-handoff line rests on: policy can deny virtualization
    # features per machine, Administrators-group membership notwithstanding.
    "win_policy_lockdown": (
        "https://learn.microsoft.com/en-us/troubleshoot/windows-server/"
        "virtualization/block-users-from-running-virtualization-features-"
        "on-specific-computers"
    ),
    # Where the accelerator's status is stated, and the page an implementer
    # must re-read before any date reaches tester-facing text.
    #
    # Verified against the page itself, which CORRECTED what research had
    # claimed -- the reason the re-read is mandatory rather than advisory:
    #   * HAXM is REMOVED, not merely deprecated: "HAXM support is removed from
    #     the Emulator" (v36.2.11, 2025-10-09). Research had said "deprecated
    #     Jan 2023"; the page states no such date, so that claim was dropped.
    #   * AEHD has an announced sunset: "The Android Emulator hypervisor driver
    #     (AEHD) will sunset on December 31, 2026" (v37.1.11, 2026-07-30).
    #     Research's date was right; its provenance is now first-hand.
    #
    # NEITHER DATE IS PRINTED FOR A TESTER. The safe tester-facing form is
    # "use WHPX" with no date and no version, because a wrong date in install
    # advice is precisely the failure this module exists to prevent -- and one
    # of the two dates WAS wrong until the page was read.
    "emulator_release_notes": "https://developer.android.com/studio/releases/emulator",
}

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

#: Every install/provision step this server can propose, with the ADMIN-FREE
#: route named FIRST. ``needs_admin`` is a property of the STEP; whether it is
#: attemptable is a property of the step AND the verdict, which is why the two
#: live apart and ``attemptability`` joins them.
#:
#: ``needs_network`` is a SECOND precondition, kept separate for the same
#: reason. This module probes PRIVILEGE and nothing else, so "your account may
#: attempt this" is the only claim it can make. A step that fetches something
#: also needs the fetch to succeed, and on a corporate machine it often does
#: not: an authenticating proxy or a TLS-inspecting middlebox refuses or
#: rewrites the download, and neither is a privilege problem. Saying only
#: "attemptable: yes" to a tester in that position sends them to retry a route
#: that cannot work and tells them nothing about why -- the same shape as
#: naming a remedy the base text does not contain.
STEPS: tuple[dict, ...] = (
    {
        "step": "install adb (platform-tools)",
        "needs_admin": False,
        "needs_network": True,
        "admin_free_route": (
            "Unpack the platform-tools zip into your home directory and add it "
            "to PATH. No administrator rights are involved: "
            + REFERENCES["platform_tools_zip"]
        ),
    },
    {
        "step": "provision the Android SDK (cmdline-tools)",
        "needs_admin": False,
        "needs_network": True,
        "admin_free_route": (
            "Unpack the cmdline-tools zip under your own home directory; this "
            "server provisions into ~/.qa-agents/mobile/ and writes nothing "
            "outside $HOME: " + REFERENCES["cmdline_tools_zip"]
        ),
    },
    {
        "step": "install via a package manager (winget / brew)",
        "needs_admin": False,
        "needs_network": True,
        "admin_free_route": (
            "On Windows, winget itself runs fine as a standard user, and "
            "winget install --scope user chooses a per-user install LOCATION "
            "-- but it is NOT an elevation bypass: Microsoft documents that a "
            "user-scope package may still ask for administrator approval, and "
            "if you decline, the install fails. So try it if you like, and if "
            "it asks to elevate and you cannot, use the zip route above, "
            "which never prompts. Option: "
            + REFERENCES["winget_scope"]
            + " ; the elevation caveat: "
            + REFERENCES["winget_scope_uac"]
            + ". On macOS, INSTALLING Homebrew itself needs an administrator "
            "(there is no supported non-admin route), but brew install for a "
            "regular formula afterwards does not -- so if Homebrew is already "
            "there, this step needs nobody: "
            + REFERENCES["brew_install"]
        ),
    },
    {
        "step": "enable hardware virtualization (Windows HypervisorPlatform)",
        # THE ONLY OS-SPECIFIC STEP. Without this key it was joined to every
        # verdict, so a macOS tester read "enable the Windows
        # HypervisorPlatform -- attemptable: unknown. Hand IT this step" two
        # bullets above another saying macOS has nothing to enable. "unknown"
        # also OVERSTATED it: the step is inapplicable here, not undetermined.
        "os": "windows",
        "needs_admin": True,
        "admin_free_route": (
            "There is no admin-free route for this one: the feature switch "
            "(Enable-WindowsOptionalFeature -Online -FeatureName "
            "HypervisorPlatform, in an ELEVATED PowerShell) and the reboot "
            "both need administrator rights, and Group Policy can deny them "
            "even to a member of the Administrators group. Hand IT this step. "
            "Once virtualization is on, a NON-administrator account runs the "
            "emulator normally: "
            + REFERENCES["win_policy_lockdown"]
            + " and "
            + REFERENCES["emulator_accel"]
        ),
    },
    {
        "step": "create an AVD and launch the emulator",
        "needs_admin": False,
        "admin_free_route": (
            "Runs entirely inside your home directory once virtualization "
            "is on. On macOS there is nothing to enable and no administrator "
            "involved at all: Hypervisor.framework is either present on the "
            "machine or it is not, with no enabling step and no rights to "
            "grant -- stated as fact here because the only source found for "
            "it was a developer forum thread, which is not a vendor doc: "
            + REFERENCES["emulator_accel"]
        ),
    },
)

#: Per-process cache. Replaced wholesale by ``probe(refresh=True)``.
_CACHE: dict | None = None


def _run(argv: list[str], timeout: float = PROBE_TIMEOUT_S) -> tuple[int, str, str]:
    """ONE subprocess seam: no shell, a mandatory timeout, and never raises.

    ``timeout`` is a parameter rather than a constant read inside, mirroring
    ``mobile.platform_info._run_sync``, so a caller holding the probe's shared
    deadline can hand down whatever is LEFT of the budget instead of granting a
    fresh full timeout to every call in a sequence.

    Tests patch this single name, so no test ever queries the real machine, and
    an rc of -1 is the module's own "could not ask" -- distinct from a real
    non-zero rc, which is evidence.
    """
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=max(0.1, float(timeout)),
            shell=False,
            check=False,
        )
        out = (done.stdout or b"").decode("utf-8", "replace")
        err = (done.stderr or b"").decode("utf-8", "replace")
        return int(done.returncode), out, err
    except Exception as exc:
        logger.debug("host_privileges probe could not run %s: %s", argv[:1], exc)
        return -1, "", str(exc)


def _host_os() -> str:
    if sys.platform == "win32":
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    return OTHER


def _slice(deadline: float) -> float:
    """What is LEFT of the probe's ONE budget, capped by the per-call timeout.

    Every subprocess in this module is started with this, so the sum of the
    calls cannot exceed ``PROBE_BUDGET_S`` no matter how many there are. A
    non-positive result means the budget is spent and the caller must return
    UNDETERMINED rather than start another process.
    """
    return min(float(PROBE_TIMEOUT_S), deadline - time.monotonic())


def _budget_spent(method: str) -> dict:
    """Out of time is UNDETERMINED, never a denial -- same rule as everywhere."""
    return {
        "elevated": False,
        "can_elevate": False,
        "undetermined": True,
        "method": method,
    }


def _posix_verdict(deadline: float) -> dict:
    """POSIX: euid, then a NON-INTERACTIVE sudo test.

    Four clauses, each individually decisive, each with its own fixture:
    root; ``sudo -n`` accepted; ``sudo -n`` refused BECAUSE a password is
    needed (the account is a sudoer); ``sudo -n`` refused because the account
    is not a sudoer. Anything else is UNDETERMINED.
    """
    if getattr(os, "geteuid", None) is not None and os.geteuid() == 0:
        return {
            "elevated": True,
            "can_elevate": True,
            "undetermined": False,
            "method": "geteuid",
        }
    budget = _slice(deadline)
    if budget <= 0:
        return _budget_spent("probe budget spent before the sudo check")
    # `-n` is what makes this non-prompting. Removing it turns an advisory
    # probe into a password prompt inside a stdio server.
    rc, _out, err = _run(["sudo", "-n", "true"], timeout=budget)
    low = (err or "").lower()
    if rc == 0:
        return {
            "elevated": False,
            "can_elevate": True,
            "undetermined": False,
            "method": "sudo -n",
        }
    if any(marker in low for marker in _SUDOER_MARKERS):
        return {
            "elevated": False,
            "can_elevate": True,
            "undetermined": False,
            "method": "sudo -n password-required",
        }
    if any(marker in low for marker in _DENIED_MARKERS):
        return {
            "elevated": False,
            "can_elevate": False,
            "undetermined": False,
            "method": "sudo -n sudoers-denied",
        }
    return {
        "elevated": False,
        "can_elevate": False,
        "undetermined": True,
        "method": "sudo -n unreadable",
    }


def _windows_verdict(deadline: float) -> dict:
    """Windows: IsInRole for the CURRENT token, then Administrators-group SID.

    Being in the group and running elevated are different facts, and the
    product needs both: a group member who is not elevated CAN elevate, while a
    non-member cannot, and Group Policy can deny even a member. An unreadable
    probe is UNDETERMINED, never a denial.

    TWO sequential subprocesses, and they share ONE deadline: each is started
    with what is left of ``PROBE_BUDGET_S``, so the wait a caller feels is
    bounded by that single number rather than by the sum of the per-call
    timeouts. Out of budget returns UNDETERMINED instead of starting the second
    call.

    NO WINDOWS MACHINE HAS RUN THIS. Both branches are graded by fixtures only,
    which is stated rather than implied.
    """
    budget = _slice(deadline)
    if budget <= 0:
        return _budget_spent("probe budget spent before the IsInRole check")
    rc, out, _err = _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "([Security.Principal.WindowsPrincipal]"
            "[Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
        ],
        timeout=budget,
    )
    lines = [ln.strip().lower() for ln in (out or "").splitlines() if ln.strip()]
    state = lines[-1] if lines else ""
    if rc != 0 or state not in ("true", "false"):
        return {
            "elevated": False,
            "can_elevate": False,
            "undetermined": True,
            "method": "IsInRole unreadable",
        }
    if state == "true":
        return {
            "elevated": True,
            "can_elevate": True,
            "undetermined": False,
            "method": "IsInRole",
        }
    group_budget = _slice(deadline)
    if group_budget <= 0:
        return _budget_spent("probe budget spent before the group check")
    grc, gout, _gerr = _run(["whoami", "/groups"], timeout=group_budget)
    if grc != 0:
        return {
            "elevated": False,
            "can_elevate": False,
            "undetermined": True,
            "method": "group membership unreadable",
        }
    member = ADMIN_SID in (gout or "")
    return {
        "elevated": False,
        "can_elevate": member,
        "undetermined": False,
        "method": "Administrators SID " + ("present" if member else "absent"),
    }


def attemptability(verdict: dict) -> list[dict]:
    """``STEPS`` joined to a verdict. ``attemptable`` is THREE-VALUED.

    ``"unknown"`` rather than ``"no"`` on an undetermined verdict: a step this
    server would happily propose today must not read as blocked because a probe
    could not answer.

    STEPS ARE FILTERED BY THE HOST OS FIRST. ``attemptable`` answers "can this
    account do it", which is a question only worth asking about a step that
    applies here at all.

    ``attemptable`` SPEAKS ONLY TO PRIVILEGE. This module probes whether the
    account can elevate and nothing else, so "yes" means "your rights do not
    block this" -- not "this will work". A step that fetches something also
    needs the fetch to succeed, and on a managed machine an authenticating
    proxy or a TLS-inspecting middlebox refuses or rewrites the download while
    the account's rights are perfectly fine. Those steps carry
    ``needs_network``, so a consumer can say what has and has not been
    established rather than letting "yes" imply a reachability check nobody
    ran.
    """
    rows: list[dict] = []
    host_os = str(verdict.get("os") or "")
    for step in STEPS:
        # INAPPLICABLE IS NOT A THREE-VALUED ANSWER, it is not a row. Marking
        # it would add a fourth value every consumer must learn; dropping it
        # says the same thing and needs no branch anywhere. A step with no
        # ``os`` applies everywhere, and an unknown host OS shows everything --
        # failing OPEN, because hiding a step a tester could have taken is
        # worse than showing one they cannot.
        step_os = str(step.get("os") or "")
        if step_os and host_os and step_os != host_os:
            continue
        if not step["needs_admin"]:
            answer = "yes"
        elif verdict.get("elevated") or verdict.get("can_elevate"):
            answer = "yes"
        elif verdict.get("undetermined"):
            answer = "unknown"
        else:
            answer = "no"
        rows.append(
            {
                "step": step["step"],
                "needs_admin": bool(step["needs_admin"]),
                "needs_network": bool(step.get("needs_network")),
                "attemptable": answer,
                "admin_free_route": step["admin_free_route"],
            }
        )
    return rows


def verdict_sentence(content: dict) -> str:
    """The one sentence every consumer renders, so three of them cannot drift."""
    name = DISPLAY.get(str(content.get("os")), "this OS")
    if content.get("undetermined"):
        return (
            "Whether this account can elevate on "
            + name
            + " is UNDETERMINED -- the check could not be read, which is NOT "
            "the same as no administrator rights. Nothing is blocked by it."
        )
    if content.get("elevated"):
        return "This session is already running elevated on " + name + "."
    if content.get("can_elevate"):
        return (
            "This account can elevate on "
            + name
            + " when asked (it is not elevated right now)."
        )
    return (
        "This account cannot elevate on "
        + name
        + ", so use the admin-free route below or hand the admin step to IT."
    )


def probe(refresh: bool = False) -> dict:
    """``{"error", "content"}`` -- the shape used across ``tools/``. Never raises.

    Cached per process, and the reply SAYS so: ``content["cached"]`` plus
    ``content["refresh_hint"]``. ``refresh=True`` discards the cache and probes
    again, because the server outlives the machine's configuration.
    """
    global _CACHE
    try:
        if _CACHE is not None and not refresh:
            cached = dict(_CACHE)
            cached["cached"] = True
            return {"error": None, "content": cached}
        # ONE deadline for the WHOLE probe, set here and handed down, because
        # the Windows path runs two subprocesses in sequence and a per-call
        # timeout bounds neither of the things a caller cares about: the wait
        # it will actually feel. See PROBE_BUDGET_S.
        deadline = time.monotonic() + float(PROBE_BUDGET_S)
        os_name = _host_os()
        if os_name == WINDOWS:
            core = _windows_verdict(deadline)
        else:
            core = _posix_verdict(deadline)
        content = {
            "os": os_name,
            "arch": str(platform.machine() or ""),
            "elevated": bool(core["elevated"]),
            "can_elevate": bool(core["can_elevate"]),
            "undetermined": bool(core["undetermined"]),
            "method": str(core["method"]),
            "cached": False,
            "refresh_hint": REFRESH_HINT,
        }
        content["summary"] = verdict_sentence(content)
        content["steps"] = attemptability(content)
        _CACHE = dict(content)
        return {"error": None, "content": content}
    except Exception as exc:
        logger.exception("host_privileges.probe failed")
        return {"error": str(exc), "content": None}
