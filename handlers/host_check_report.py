"""The host-privileges report: header lines and one block per install step."""

from __future__ import annotations

from tools import host_privileges

_HEADER_TAIL = [
    "",
    "This is advisory. It does not block a run, a provision or an install.",
    "",
    "### Steps, admin-free route first",
    "",
]

_NEEDS_NETWORK_LINE = (
    "  - This step downloads from the internet. Nothing here checked "
    "that the download can reach you: on a managed machine a proxy "
    "or TLS inspection can refuse it while your account rights are "
    "fine. If it fails with a certificate or proxy error, that is "
    "not a permissions problem and elevating will not fix it."
)


# What OS is this, and can this account elevate? ADVISORY, never a refusal.
#
# Rendered from ``tools.host_privileges`` -- the ONE producer of the verdict,
# so this report, the mobile preflight advisory, the virtualization fix text
# and the tooling install rows cannot disagree. Nothing here blocks anything:
# the point is that the calling agent stops proposing an elevated command to
# a tester who cannot run it, and stops sending a tester who CAN elevate off
# to IT.
#
# Never raises. An ``error`` verdict degrades to one advisory line, because a
# failed probe must not read as a denial.
async def handle_host_check(refresh: bool = False) -> str:
    """What OS is this, and can this account elevate? ADVISORY, never a refusal."""
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


def _cached_line(content: dict) -> str:
    if content.get("cached"):
        return "- **Cached:** yes. " + str(content.get("refresh_hint") or "")
    return "- **Cached:** no, probed just now. " + str(
        content.get("refresh_hint") or ""
    )


def _host_check_header(content: dict) -> list[str]:
    """Report lines above the per-step list."""
    return [
        "## Host check",
        "",
        "- **OS:** "
        + host_privileges.DISPLAY.get(str(content.get("os")), "unknown")
        + "  |  **arch:** "
        + str(content.get("arch") or "unknown"),
        "- **Verdict:** " + str(content.get("summary") or ""),
        "- **How it was checked:** " + str(content.get("method") or ""),
        _cached_line(content),
        *_HEADER_TAIL,
    ]


# `attemptable` is a PRIVILEGE answer and nothing else. Saying only
# "yes" to a tester behind an authenticating proxy or a
# TLS-inspecting middlebox sends them to retry a download that
# cannot work and tells them nothing about why -- the rights really
# are fine. Nothing here probes reachability, so this says what was
# checked rather than implying a check nobody ran.
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
        lines.append(_NEEDS_NETWORK_LINE)
    return lines
