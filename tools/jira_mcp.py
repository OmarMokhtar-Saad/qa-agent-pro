"""Jira access via the HOST AGENT's own Atlassian MCP connection.

WHY THIS MODULE EXISTS
----------------------
Until 2026-08-01 ``tools/jira_fetcher.py`` read Jira over the REST API with
HTTP Basic Auth (``JIRA_EMAIL`` + ``JIRA_API_TOKEN``). That meant every tester
had to mint and paste a personal API token into a local ``.env``, and the
server held a long-lived credential it had to defend with a full SSRF stack.

Test-case generation is now permanently host ("boomerang") mode
(``QA_GENERATION_MODE`` is HARDCODED to ``"host"``), so there is ALWAYS an
interactive chat agent in the loop — and that agent can have its own OAuth 2.1
Atlassian MCP connection (``https://mcp.atlassian.com/v1/mcp/authv2``). So the
server stops fetching Jira itself and instead:

1. returns a DIRECTIVE telling the calling agent which of its own
   ``mcp__atlassian__*`` tools to call, and
2. accepts the fetched issue JSON back on the next call, normalizes it, and
   feeds the SAME grounded dict the REST path used to produce.

This is the same shape as the existing ``qa_prepare_test_cases`` /
``qa_submit_suite`` boomerang: the server never calls the host's tools, it only
returns instructions the agent acts on. Jira Cloud only (confirmed: no
Server/Data-Center users).

CONTRACT (Hard Rule, see CLAUDE.md)
-----------------------------------
* Nothing here EVER raises to a caller. Every public function returns a value
  (``{"error": ..., "content": None}`` for fetch-shaped results, ``""`` / ``{}``
  / ``[]`` for helpers).
* Host-supplied issue JSON is UNTRUSTED input: size-capped, shape-validated,
  ``json.loads``-only (never ``eval``), issue keys regex-gated, and every URL
  stripped out of BACKGROUND (parent / linked-issue) text.
* When the agent has no Atlassian MCP connection the result is a clear,
  actionable, per-client setup message — never an exception, never a silent
  empty suite.
* Callers still wrap the returned text via ``tools.untrusted.wrap_untrusted``
  before it reaches a model, exactly as they did for REST content.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from config.settings import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

# Host-submitted JSON is untrusted: refuse anything absurd before parsing it.
_MAX_PAYLOAD_BYTES = 2_000_000

# Cap on how many sibling sub-tasks / linked issues are listed as context.
_MAX_RELATED_ISSUES = 10

_MAX_ADF_DEPTH = 200

# Jira issue keys taken from a RESPONSE (parent.key, subtask keys, linked issue
# keys) are echoed back into tester-facing text and into the next directive.
# Treat them as untrusted: a strict syntax gate stops a crafted response from
# smuggling "../", an absolute URL, or a query string through.
#
# \Z (not $) so a trailing newline cannot slip past the anchor; the {0,63} bound
# keeps an absurdly long project prefix out of the directive entirely.
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{0,63}-\d+\Z")

# Every http(s) URL is stripped out of parent/linked-issue text before it
# reaches a prompt. That text is authored by OTHER people, so it is a strictly
# higher injection/phishing risk than the target ticket, and parent context is
# BACKGROUND ONLY -- it must never supply a navigation target (SHYJ-7154).
_URL_IN_TICKET_RE = re.compile(r"https?://\S+")

_AC_HEADING_RE = re.compile(r"(?im)^[\s>#*_-]*acceptance\s+criteria\s*:?\s*$")

# A line only counts as the NEXT section heading when it carries a real heading
# signal -- otherwise an ordinary short AC line ("Account is locked") would be
# mistaken for a heading and cut the block short (NB-021).
_NEXT_HEADING_RE = re.compile(
    r"""(?mx)
    ^[\s>*_-]*(?:
        \#{1,6}\s*\S.{0,40}$          # markdown heading: leading #(s)
      | [A-Za-z][\w /-]{1,40}:\s*$    # label line ending in a colon
      | [A-Z0-9][A-Z0-9 /_-]{1,40}$   # ALL-CAPS label (no lowercase letters)
    )
    """
)


def _tool_prefix() -> str:
    """Client-specific Atlassian MCP tool-name prefix.

    Claude Code / Claude Desktop expose the remote server's tools as
    ``mcp__atlassian__getJiraIssue``; other clients namespace differently, so
    the prefix is configurable (``QA_JIRA_MCP_TOOL_PREFIX``) rather than
    hardcoded into the tester-facing directive. Never raises.
    """
    try:
        value = str(getattr(settings, "qa_jira_mcp_tool_prefix", "") or "").strip()
    except Exception:  # pragma: no cover - settings is lenient by contract
        value = ""
    return value or "mcp__atlassian__"


# --------------------------------------------------------------------------- #
# URL / key helpers (pure)                                                     #
# --------------------------------------------------------------------------- #


def _valid_issue_key(value: object) -> str:
    """Return *value* when it is a syntactically valid Jira issue key, else "".

    Security gate for every key that comes back inside a host-submitted payload
    and is later echoed into text. Never raises.
    """
    if not isinstance(value, str):
        return ""
    return value if _ISSUE_KEY_RE.match(value) else ""


def selected_issue_key(url: str) -> str:
    """Ticket key carried in a board/backlog URL's query string, else "".

    Testers copy URLs like .../boards/1276?selectedIssue=KEY-1 straight from the
    board, and that path has no /browse/ segment. Never raises.
    """
    try:
        params = parse_qs(urlparse(url).query)
        for name in ("selectedIssue", "selectedIssueKey"):
            for value in params.get(name, []):
                key = _valid_issue_key(value)
                if key:
                    return key
    except Exception:
        logger.debug("selected_issue_key: parse failed", exc_info=True)
    return ""


def looks_like_jira_url(url: str) -> bool:
    """True when *url* names a Jira ISSUE: a Jira-Cloud-looking host (or the
    configured JIRA_BASE_URL host) AND a path/query that actually identifies an
    issue - ``/browse/``, ``/issues/`` or ``?selectedIssue=``.

    The path/query gate is a precondition, not a nicety: the same Atlassian host
    also serves Confluence (``/wiki/spaces/...``), dashboards, board and profile
    pages. Those are ordinary web pages, so they must FALL THROUGH to the
    SSRF-hardened generic fetcher rather than being answered with the "fetch this
    ticket with your Atlassian MCP" directive for a ticket that does not exist.
    Never raises.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    host_ok = "atlassian.net" in host or host.startswith("jira.") or ".jira." in host
    if not host_ok:
        try:
            configured = (urlparse(settings.jira_base_url or "").hostname or "").lower()
        except Exception:
            configured = ""
        host_ok = bool(configured) and host == configured
    if not host_ok:
        return False
    path = (parsed.path or "").rstrip("/")
    if "/browse/" in path or "/issues/" in path:
        return True
    return bool(selected_issue_key(url))


