"""Moved from handlers (refactor P2): jira."""

from __future__ import annotations

import json
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from config.settings import settings
from handlers import _common, _elicit, _reply
from tools.jira_fetcher import verify_jira_access
from tools.jira_mcp import (
    connect_steps,
    fetch_tool_name,
    verify_directive,
    verify_outcome,
)

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# Handlers (async, never-raise, return concise markdown)
# --------------------------------------------------------------------------- #


def _has_control_chars(value: str) -> bool:
    """True if *value* contains any character that could split a .env
    value across lines. Covers C0/C1 controls (0x00-0x1F, 0x7F-0x9F)
    plus the Unicode line/paragraph separators U+2028/U+2029 -- the full
    set ``str.splitlines()`` recognizes, so a value that survives this
    guard can never inject a rogue KEY= line when .env is next re-parsed
    (str.splitlines() also breaks on U+0085/U+2028/U+2029)."""
    return any(
        ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "\u2028\u2029"
        for ch in str(value)
    )


async def handle_configure_jira(
    base_url: str = "",
    email: str = "",
    api_token: str = "",
    atlassian_verify_json: str = "",
    *,
    verify: bool = True,
    progress: _common.ProgressCb = None,
) -> str:
    """2026-08-01: there are no Jira credentials for this server to configure.

    Jira is read through the CALLING AGENT's own Atlassian MCP connection
    (mcp.atlassian.com, OAuth 2.1, Jira Cloud), so nothing is stored here any
    more. The tool is KEPT rather than removed: clients cache their tool list,
    and testers still say "configure Jira" -- so it must answer with the real
    next step instead of vanishing.

    Deliberately writes NOTHING. Silently accepting an API token the server can
    no longer use would be the worst outcome: the tester would believe Jira was
    wired up and then get an empty ticket. The arguments are accepted, ignored,
    and never logged or echoed. Never raises.

    2026-08-03 -- this is ALSO the return half of the live connection check.
    Called with no arguments it now hands the agent a directive to make one
    read-only ``atlassianUserInfo`` call; called back with that call's raw JSON
    in ``atlassian_verify_json`` it parses the blob defensively (size-capped,
    ``json.loads`` only, never eval, no assumed schema) and reports a REAL
    verified / not-connected verdict. That blob is read once, never persisted
    and never logged -- only its PRESENCE reaches the audit record, and only the
    resulting yes/no, its timestamp and the probe tool's NAME reach the verdict
    store (``tools/verify_store.py``). No account id, email or display name is
    kept, which is what the tester-facing reply promises.
    """
    try:
        host, malformed_url = _echoed_jira_host(base_url)
        await _common._emit(progress, "\U0001f517 Checking how Jira is connected\u2026")
        verification = str(atlassian_verify_json or "").strip()
        await _audit_configure_jira(bool(verification))
        if verification:
            return await _verified_jira_reply(verification)
        return _jira_connect_reply(host, malformed_url, bool(email or api_token))
    except Exception as exc:
        logger.exception("handle_configure_jira failed")
        _common._capture_error(exc, "qa_configure_jira")
        return connect_steps()


async def _audit_configure_jira(verification_supplied: bool) -> None:
    """Audit row for `handle_configure_jira`; nothing is stored, only PRESENCE."""
    await _common._audit(
        "mcp_configure_jira",
        detail={
            "mode": "mcp",
            "credentials_stored": False,
            # PRESENCE only -- the payload itself is never logged.
            "verification_supplied": verification_supplied,
        },
    )


def _echoed_jira_host(base_url: str) -> tuple[str, bool]:
    """``(host_to_echo, malformed)`` for the *base_url* argument."""
    # A hostile base_url can carry a newline: urlparse() STRIPS it, so
    # the tail ("QA_UPDATE_REPO=attacker/repo") would be glued onto the
    # hostname and rendered into the tester's chat. Nothing is written
    # to .env any more, so this can no longer inject a setting -- but
    # echoing it is still wrong, so a value carrying any line-splitting
    # character loses its host mention entirely rather than being
    # partially rendered. The tester still learns their input looked
    # malformed -- just never the raw value.
    if not base_url:
        return "", False
    if _has_control_chars(base_url):
        return "", True
    candidate = base_url if "://" in base_url else "https://" + base_url
    return _reply._clip_echo((urlparse(candidate).hostname or "").lower(), 80), False


