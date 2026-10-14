"""Jira MCP: verify probe helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from tools.jira_mcp_access import _split_envelope
from tools.jira_mcp_connect import connect_steps
from tools.jira_mcp_errors import _NO_IDENTITY, _carries_error_key, _error_text
from tools.jira_mcp_hint import connect_hint_line
from tools.jira_mcp_identity import _has_identity, _identity_label
from tools.jira_mcp_tool_names import verify_tool_name
from tools.jira_mcp_verify_render import _render_verified


def _failed_result(failure: str) -> tuple[str, str]:
    """The verdict for an error marker."""
    return (
        "failed",
        "\u274c **Not connected** -- that Atlassian call did not "
        f"return an identity: {failure or _NO_IDENTITY}\n\n" + connect_steps(),
    )


def _empty_result() -> tuple[str, str]:
    """The verdict for an empty probe object."""
    return (
        "unknown",
        "\u26a0\ufe0f **That verification result was empty**, so I "
        "still can't confirm the Atlassian connection. Re-run "
        f"`{verify_tool_name()}` and pass its RAW JSON result as "
        "`atlassian_verify_json`.\n\n" + connect_hint_line(),
    )


def _no_identity_result() -> tuple[str, str]:
    """The verdict for a payload with no error and no identity."""
    return (
        "unknown",
        "\u26a0\ufe0f **That result carried no identity**, so I still "
        "can't confirm the Atlassian connection. A working "
        f"`{verify_tool_name()}` call returns your account details. "
        "Re-run it and pass its RAW JSON result as "
        "`atlassian_verify_json`, or pass "
        '`{"error": "<what happened>"}` if the call failed.\n\n' + connect_hint_line(),
    )


# THE INVARIANT, in the order it has to be applied:
#   1. an error marker carrying TEXT is a failure, whatever else is here;
#   2. otherwise identity evidence wins -- an EMPTY errorMessages/errors
#      container is what a SUCCESSFUL Jira envelope carries, and letting
#      it beat a valid account_id persisted "not connected" for a week
#      about a working connection;
#   3. an error marker with no text and no identity is still a failure;
#   4. anything else establishes nothing -- "unknown", never written.
# A payload with no error and no identity establishes NOTHING: a guess
# dressed as a result would silence the real question for the week the
# verdict is persisted. An unrecognized shape is "unknown".
# `identified` DECIDES; the label only renders. Written the other way
# round -- label first, evidence as the fallback -- a bare `name` was
# its own proof, which is how a Jira status called "High" reported
# "connected as High" while _has_identity said False.
def _probe_verdict(probe: dict, sites: object) -> tuple[str, str]:
    """The verdict for the identity half of a verification payload."""
    failure = _error_text(probe)
    identified = _has_identity(probe)
    if failure or (_carries_error_key(probe) and not identified):
        return _failed_result(failure)
    if not probe:
        return _empty_result()
    who = (
        (_identity_label(probe) or "connected (identity payload received)")
        if identified
        else ""
    )
    if not who:
        return _no_identity_result()
    return _render_verified(who, sites)


def _probe_and_sites(payload: dict) -> tuple[dict, object]:
    """Split a payload into the identity half and the accessible-sites half."""
    # The blob may carry BOTH probe results. `probe` is the half that answers
    # "is there a session"; every identity check runs on it rather than on the
    # whole envelope, so a string inside a site entry can never be read as an
    # identity.
    identity, sites, envelope = _split_envelope(payload)
    return (identity if envelope else payload), sites