def issue_key_from_url(url: str) -> str:
    """Best-effort Jira issue key for *url* ("" when there isn't one).

    Handles ``/browse/KEY-1``, ``/issues/KEY-1``, and board/backlog URLs that
    carry ``?selectedIssue=KEY-1``. Never raises.
    """
    try:
        parsed = urlparse(url)
        path = (parsed.path or "").rstrip("/")
        for marker in ("/browse/", "/issues/"):
            if marker in path:
                key = _valid_issue_key(path.split(marker, 1)[1].split("/", 1)[0])
                if key:
                    return key
        return selected_issue_key(url)
    except Exception:
        logger.debug("issue_key_from_url: parse failed", exc_info=True)
        return ""


def _strip_urls(text: str) -> str:
    """Replace every http(s) URL in *text* with a placeholder. Never raises."""
    try:
        return _URL_IN_TICKET_RE.sub("[link removed]", text)
    except Exception:
        logger.exception("_strip_urls failed - dropping the text")
        return ""


# --------------------------------------------------------------------------- #
# Field extraction (pure; carried over verbatim from the REST implementation)  #
# --------------------------------------------------------------------------- #


def _extract_adf_text(node: dict, depth: int = 0) -> str:
    """Recursively extract plain text from Atlassian Document Format.

    The Atlassian MCP server returns the SAME ADF documents the REST API did, so
    this is unchanged. Guards against pathologically deep / self-referential
    blobs by capping recursion at _MAX_ADF_DEPTH.
    """
    if depth > _MAX_ADF_DEPTH:
        return ""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    content = node.get("content", [])
    if not isinstance(content, list):
        return ""
    parts = [_extract_adf_text(child, depth + 1) for child in content]
    sep = (
        "\n"
        if node.get("type")
        in ("paragraph", "tableRow", "tableCell", "bulletList", "listItem")
        else " "
    )
    return sep.join(p for p in parts if p.strip())