async def _verified_jira_reply(verification: str) -> str:
    """Return half of the live check: parse the hand-back, persist the verdict."""
    # The AGENT already made the read-only atlassianUserInfo call with its OWN
    # Atlassian MCP connection, so all that is left here is parsing what it
    # handed back. No LLM call, no HTTP request.
    status, message = verify_outcome(verification)
    # 2026-09-01 (item A): the VERDICT is persisted, the payload is not.
    # Only {verified, at, tool} reaches the store -- never the account
    # id, the email, the display name, or any fragment of the raw JSON
    # -- so the "we did not keep that identity" promise this reply makes
    # still holds, while qa-doctor stops re-raising a question that was
    # just answered (and can therefore reach "Ready" again).
    #
    # "unknown" establishes NOTHING and deliberately does not overwrite
    # an earlier verdict: an agent that fumbles the hand-back must not
    # be able to erase a good result.
    # 2026-09-01: five statuses, not three. wrong_site is a NEGATIVE
    # verdict -- the sign-in works and cannot reach this tester's Jira --
    # so it persists as verified=False and reads as "failed" downstream.
    # verified_no_access is a positive identity with the access half
    # unchecked, recorded so the report can say which of the two it is.
    _persistable = {
        "verified": (True, True),
        "verified_no_access": (True, False),
        "wrong_site": (False, True),
        "failed": (False, False),
    }
    if status not in _persistable:
        return message
    from tools.jira_mcp import probe_provenance
    from tools.verify_store import record_verdict

    _verified_flag, _site_checked = _persistable[status]
    _stored = await record_verdict(_verified_flag, probe_provenance(), _site_checked)
    # verify_store never raises -- it reports. A discarded report is
    # how the reply came to promise a week of remembered state that
    # a locked suites.db had not written.
    if not (isinstance(_stored, dict) and _stored.get("content")):
        logger.warning(
            "verify_store: the verdict was not recorded (%s)",
            (_stored or {}).get("error"),
        )
        message = _verdict_unrecorded_message(message)
    return message


def _verdict_unrecorded_message(message: str) -> str:
    """The verified reply with its week-of-remembered-state promise withdrawn."""
    return message.replace(
        "It records only that a check succeeded, when, and "
        "which tool was called, so `qa-doctor` stops asking "
        "again for the next week.",
        "I could not write this result down, so `qa-doctor` "
        "will ask again -- the check itself still stands.",
    )


def _jira_connect_reply(host: str, malformed_url: bool, had_credentials: bool) -> str:
    """The "nothing was saved" reply of `handle_configure_jira` (no verification)."""
    preamble = (
        "\u2139\ufe0f **Nothing was saved -- and nothing needs to be.**\n\n"
        if had_credentials
        else ""
    )
    if malformed_url:
        preamble += (
            "\u26a0\ufe0f That URL looked malformed (it contained a line "
            "break or control character), so I ignored it.\n\n"
        )
    where = f" for `{host}`" if host else ""
    return (
        preamble + f"Jira access{where} no longer uses an API token stored on this "
        "machine. It runs through **your own Atlassian MCP connection** "
        "(OAuth, in your editor), so there is nothing to copy, nothing to "
        "rotate, and your own Jira permissions apply.\n\n"
        + connect_steps()
        + "\n\n"
        + verify_directive()
    )


def _jira_config_hint(url: str) -> str:
    """Deprecated shim -- always returns "".

    Until 2026-08-01 this produced one-time-setup instructions for the
    JIRA_EMAIL / JIRA_API_TOKEN pair. Those credentials no longer exist: Jira is
    read through the calling agent's own Atlassian MCP connection, so there is
    nothing to configure here and no hint worth giving. The SINGLE source of
    actionable guidance is tools/jira_mcp.build_fetch_directive /
    not_connected_message, which the fetch itself returns -- two competing
    messages for the same failure is precisely what confused testers before.

    Kept (not deleted) so both call sites in _ground_and_gate stay structurally
    identical to the server path they were converged from; both now fall
    through. Never raises.
    """
    return ""


# Failure reasons are assembled from an UNTRUSTED response (a Content-Type, a
# ticket-supplied filename) and land inside a markdown reply, so they go through
# the same shape of allowlist as _safe_image_name rather than being interpolated
# raw. `re` is deliberately not imported by this module, hence a set.
_FETCH_REASON_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._:/()-,"
)