def _as_text(value: object) -> str:
    """Coerce a Jira field that may be ADF, plain text, or absent into text.

    The Atlassian MCP server may return either ADF (like REST v3) or an
    already-rendered string depending on the tool and the ``fields`` requested,
    so both are accepted. Never raises.
    """
    try:
        if isinstance(value, dict):
            return _extract_adf_text(value)
        if value is None:
            return ""
        return str(value)
    except Exception:
        logger.exception("_as_text failed - dropping the value")
        return ""


def _extract_ac_from_description(description: str) -> str:
    """Fallback AC extraction: scan a ticket description for an 'Acceptance
    Criteria' heading and return the block beneath it (QW-11 / I-023).

    Empty string when no such heading is present. Never raises.
    """
    try:
        if not description:
            return ""
        m = _AC_HEADING_RE.search(description)
        if not m:
            return ""
        rest = description[m.end() :].lstrip("\n")
        collected: list[str] = []
        for line in rest.splitlines():
            if (
                collected
                and _NEXT_HEADING_RE.match(line)
                and not re.match(r"^\s*[-*•\d]", line)
            ):
                break
            collected.append(line)
        return "\n".join(collected).strip()
    except Exception:
        logger.exception("_extract_ac_from_description failed - returning empty")
        return ""


def _extract_priority(fields: dict) -> str:
    """Priority is a {name: "High", ...} object, or absent on some issue types."""
    priority = fields.get("priority") if isinstance(fields, dict) else None
    if isinstance(priority, dict):
        return priority.get("name", "") or ""
    return ""


def _extract_names(items: object) -> list[str]:
    """Extract display names from a Jira field that's a list of either plain
    strings (labels) or {name: ...} objects (components). Never raises."""
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _comment_lines(records: list[dict]) -> list[str]:
    """Render comment records as the legacy "Author: body" strings.

    The public ``comments`` key has always had this shape, so keeping it
    byte-identical means every existing consumer and test is unaffected by the
    richer ``comments_meta`` records. Never raises.
    """
    try:
        out: list[str] = []
        for rec in records or []:
            body = str(rec.get("body") or "").strip()
            if body:
                out.append(f"{rec.get('author') or 'Unknown'}: {body}")
        return out
    except Exception:
        logger.exception("Rendering Jira comment lines failed")
        return []


def _effective_comment_cap() -> int:
    """How many comments to keep from the host-supplied payload.

    jira_max_comments defaults to 5 while qa_comment_reconcile_max_comments
    defaults to 50, so without this widening the deep-thread window would be a
    dead knob. Widens ONLY while the reconciler is enabled. Never raises.
    """
    try:
        base = int(settings.jira_max_comments)
    except Exception:
        base = 5
    try:
        if settings.qa_comment_reconcile_enabled:
            deep = int(settings.qa_comment_reconcile_max_comments)
            if deep > base:
                return deep
    except Exception:
        logger.debug("Comment-cap widening failed - using jira_max_comments")
    return base


def _extract_comment_records(fields: dict) -> list[dict]:
    """Comment RECORDS from an issue payload, OLDEST FIRST, capped.

    The Atlassian MCP ``getJiraIssue`` response embeds the thread at
    ``fields.comment.comments`` in ASCENDING (oldest-first) order. That is the
    order every downstream consumer wants, but a long thread must keep the
    NEWEST N (those carry the current requirements), so the TAIL is taken and
    the chronological order is preserved. Never raises; returns [] when
    JIRA_FETCH_COMMENTS is off or the cap is 0.
    """
    cap = _effective_comment_cap()
    if not settings.jira_fetch_comments or cap <= 0:
        return []
    try:
        container = fields.get("comment") if isinstance(fields, dict) else None
        if isinstance(container, dict):
            raw = container.get("comments")
        else:
            raw = container
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            body = _as_text(c.get("body")).strip()
            if not body:
                continue
            author = c.get("author")
            author = author if isinstance(author, dict) else {}
            out.append(
                {
                    "id": str(c.get("id") or ""),
                    "author": str(author.get("displayName") or "Unknown"),
                    "created": str(c.get("created") or ""),
                    "body": body,
                }
            )
        # Keep the NEWEST cap records but hand them back chronologically.
        return out[-cap:]
    except Exception:
        logger.exception("Extracting Jira comment records failed")
        return []


def _extract_image_attachments(fields: dict) -> list[dict]:
    """Image attachment METADATA ({filename, mime, size}) from an issue payload.

    Deliberately no ``url`` and no bytes: the Atlassian MCP server returns
    attachment metadata only, and this module makes NO outbound HTTP requests of
    its own (that is the whole point of the migration). Attachment BYTES are
    therefore unavailable on the MCP path -- the caller reports that to the
    tester rather than silently pretending the ticket had no screenshots.
    Never raises.
    """
    if not settings.jira_fetch_images or settings.jira_max_images <= 0:
        return []
    try:
        attachments = fields.get("attachment") if isinstance(fields, dict) else None
        if not isinstance(attachments, list):
            return []
        out: list[dict] = []
        for att in attachments:
            if not isinstance(att, dict):
                continue
            mime = str(att.get("mimeType") or "").lower()
            if not mime.startswith("image/"):
                continue
            out.append(
                {
                    "filename": str(att.get("filename") or "attachment"),
                    "mime": mime,
                    "size": att.get("size") or 0,
                }
            )
            if len(out) >= settings.jira_max_images:
                break
        return out
    except Exception:
        logger.exception("Extracting Jira image attachment metadata failed")
        return []


def _extract_parent_ref(fields: dict) -> dict | None:
    """{key, summary, issuetype} for this issue's parent, or None. Never raises."""
    try:
        parent = fields.get("parent") if isinstance(fields, dict) else None
        if not isinstance(parent, dict):
            return None
        key = _valid_issue_key(parent.get("key"))
        if not key:
            return None
        pfields = parent.get("fields")
        pfields = pfields if isinstance(pfields, dict) else {}
        itype = pfields.get("issuetype")
        return {
            "key": key,
            "summary": str(pfields.get("summary") or "").strip(),
            "issuetype": (
                str(itype.get("name") or "").strip() if isinstance(itype, dict) else ""
            ),
        }
    except Exception:
        logger.exception("Extracting the Jira parent reference failed")
        return None