def _safe_fetch_reason(raw) -> str:
    """Sanitized, capped rendering of ONE fetch-failure reason. Never raises."""
    try:
        return "".join(ch for ch in str(raw or "") if ch in _FETCH_REASON_ALLOWED)[
            :200
        ].strip()
    except Exception:
        return ""


def _safe_host(raw) -> str:
    """Hostname of *raw* when it is a URL, else "". Never raises."""
    try:
        return urlparse(str(raw or "")).hostname or ""
    except Exception:
        return ""


def _looks_like_jira_host(url: str) -> bool:
    """True when a pasted URL's host looks like a Jira / Atlassian instance.
    Shared by the config hint and the access pre-flight. Never raises."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return "atlassian.net" in host or host.startswith("jira.") or ".jira." in host


def _skip_ui_extraction(url: str, attached_images: list | None = None) -> bool:
    """True when scraping *url* for app UI structure cannot pay off (ops-4b).

    extract_ui_elements exists to read the structure of the APP UNDER TEST. A
    Jira link is not that app -- the page is the TICKET -- so the call returns
    "0 headings, 0 fields, 0 buttons, 0 links (method=none)" and contributes
    nothing: _build_ui_prompt_block already renders "" for zero elements and
    _complexity_signal_score already scores 0. Skipping is therefore behaviour-
    preserving, and it replaces a log line that reads like a failed extraction
    with one that states the real reason.

    Detection deliberately has TWO arms: _looks_like_jira_host covers the
    atlassian.net / jira.* shapes, and a match against the configured
    JIRA_BASE_URL host covers a self-hosted instance on a custom domain
    (tickets.example.com), which the shape check alone would miss.

    A MOBILE-only run never reaches the caller's extraction block at all (it is
    guarded by _is_url and there is no URL), so *attached_images* is accepted
    and documented rather than gated on: a real web-app URL submitted WITH
    screenshots still has a live page worth extracting, and skipping there would
    discard useful signal. Jira + mobile is covered by the Jira arm.

    Never raises: an unparseable URL returns False and falls through to the
    normal extraction path, so this can only ever skip known-useless work.
    """
    try:
        if _looks_like_jira_host(url):
            return True
        host = (urlparse(url).hostname or "").lower()
        configured = (urlparse(settings.jira_base_url).hostname or "").lower()
        return bool(host and configured and host == configured)
    except Exception:
        logger.debug("_skip_ui_extraction check failed -- extracting", exc_info=True)
        return False


def _jira_token_steps(host: str, *, verify_error: str = "") -> str:
    """Professional "no access" message + how to CONNECT (no API token any more).

    Since 2026-08-01 Jira is read through the calling agent's own Atlassian MCP
    connection, so the old "create an API token" steps would send the tester to
    a page whose output this server cannot use. Delegates to the single source
    of truth in tools/jira_mcp so all four supported clients stay documented in
    exactly one place. *verify_error* (already sanitized) is an optional
    one-line reason. Never echoes a secret; never raises.
    """
    reason = f"\n\n_Reason: {verify_error}_" if verify_error else ""
    return (
        f"\u26a0\ufe0f **I don't have access to this Jira instance (`{host}`).**"
        + reason
        + "\n\n"
        + connect_steps()
    )


async def _jira_preflight(
    url: str,
    *,
    ask_text: _elicit.AskCb = None,
    choose: _common.ChooseCb = None,
    progress: _common.ProgressCb = None,
) -> Optional[str]:
    """Jira access pre-flight -- NETWORK-FREE since 2026-08-01.

    The server no longer holds Jira credentials, so there is nothing here it can
    verify: whether Jira is reachable is a property of the CALLING AGENT's
    Atlassian MCP connection, which a stdio subprocess cannot observe. The probe
    call is kept -- it is now tools/jira_mcp.verify_jira_access, which makes no
    HTTP request -- so this hook, its call sites and the tests that stub it keep
    their shape, and the happy path returns None so grounding proceeds to the
    fetch, where build_fetch_directive / not_connected_message give the ONE
    actionable message.

    The credential-elicitation loop is gone with the credentials: there is
    nothing a tester can type here that would help, and asking would be a dead
    end. ask_text / choose are accepted for signature compatibility and unused.

    Returns None to proceed, or a markdown string to short-circuit. Never raises.
    """
    if not _looks_like_jira_host(url):
        return None
    try:
        probe = await verify_jira_access()
        if probe.get("ok"):
            return None
        # A not-ok verdict can now only come from a test double or a future
        # replacement probe. Surface the connection steps rather than silently
        # continuing into a fetch that cannot succeed.
        return _jira_token_steps(
            (urlparse(url).hostname or "").lower(),
            verify_error=str(probe.get("error") or ""),
        )
    except Exception:
        logger.debug("Jira pre-flight failed -- proceeding to the fetch", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# v1.97.0 cursor-hardening (item 1): per-fetch Jira staging tray. Mirrors the
# _CAPTURE_TRAY shape exactly -- a token minted before the consuming call
# exists (by tools.jira_mcp.build_fetch_directive), redeemed once
# qa_prepare_test_cases/qa_generate_test_cases arrives with the same token,
# swept on every touch. Keeps a single raw Jira fetch result from ever having
# to survive inside one host-authored jira_content_json blob.
# --------------------------------------------------------------------------- #
_JIRA_STAGE_TRAY: dict = {}
_JIRA_STAGE_TRAY_TTL_S = 1800
_JIRA_STAGE_TRAY_MAX = 24
_JIRA_STAGE_PART_MAX_BYTES = 20 * 1024
_JIRA_STAGE_PARTS = ("issue", "parent", "siblings")


def _sweep_jira_stage_tray() -> None:
    """Evict stage-tray entries older than TTL or beyond MAX count (oldest
    first). Mirrors _sweep_capture_tray. Never raises."""
    try:
        now = time.time()
        for token, entry in list(_JIRA_STAGE_TRAY.items()):
            if now - float(entry.get("_ts") or 0) > _JIRA_STAGE_TRAY_TTL_S:
                _JIRA_STAGE_TRAY.pop(token, None)
        overflow = len(_JIRA_STAGE_TRAY) - _JIRA_STAGE_TRAY_MAX
        if overflow > 0:
            oldest = sorted(
                _JIRA_STAGE_TRAY.items(), key=lambda kv: kv[1].get("_ts") or 0
            )
            for token, _ in oldest[:overflow]:
                _JIRA_STAGE_TRAY.pop(token, None)
    except Exception:
        logger.debug("_sweep_jira_stage_tray failed", exc_info=True)


async def handle_stage_jira(stage_token: str, part: str, json_text: str) -> str:
    """Stage ONE raw Jira fetch result (issue/parent/siblings) under
    stage_token, redeemed later by _assemble_staged_jira_payload. Treats
    json_text as UNTRUSTED, exactly like jira_content_json -- stored as-is,
    parsed only once, at assembly time. Never raises."""
    try:
        _sweep_jira_stage_tray()
        token = str(stage_token or "").strip()
        if not token:
            return "No `stage_token` given -- call the fetch directive first."
        if part not in _JIRA_STAGE_PARTS:
            return (
                f"Unknown `part={part!r}` -- expected one of "
                f"{', '.join(_JIRA_STAGE_PARTS)}."
            )
        text = str(json_text or "")
        if len(text.encode("utf-8", "ignore")) > _JIRA_STAGE_PART_MAX_BYTES:
            return (
                "That staged part is too large to accept (over "
                f"{_JIRA_STAGE_PART_MAX_BYTES // 1024} KB). Re-fetch requesting "
                "only the fields the directive named."
            )
        entry = _JIRA_STAGE_TRAY.setdefault(token, {})
        entry[part] = text
        entry["_ts"] = time.time()
        _sweep_jira_stage_tray()
        return (
            f"Staged `{part}` part for this fetch. Stage the remaining parts, "
            "then call `qa_prepare_test_cases` with the same `stage_token`."
        )
    except Exception:
        logger.debug("handle_stage_jira failed", exc_info=True)
        return "Could not stage that part -- re-fetch and try again."


#: A stage token is minted by ``jira_mcp.build_fetch_directive`` as
#: ``secrets.token_urlsafe(16)``, so it is always URL-safe base64. Anything
#: else reaching a reply is host-supplied free text. Checked without ``re``:
#: this module has no module-level regex import and does not need one for a
#: character-class test.
_STAGE_TOKEN_EXTRA_CHARS = "-_"


def _is_stage_token_shaped(text: str) -> bool:
    """True when ``text`` could be a minted stage token: URL-safe base64, and
    long enough that no short free-text fragment passes by accident."""
    return 16 <= len(text) <= 64 and all(
        (ch.isascii() and ch.isalnum()) or ch in _STAGE_TOKEN_EXTRA_CHARS for ch in text
    )


def _safe_stage_token(value: object) -> str:
    """``value`` if it is shaped like a stage token, else a sanitised stand-in.

    Same discipline as ``host_mode.safe_prep_id`` and ``jira_mcp._sanitize_echo``:
    a token we do not recognise is still ECHOED -- a host that sent a typo needs
    to see what it sent -- but stripped of backticks and newlines and capped, so
    it can only ever read as a wrong token, never close its code span and write
    markdown into text the model then follows. Never raises.
    """
    try:
        text = str(value or "").strip()
        if _is_stage_token_shaped(text):
            return text
        flattened = "".join(
            " " if ch in "`\n\r\t" else ch for ch in text[:64] if ch.isprintable()
        )
        return " ".join(flattened.split())
    except Exception:  # pragma: no cover - defensive
        return ""


def _assemble_staged_jira_payload(stage_token: str) -> tuple:
    """(assembled_json_string, error) from the parts staged under
    stage_token. Requires at least `issue`; a malformed single part does not
    sink the others. Never raises."""
    try:
        token = str(stage_token or "").strip()
        entry = _JIRA_STAGE_TRAY.get(token) if token else None
        if not entry or not entry.get("issue"):
            return "", _no_issue_staged_message(token, entry)
        payload: dict = {}
        try:
            payload["issue"] = json.loads(entry["issue"])
        except Exception:
            return "", "The staged `issue` part is not valid JSON -- re-stage it."
        _add_optional_staged_parts(payload, entry)
        return json.dumps(payload), ""
    except Exception:
        logger.debug("_assemble_staged_jira_payload failed", exc_info=True)
        return "", "Could not assemble the staged Jira payload -- re-stage and retry."


def _no_issue_staged_message(token: str, entry: dict | None) -> str:
    """The refusal text for a stage token with no `issue` part staged."""
    # B1/B2 (2026-09-26, v1.98 scope B): the live TICKET-5645 failure was
    # an agent that read this exact refusal, concluded -- wrongly, two
    # turns after using the SAME bridge for part='parent'/'siblings' --
    # that its Atlassian MCP tool was "not in shell", and rebuilt its
    # own MCP client instead of making one more call it already had.
    # Lead with an explicit denial of that belief, name the real tool,
    # and give the literal next call with THIS token filled in.
    _also = [k for k in ("parent", "siblings") if entry and entry.get(k)]
    _used_note = (
        " -- the SAME bridge you already used for this ticket's " + " and ".join(_also)
        if _also
        else ""
    )
    return (
        "\u26a0\ufe0f **No `issue` part staged yet -- this is not a "
        "missing tool.** You already have `"
        + fetch_tool_name()
        + "`"
        + _used_note
        + ". Call it once more for the ticket itself, then stage "
        "that exact result with this literal call: "
        f"`qa_stage_jira(stage_token='{_safe_stage_token(token)}', part='issue', "
        "json=<json.dumps(that result)>)`. Once `issue` is staged, "
        "call `qa_prepare_test_cases` again with the SAME "
        "`feature_or_url` and this SAME `stage_token` -- do not pass "
        "`jira_content_json`."
    )


def _add_optional_staged_parts(payload: dict, entry: dict) -> None:
    """Add the parent and sibling parts that parse; a bad one is skipped."""
    for part, key in (("parent", "parent_issue"), ("siblings", "sibling_issues")):
        if entry.get(part):
            try:
                payload[key] = json.loads(entry[part])
            except Exception:
                logger.debug("staged %s part failed to parse", part, exc_info=True)


def _resolve_jira_content_json(jira_content_json: str, stage_token: str) -> tuple:
    """(resolved_jira_content_json, error) -- assembles from the stage tray
    only when the caller sent stage_token AND no explicit jira_content_json
    (an explicit argument always wins, so a legacy caller's jira_content_json
    is never silently overridden). Never raises."""
    try:
        explicit = str(jira_content_json or "").strip()
        if explicit or not stage_token:
            return jira_content_json, ""
        return _assemble_staged_jira_payload(stage_token)
    except Exception:
        logger.debug("_resolve_jira_content_json failed", exc_info=True)
        return jira_content_json, ""