def _extract_subtasks(fields: dict) -> list[dict]:
    """[{key, summary, status}] for this issue's sub-tasks (capped). Never raises."""
    try:
        items = fields.get("subtasks") if isinstance(fields, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = _valid_issue_key(item.get("key"))
            if not key:
                continue
            sfields = item.get("fields")
            sfields = sfields if isinstance(sfields, dict) else {}
            status = sfields.get("status")
            out.append(
                {
                    "key": key,
                    "summary": str(sfields.get("summary") or "").strip(),
                    "status": (
                        str(status.get("name") or "").strip()
                        if isinstance(status, dict)
                        else ""
                    ),
                }
            )
            if len(out) >= _MAX_RELATED_ISSUES:
                break
        return out
    except Exception:
        logger.exception("Extracting Jira sub-tasks failed")
        return []


def _extract_issuelinks(fields: dict) -> list[dict]:
    """[{relation, key, summary}] for linked issues (capped). Never raises."""
    try:
        items = fields.get("issuelinks") if isinstance(fields, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ltype = item.get("type")
            ltype = ltype if isinstance(ltype, dict) else {}
            for side, phrase_key in (
                ("outwardIssue", "outward"),
                ("inwardIssue", "inward"),
            ):
                issue = item.get(side)
                if not isinstance(issue, dict):
                    continue
                key = _valid_issue_key(issue.get("key"))
                if not key:
                    continue
                lfields = issue.get("fields")
                lfields = lfields if isinstance(lfields, dict) else {}
                out.append(
                    {
                        "relation": str(ltype.get(phrase_key) or "relates to").strip(),
                        "key": key,
                        "summary": str(lfields.get("summary") or "").strip(),
                    }
                )
                break
            if len(out) >= _MAX_RELATED_ISSUES:
                break
        return out
    except Exception:
        logger.exception("Extracting Jira issue links failed")
        return []


def _build_parent_context(
    parent: dict | None, subtasks: list[dict], issuelinks: list[dict]
) -> str:
    """Compose the BACKGROUND block for a ticket whose requirements live
    elsewhere.

    Plain text only: every http(s) URL is stripped and the result is capped at
    settings.jira_max_parent_chars. Returned under its OWN key
    (``parent_context``) and deliberately NOT merged into raw_text/description --
    a link inside somebody else's story must never become this ticket's
    navigation target (SHYJ-7154). Never raises.
    """
    try:
        has_parent = bool(parent and parent.get("key"))
        lines: list[str] = []
        if has_parent:
            label = parent.get("issuetype") or "issue"
            head = f"Parent {label} {parent['key']}: {parent.get('summary', '')}"
            lines.append(head.strip())
            desc = str(parent.get("description") or "").strip()
            if desc:
                lines += ["", desc]
            pac = str(parent.get("acceptance_criteria") or "").strip()
            if pac:
                lines += ["", "Parent acceptance criteria:", pac]
        if subtasks:
            heading = (
                "Other sub-tasks under the same parent (context only):"
                if has_parent
                else "Sub-tasks this story is broken down into (context only):"
            )
            lines += ["", heading]
            for sub_task in subtasks:
                status = str(sub_task.get("status") or "").strip()
                suffix = f" [{status}]" if status else ""
                lines.append(
                    f"- {sub_task['key']}: {sub_task.get('summary', '')}{suffix}".rstrip()
                )
        if issuelinks:
            lines += ["", "Linked issues (context only):"]
            lines += [
                f"- {ln.get('relation', 'relates to')} {ln['key']}: "
                f"{ln.get('summary', '')}".rstrip()
                for ln in issuelinks
            ]
        text = "\n".join(lines).strip()
        if not text:
            return ""
        cap = settings.jira_max_parent_chars
        return _strip_urls(text)[: cap if cap > 0 else 0]
    except Exception:
        logger.exception("Building the Jira parent context failed - omitting it")
        return ""


def _parent_body(parent_issue: object) -> dict:
    """{description, acceptance_criteria} from an optional second getJiraIssue
    payload for the parent story. {} when absent/unusable. Never raises."""
    try:
        if not settings.jira_fetch_parent:
            return {}
        fields = _issue_fields(parent_issue)
        if not fields:
            return {}
        description = _as_text(fields.get("description")).strip()
        ac_raw = _as_text(fields.get(settings.jira_ac_field)).strip()
        return {
            "description": description,
            "acceptance_criteria": (
                ac_raw or _extract_ac_from_description(description)
            ),
        }
    except Exception:
        logger.exception("Extracting the parent story body failed - omitting it")
        return {}


def _issue_fields(issue: object) -> dict:
    """The ``fields`` mapping of an issue payload (or {} ). Never raises."""
    try:
        if not isinstance(issue, dict):
            return {}
        fields = issue.get("fields")
        if isinstance(fields, dict):
            return fields
        # Some MCP responses flatten the issue (summary/description at top
        # level). Accept that shape too rather than silently returning nothing.
        if any(k in issue for k in ("summary", "description")):
            return issue
        return {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Tester-facing messages                                                       #
# --------------------------------------------------------------------------- #

_CONNECT_STEPS = (
    "**How to connect Atlassian (one time, ~1 minute):**\n"
    "- **Claude Code** - make sure `.mcp.json` has the `atlassian` entry "
    '(`{"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}`), '
    "then run `/mcp` and authenticate `atlassian` in the browser window it opens.\n"
    "- **Claude Desktop** - claude.ai -> **Settings -> Connectors -> Atlassian -> "
    "Connect**, then reopen the desktop app.\n"
    "- **Cursor** - **Settings -> Features -> MCP -> Add Server**, type `http`, URL "
    "`https://mcp.atlassian.com/v1/mcp/authv2`; Cursor completes OAuth automatically "
    "on the first 401/`WWW-Authenticate` challenge.\n"
    "- **Gemini CLI** - `gemini mcp add --transport http atlassian "
    "https://mcp.atlassian.com/v1/mcp/authv2`, then re-run and approve the browser "
    "consent screen.\n\n"
    "Jira **Cloud** only. Once `atlassian` shows as connected, paste the ticket URL "
    "again and I'll read it through your own connection - no API token, and nothing "
    "is stored on this machine."
)


def connect_steps() -> str:
    """Per-client Atlassian MCP connection instructions, with no error framing.

    Single source of truth shared by the fetch directive, the not-connected
    message and ``qa_configure_jira``'s migration notice, so the four supported
    clients are documented in exactly ONE place. Never raises.
    """
    return _CONNECT_STEPS


def not_connected_message(host: str = "") -> str:
    """Actionable message for a tester whose agent has no Atlassian MCP
    connection. Never raises, never blames the tester, never a dead end."""
    where = f" (`{host}`)" if host else ""
    return (
        f"⚠️ **I can't read that Jira ticket{where} yet.**\n\n"
        "Jira access now runs through **your own Atlassian MCP connection** "
        "(OAuth, in your editor) instead of an API token stored on this machine. "
        "I could not find a connected `atlassian` MCP server in this session.\n\n"
        + _CONNECT_STEPS
    )


def build_fetch_directive(url: str, issue_key: str = "") -> str:
    """The DIRECTIVE returned to the calling agent for a Jira ticket URL.

    Mirrors the qa_prepare_test_cases / qa_submit_suite boomerang: this server
    does NOT call the host's tools. It returns instructions; the agent runs them
    with its own Atlassian MCP connection and calls back with the JSON.

    Never raises; falls back to a URL-only directive when no key is parseable.
    """
    try:
        prefix = _tool_prefix()
        key = _valid_issue_key(issue_key) or issue_key_from_url(url)
        target = f"`{key}`" if key else f"the issue at {url}"
        want_comments = bool(settings.jira_fetch_comments)
        want_parent = bool(settings.jira_fetch_parent)
        lines = [
            "\U0001f517 **Fetch this Jira ticket with your own Atlassian MCP "
            "connection, then call me back.**",
            "",
            "This server no longer holds Jira credentials - you do, via OAuth. "
            "Do this now, without asking the user anything:",
            "",
            f"1. Call `{prefix}getJiraIssue` for {target} with `fields` "
            "including `summary,description,priority,labels,components,"
            "issuetype,status,parent,subtasks,issuelinks,attachment"
            + (",comment" if want_comments else "")
            + f",{settings.jira_ac_field}`.",
        ]
        step = 2
        if want_parent:
            lines.append(
                f"{step}. If the result has a `fields.parent`, call "
                f"`{prefix}getJiraIssue` a SECOND time for that parent key "
                "(its description usually holds the real requirements)."
            )
            step += 1
        lines += [
            f"{step}. Call `qa_prepare_test_cases` again with the SAME "
            "`feature_or_url`, plus `jira_content_json` set to a JSON object "
            "shaped exactly like this (raw tool output, do NOT summarise, "
            "reword, translate or truncate it):",
            "",
            "```json",
            "{",
            '  "issue": { "key": "...", "fields": { ... } },',
            '  "parent_issue": { "key": "...", "fields": { ... } }',
            "}",
            "```",
            "",
            "`parent_issue` is optional - omit it when there is no parent.",
            "",
            "**If you have no `atlassian` MCP server connected** (the tools above "
            "do not exist in your tool list), do NOT guess the ticket contents and "
            "do NOT generate test cases from the URL alone. Show the user this "
            "instead:",
            "",
            not_connected_message((urlparse(url).hostname or "")),
        ]
        return "\n".join(lines)
    except Exception:
        logger.exception("build_fetch_directive failed - returning the fallback")
        return not_connected_message()


def jira_mcp_required_result(url: str) -> dict:
    """The fetch-shaped result for a Jira URL that has not been supplied yet.

    Keeps ``tools/jira_fetcher.fetch_url_content``'s Hard-Rule contract
    (``{"error": ..., "content": None}``) while carrying the machine-readable
    ``needs_jira_mcp`` marker and the tester/agent-facing directive. Never raises.
    """
    key = issue_key_from_url(url)
    return {
        "error": build_fetch_directive(url, key),
        "content": None,
        "needs_jira_mcp": True,
        "issue_key": key,
        "source_url": url,
    }


# --------------------------------------------------------------------------- #
# Normalization of the host-submitted payload                                  #
# --------------------------------------------------------------------------- #


def _load_payload(raw: object) -> tuple[dict, str]:
    """(payload, error) from an untrusted host submission. Never raises.

    Accepts a dict (already parsed by the MCP layer) or a JSON string, tolerating
    a ```json fence. Uses json.loads ONLY - never eval - and refuses anything
    over _MAX_PAYLOAD_BYTES before parsing.
    """
    try:
        if isinstance(raw, dict):
            return raw, ""
        text = str(raw or "").strip()
        if not text:
            return {}, "empty"
        if len(text.encode("utf-8", "ignore")) > _MAX_PAYLOAD_BYTES:
            return {}, (
                "That Jira payload is too large to accept "
                f"(over {_MAX_PAYLOAD_BYTES // 1000} KB). Re-fetch the issue "
                "requesting only the fields listed in the directive."
            )
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}, "not_object"
        return data, ""
    except json.JSONDecodeError:
        return {}, "invalid_json"
    except Exception:
        logger.exception("_load_payload failed")
        return {}, "invalid_json"


def normalize_issue_payload(raw: object, source_url: str = "") -> dict:
    """Turn a host-submitted Atlassian MCP issue payload into the grounded dict
    the REST path used to return.

    Returns EXACTLY the historical key set so every downstream consumer
    (``_ground_and_gate``, ``comment_reconciler``, ``_prepare_generation``,
    ``rtm``) is unchanged: ``title, description, acceptance_criteria, priority,
    labels, components, comments, comments_meta, images, parent, subtasks,
    issuelinks, parent_context, raw_text, content`` (plus ``error``).

    UNTRUSTED input: size-capped, ``json.loads``-only, issue keys regex-gated,
    URLs stripped from BACKGROUND text. Never raises - a malformed payload comes
    back as ``{"error": <actionable text>, "content": None}``.
    """
    try:
        payload, load_error = _load_payload(raw)
        if load_error:
            return {
                "error": (
                    "⚠️ I couldn't read the Jira payload you sent back "
                    f"({load_error}). Re-run the `getJiraIssue` call and pass its "
                    "RAW JSON result as `jira_content_json`, unmodified."
                    if load_error not in ("empty",)
                    else jira_mcp_required_result(source_url)["error"]
                ),
                "content": None,
                "needs_jira_mcp": load_error == "empty",
            }

        issue = (
            payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
        )
        fields = _issue_fields(issue)
        if not fields:
            return {
                "error": (
                    "⚠️ That Jira payload has no `fields` object, so there "
                    "is nothing to generate from. Call `getJiraIssue` again and pass "
                    "its RAW result (the whole object, including `fields`)."
                ),
                "content": None,
                "needs_jira_mcp": False,
            }

        key = _valid_issue_key(issue.get("key")) or issue_key_from_url(source_url)

        # Emptiness is judged on the SOURCE fields, BEFORE any fallback value is
        # applied to `title`. Judging it afterwards (on `title` / `raw_text`)
        # would let a ticket whose summary AND description are both empty look
        # "grounded" purely because the issue KEY was substituted for the title,
        # and a whole suite would then be generated from nothing.
        summary_src = str(fields.get("summary") or "").strip()
        description_src = _as_text(fields.get("description")).strip()
        ac_src = _as_text(fields.get(settings.jira_ac_field)).strip()
        if not (summary_src or description_src or ac_src):
            return {
                "error": (
                    "⚠️ That Jira issue came back with no summary, no "
                    "description and no acceptance criteria, so there is nothing to "
                    "generate from. Check the issue key, or paste the ticket text "
                    "and I'll work from that."
                ),
                "content": None,
                "needs_jira_mcp": False,
            }

        title = summary_src or key or "Jira issue"
        description = description_src

        priority = _extract_priority(fields)
        labels = _extract_names(fields.get("labels"))
        components = _extract_names(fields.get("components"))

        comment_records = _extract_comment_records(fields)
        comments = _comment_lines(comment_records)[-settings.jira_max_comments :][::-1]

        parent: dict | None = None
        subtasks: list[dict] = []
        issuelinks: list[dict] = []
        parent_context = ""
        if settings.jira_fetch_parent:
            parent = _extract_parent_ref(fields)
            subtasks = _extract_subtasks(fields)
            issuelinks = _extract_issuelinks(fields)
            if parent:
                parent = {**parent, **_parent_body(payload.get("parent_issue"))}
            parent_context = _build_parent_context(parent, subtasks, issuelinks)

        meta_lines = []
        if priority:
            meta_lines.append(f"Priority: {priority}")
        if labels:
            meta_lines.append(f"Labels: {', '.join(labels)}")
        if components:
            meta_lines.append(f"Components: {', '.join(components)}")
        meta_block = ("\n".join(meta_lines) + "\n") if meta_lines else ""

        raw_text = f"{title}\n{meta_block}{description}".strip()
        # QA_COMMENT_RECONCILE_ENABLED ON: the raw thread is SUPPRESSED here
        # deliberately - tools/comment_reconciler turns it into a fenced,
        # deterministically-resolved, URL-stripped AMENDMENTS block that becomes
        # the ONLY comment-derived input the generator sees. Unchanged from the
        # REST implementation.
        if comments and not settings.qa_comment_reconcile_enabled:
            raw_text += "\n\n## Comments\n" + "\n".join(f"- {c}" for c in comments)

        acceptance_criteria = ac_src or _extract_ac_from_description(description)

        attachments = _extract_image_attachments(fields)
        result = {
            "title": title,
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "priority": priority,
            "labels": labels,
            "components": components,
            "comments": comments,
            "comments_meta": comment_records,
            # The Atlassian MCP server returns attachment METADATA, not bytes,
            # and this module makes no outbound HTTP requests - so ticket
            # screenshots cannot ride along any more. Report it rather than
            # silently pretending the ticket had none.
            "images": [],
            "image_attachments": attachments,
            "images_unavailable": bool(attachments),
            "parent": parent,
            "subtasks": subtasks,
            "issuelinks": issuelinks,
            "parent_context": parent_context,
            "raw_text": raw_text,
            "content": raw_text,
            "issue_key": key,
            "source": "atlassian_mcp",
            "error": None,
        }
        return result
    except Exception:
        logger.exception("normalize_issue_payload failed")
        return {
            "error": (
                "⚠️ I couldn't process that Jira payload. Re-run the "
                "`getJiraIssue` call and pass its RAW JSON result unmodified, or "
                "paste the ticket text and I'll generate test cases from it."
            ),
            "content": None,
            "needs_jira_mcp": False,
        }


# --------------------------------------------------------------------------- #
# Access probe (network-free replacement for the REST /myself pre-flight)      #
# --------------------------------------------------------------------------- #


async def verify_jira_access(
    *,
    base_url: str = "",
    email: str = "",
    api_token: str = "",
) -> dict:
    """Network-free stand-in for the removed REST credential probe.

    The server no longer holds Jira credentials, so there is nothing here to
    verify: whether Jira is reachable is a property of the CALLING AGENT's
    Atlassian MCP connection, which a stdio subprocess cannot see. Returning
    ``ok=True`` lets the pre-flight fall through to the fetch, where
    :func:`build_fetch_directive` / :func:`not_connected_message` provide the
    real, actionable guidance in one place.

    The signature is preserved so existing call sites keep working. Arguments
    are accepted and ignored. Never raises, makes no HTTP request.
    """
    return {"ok": True, "error": "", "account": "", "mode": "mcp"}
