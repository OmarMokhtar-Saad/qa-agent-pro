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
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import llm
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


# A use-case table row: "| **Basic Flow** | user clicks cancel ... |". The label
# is bounded and the body is whatever remains up to the trailing pipe.
_UC_ROW_RE = re.compile(r"^\|\s*\**\s*([A-Za-z][A-Za-z /_-]{2,40}?)\s*\**\s*\|(.*)$")

# Only rows that carry a REQUIREMENT. Description/Actor/Pre-condition are context,
# not testable criteria, and including them produced restatements of the feature
# rather than things a case can verify.
_UC_AC_LABELS = frozenset(
    {
        "basic flow",
        "alternative flow",
        "alternate flow",
        "exception flow",
        "business rules",
        "business rule",
        "post-condition",
        "postcondition",
        "post condition",
        "acceptance criteria",
    }
)

# Confluence/Jira rich-text leaves these inline nodes in the markdown export.
_UC_CUSTOM_TAG_RE = re.compile(r"<custom\b[^>]*>.*?</custom>|<custom\b[^>]*/?>", re.S)
_UC_MAX_CRITERIA = 12
_UC_MAX_CRITERION_CHARS = 600


def _extract_ac_from_uc_table(description: str) -> str:
    """Pull acceptance criteria out of a USE-CASE TABLE description.

    2026-08-03. ``_extract_ac_from_description`` only understands an "Acceptance
    Criteria" heading. A whole ticket family writes the requirements as a markdown
    UC table and has no such heading at all:

        | **UC**              | **Cancel order**                          |
        | **Basic Flow**      | User clicks cancel ... System displays ... |
        | **Alternative Flow**| In Step 2, if the user clicks Keep ...     |
        | **Business Rules**  | **BR02**: Not all products have ...       |

    That returned "" on the observed run, so the suite finalized with NO acceptance
    criteria and 61 of 98 cases had no ``requirement_id``.

    Emits ONE criterion per requirement-bearing row rather than trying to split a
    row into steps: the real rows are run-on prose ("User clicks on cancel order
    option System displays cancelation reason **DF01** User select a reason"), so
    sentence-splitting invents boundaries that are not in the ticket. One row per
    criterion is coarser but is actually what the ticket asserts.

    Context-only rows (Description / Actor / Pre-condition) are deliberately
    skipped -- they restate the feature instead of stating something checkable.

    Bounded and never raises: at most ``_UC_MAX_CRITERIA`` rows, each truncated to
    ``_UC_MAX_CRITERION_CHARS``. The caller still routes the result through the
    normal untrusted-text path.
    """
    try:
        if not description:
            return ""
        out: list[str] = []
        for line in description.splitlines():
            m = _UC_ROW_RE.match(line.strip())
            if not m:
                continue
            label = " ".join(m.group(1).split()).strip("*: ").casefold()
            if label not in _UC_AC_LABELS:
                continue
            body = m.group(2)
            body = _UC_CUSTOM_TAG_RE.sub(" ", body)
            body = body.replace("|", " ").replace("*", "").replace("\\", "")
            body = " ".join(body.split())
            if not body:
                continue
            out.append(
                f"{m.group(1).strip('*: ').strip()}: {body[:_UC_MAX_CRITERION_CHARS]}"
            )
            if len(out) >= _UC_MAX_CRITERIA:
                break
        return "\n".join(out)
    except Exception:
        logger.exception("_extract_ac_from_uc_table failed - returning empty")
        return ""


def _usable_ac_text(raw: object) -> bool:
    """Whether the configured AC custom field actually holds acceptance criteria.

    2026-08-03. ``settings.jira_ac_field`` is a per-instance GUESS (default
    ``customfield_10016``). On a real workspace that id is a DATE field, so the AC
    block arrived as ``2025-09-11T09:07:21.362+0300``. Because the fallback was
    written as ``ac_src or _extract_ac_from_description(description)``, a non-empty
    timestamp WON the ``or`` and the ticket's real criteria -- which lived in the
    description -- were never parsed. Downstream, ``tools/rtm`` turned that
    timestamp into ``AC-001`` and 37 of 98 cases "traced" to it.

    ``rtm.looks_like_requirement_text`` now rejects that value, which stops the fake
    criterion, but on its own it only converts a WRONG AC into NO AC: the ``or``
    still suppresses the description. Truthiness is the wrong test, so ask the same
    question rtm asks downstream -- does any line look like a requirement? -- so the
    two cannot disagree about what counts as usable.

    Multi-line AC fields are common, and one junk line must not veto a real block,
    so ANY plausible line makes the field usable. On an unexpected error this
    returns today's truthiness answer: preferring a possibly-odd AC field is
    recoverable, silently discarding a real one is not.
    """
    text = "" if raw is None else str(raw)
    try:
        from tools.rtm import looks_like_requirement_text

        if not text.strip():
            return False
        return any(looks_like_requirement_text(ln) for ln in text.splitlines())
    except Exception:
        logger.exception("_usable_ac_text failed - treating the field as usable")
        return bool(text.strip())


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
            # No "Acceptance Criteria" heading. Before giving up, try the use-case
            # table shape (opt-in) -- a whole ticket family has its requirements
            # only there, and returning "" left those suites with no traceability.
            if bool(getattr(settings, "qa_jira_uc_table_ac_enabled", False)):
                return _extract_ac_from_uc_table(description)
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


def _extract_sibling_bodies(payload: dict, target_key: str = "") -> list[dict]:
    """[{key, issuetype, summary, description, acceptance_criteria}] for the user
    stories under the same parent.

    Read from an OPTIONAL third host call (``searchJiraIssuesUsingJql``) handed
    back as ``sibling_issues``: those child issues' BODIES carry the
    requirements a sub-task inherits, while ``_extract_subtasks`` only ever sees
    key/summary/status. Gated by ``settings.jira_fetch_sibling_stories``,
    count-capped by ``_MAX_RELATED_ISSUES`` and char-capped PER ISSUE so one
    huge story cannot crowd out the rest.

    UNTRUSTED third-party text -- strictly higher injection risk than the target
    ticket, because it is authored by other people for another purpose. Keys are
    regex-gated here, every URL is stripped by :func:`_build_parent_context`, and
    the result stays in ``parent_context`` (BACKGROUND) instead of ``raw_text``.
    Never raises.
    """
    try:
        if not settings.jira_fetch_sibling_stories:
            return []
        items = payload.get("sibling_issues") if isinstance(payload, dict) else None
        if isinstance(items, dict):
            # Accept the RAW JQL response shape ({"issues": [...]}) as well as a
            # bare list, so a host that pastes the tool result unmodified works.
            items = items.get("issues")
        if not isinstance(items, list):
            return []
        target = _valid_issue_key(target_key)
        per_issue = max(1, settings.jira_max_sibling_chars // _MAX_RELATED_ISSUES)
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = _valid_issue_key(item.get("key"))
            if not key or key == target:
                continue
            sfields = _issue_fields(item)
            desc = _as_text(sfields.get("description")).strip()[:per_issue]
            sac = _as_text(sfields.get(settings.jira_ac_field)).strip()[:per_issue]
            if not desc and not sac:
                # Title-only siblings add nothing _extract_subtasks does not
                # already list, and each one costs budget.
                continue
            itype = sfields.get("issuetype")
            out.append(
                {
                    "key": key,
                    "issuetype": (
                        str(itype.get("name") or "").strip()
                        if isinstance(itype, dict)
                        else ""
                    ),
                    "summary": str(sfields.get("summary") or "").strip(),
                    "description": desc,
                    "acceptance_criteria": sac,
                }
            )
            if len(out) >= _MAX_RELATED_ISSUES:
                break
        return out
    except Exception:
        logger.exception("Extracting sibling user stories failed")
        return []


def _build_parent_context(
    parent: dict | None,
    subtasks: list[dict],
    issuelinks: list[dict],
    siblings: list[dict] | None = None,
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
        if siblings:
            lines += [
                "",
                "Sibling user stories under the same parent (context only -- "
                "requirements they state can apply to this ticket, but they are "
                "NOT the thing under test):",
            ]
            budget = max(0, settings.jira_max_sibling_chars)
            for sib in siblings:
                if budget <= 0:
                    break
                head = f"- {sib['key']}"
                itype = str(sib.get("issuetype") or "").strip()
                if itype:
                    head += f" ({itype})"
                sib_summary = str(sib.get("summary") or "").strip()
                if sib_summary:
                    head += f": {sib_summary}"
                block = [head.rstrip()]
                sib_desc = str(sib.get("description") or "").strip()
                if sib_desc:
                    block.append(f"    {sib_desc}")
                sib_ac = str(sib.get("acceptance_criteria") or "").strip()
                if sib_ac:
                    block += ["    Acceptance criteria:", f"    {sib_ac}"]
                chunk = "\n".join(block)
                if len(chunk) > budget:
                    chunk = chunk[:budget]
                budget -= len(chunk)
                lines.append(chunk)
        text = "\n".join(lines).strip()
        if not text:
            return ""
        cap = settings.jira_max_parent_chars
        if cap <= 0:
            return ""
        # The sibling budget is ADDITIVE. Capping the composed block at
        # jira_max_parent_chars alone would let sibling prose truncate away the
        # parent's own description -- the very thing this block exists to carry.
        if siblings:
            cap += max(0, settings.jira_max_sibling_chars)
        return _strip_urls(text)[:cap]
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
                ac_raw
                if _usable_ac_text(ac_raw)
                else _extract_ac_from_description(description)
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

_CONNECT_PREAMBLE = "**How to connect Atlassian (one time, ~1 minute):**\n"

_CONNECT_STEP_CLAUDE_CODE = (
    "- **Claude Code** - make sure `.mcp.json` has the `atlassian` entry "
    '(`{"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}`), '
    "then run `/mcp` and authenticate `atlassian` in the browser window it opens.\n"
)
_CONNECT_STEP_CLAUDE_DESKTOP = (
    "- **Claude Desktop** - claude.ai -> **Settings -> Connectors -> Atlassian -> "
    "Connect**, then reopen the desktop app.\n"
)
_CONNECT_STEP_CURSOR = (
    "- **Cursor** - Cursor's settings menu path for this moves between versions, so "
    "edit the config file directly: add to `.cursor/mcp.json` (project) or "
    "`~/.cursor/mcp.json` (global) under `mcpServers`: "
    '`"atlassian": {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}`, '
    "then restart Cursor - it completes OAuth automatically on the first "
    "401/`WWW-Authenticate` challenge. (Newer versions also expose this under "
    "**Settings -> Tools & MCP** or the **Customize** page.)\n"
)
_CONNECT_STEP_GEMINI_CLI = (
    "- **Gemini CLI** - `gemini mcp add --transport http atlassian "
    "https://mcp.atlassian.com/v1/mcp/authv2`, then re-run and approve the browser "
    "consent screen.\n"
)
_CONNECT_CLOSING = (
    "\nJira **Cloud** only. Once `atlassian` shows as connected, paste the ticket URL "
    "again and I'll read it through your own connection - no API token, and nothing "
    "is stored on this machine."
)

_CONNECT_STEPS_BY_CLIENT = {
    "claude-code": _CONNECT_STEP_CLAUDE_CODE,
    "claude-desktop": _CONNECT_STEP_CLAUDE_DESKTOP,
    "cursor": _CONNECT_STEP_CURSOR,
    "gemini-cli": _CONNECT_STEP_GEMINI_CLI,
}

_CONNECT_STEPS = (
    _CONNECT_PREAMBLE
    + _CONNECT_STEP_CLAUDE_CODE
    + _CONNECT_STEP_CLAUDE_DESKTOP
    + _CONNECT_STEP_CURSOR
    + _CONNECT_STEP_GEMINI_CLI
    + _CONNECT_CLOSING
)


def _detect_client_key(host: str) -> str:
    """Best-effort match of an MCP clientInfo.name to one of the 4 documented
    clients. Returns '' when the host is empty/unrecognized (caller should
    fall back to showing all four)."""
    h = (host or "").strip().lower()
    if "cursor" in h:
        return "cursor"
    if "gemini" in h:
        return "gemini-cli"
    if "desktop" in h:
        return "claude-desktop"
    if "claude" in h:
        return "claude-code"
    return ""


def connect_steps() -> str:
    """Per-client Atlassian MCP connection instructions, with no error framing.

    Detects the connected MCP client (forwarded from the initialize handshake
    via llm.set_host_client) and returns ONLY that client's steps when
    recognized, so testers aren't shown instructions for editors they don't
    use. Falls back to all four clients when the host is empty/unrecognized.
    Never raises.
    """
    try:
        key = _detect_client_key(llm.get_host_client())
    except Exception:
        key = ""
    if key:
        return _CONNECT_PREAMBLE + _CONNECT_STEPS_BY_CLIENT[key] + _CONNECT_CLOSING
    return _CONNECT_STEPS


# Where THIS server's own files live. A module constant rather than an inline
# expression so tests can point it somewhere harmless -- the same seam
# tools/updater._INSTALL_DIR already provides.
_INSTALL_ROOT = Path(__file__).resolve().parent.parent

# A local mcp.json is a hand-written config file; anything larger than this is
# not one, and must not be read into memory just to be rejected.
_MAX_LOCAL_CONFIG_BYTES = 1_000_000


def _home_dir() -> Path | None:
    """The user's home directory, or None when it cannot be determined.
    Never raises (Path.home() can raise on an environment with no HOME)."""
    try:
        return Path.home()
    except Exception:
        return None


def _local_config_paths(
    client_key: str, workspace_roots: list[Path] | None = None
) -> list[Path]:
    """Known on-disk MCP config locations for a client, most authoritative first.

    Project-scoped config lives in the tester's OPEN EDITOR WORKSPACE, which a
    stdio subprocess cannot infer on its own. Two guesses shipped on that
    reasoning and BOTH were confirmed wrong against live qa-agent-pro sessions:

    * this server's own install directory (v1.31.0) -- a dist install lives in a
      fixed place (e.g. ~/.qa-agents/) while the tester can have ANY project
      open in their editor;
    * ``Path.cwd()`` (v1.32.0) -- an editor does NOT reliably spawn the MCP
      subprocess with the workspace as its working directory, and inside THIS
      server it is provably useless: mcp_server chdir()s to its own install root
      at import time, so cwd collapses onto the candidate above.

    The MCP protocol carries the real signal: the client's ``roots`` capability
    reports the open workspace folder(s). ``mcp_server.qa_setup_check`` resolves
    it (``ctx.list_roots()``) and threads the result down as ``workspace_roots``,
    which is checked FIRST. The two guesses stay on as low-priority candidates --
    a dev checkout runs the server FROM the workspace, so they are right there --
    and a client with no ``roots`` support passes ``None``, degrading to exactly
    the previously shipped chain. Never raises: an unreadable cwd, an
    unknowable home, or a garbage roots entry just drops that candidate.

    Only clients with a well-defined local JSON config are covered here --
    Claude Desktop's Atlassian connection is a hosted claude.ai Connector
    with no local file, so it is intentionally absent."""
    if client_key not in ("cursor", "claude-code"):
        return []

    parts = (".cursor", "mcp.json") if client_key == "cursor" else (".mcp.json",)
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path)
        if key not in seen:
            seen.add(key)
            candidates.append(path)

    for root in workspace_roots or []:
        try:
            _add(Path(root).joinpath(*parts))
        except Exception:
            logger.debug("ignoring unusable workspace root %r", root, exc_info=True)
            continue

    _add(_INSTALL_ROOT.joinpath(*parts))
    try:
        _add(Path.cwd().joinpath(*parts))
    except OSError:
        pass

    if client_key == "cursor":
        home = _home_dir()
        if home is not None:
            _add(home / ".cursor" / "mcp.json")
    return candidates


def _local_atlassian_entry_exists(
    client_key: str, workspace_roots: list[Path] | None = None
) -> bool:
    """Best-effort, read-only check for an 'atlassian' MCP server entry
    already present in a known on-disk config file for this client.

    ``workspace_roots`` (the tester's OPEN workspace, from the MCP ``roots``
    capability) is searched before the local fallbacks -- see
    ``_local_config_paths`` for why nothing else on disk can be trusted to be
    the tester's project.

    This can only prove the entry is CONFIGURED, never that it is actually
    CONNECTED/AUTHORIZED -- that live OAuth state lives inside the editor's
    own MCP client runtime, which a sibling stdio process cannot observe.
    Callers must phrase around that gap. Never raises: an oversized, absent,
    unreadable or non-object config is skipped, not fatal."""
    for path in _local_config_paths(client_key, workspace_roots):
        try:
            if path.stat().st_size > _MAX_LOCAL_CONFIG_BYTES:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if "atlassian" in (data.get("mcpServers") or {}):
                return True
        except Exception:
            continue
    return False


def connect_hint_line(workspace_roots: list[Path] | None = None) -> str:
    """One-line, client-aware Jira-connect hint for compact reports (e.g.
    qa_setup_check's optional-items list).

    ``workspace_roots`` are the tester's OPEN workspace folder(s), resolved from
    the MCP ``roots`` capability by ``mcp_server.qa_setup_check`` and passed
    straight through to the on-disk lookup -- the only authoritative way to find
    the project-scoped config a tester would actually edit. ``None`` (a client
    without ``roots`` support, or a failed lookup) degrades to the
    install-dir / cwd / global candidate chain exactly as before.

    Detects the connected MCP client
    the same way connect_steps() does and reuses the SAME per-client detail
    text (exact JSON/URL/restart step) as one inline sentence instead of a
    bulleted block, so a tester gets full actionable steps without first
    having to paste a Jira URL to trigger connect_steps(). When an
    'atlassian' entry is already found on disk, says so instead of telling
    the tester to add something that is already there -- but never claims
    it is actually connected, since that cannot be observed from here.
    Falls back to a short all-clients summary when the host is
    empty/unrecognized. Never raises.
    """
    try:
        key = _detect_client_key(llm.get_host_client())
    except Exception:
        key = ""
    base = "To paste Jira ticket URLs, connect the Atlassian MCP server"
    if key:
        try:
            already_configured = _local_atlassian_entry_exists(
                key, workspace_roots=workspace_roots
            )
        except Exception:
            already_configured = False
        if already_configured:
            return (
                "An `atlassian` MCP entry is already configured on disk for "
                f"{key.replace('-', ' ').title()}. I can't tell from here whether "
                "it's actually authorized - if pasting a Jira ticket URL doesn't "
                "work, restart your editor (OAuth sessions can also expire and "
                "need re-authenticating)."
            )
        detail = _CONNECT_STEPS_BY_CLIENT[key].strip()
        detail = detail.split(" - ", 1)[1] if " - " in detail else detail
        return f"{base} - {detail}"
    return (
        f"{base} in your editor (Claude Code: `/mcp`; Claude Desktop: Settings > "
        "Connectors; Cursor: edit `.cursor/mcp.json`; Gemini CLI: `gemini mcp add`). "
        "No API token and no .env entry are needed."
    )


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


# --------------------------------------------------------------------------- #
# Live connection verification (the AGENT calls; this server only parses)      #
# --------------------------------------------------------------------------- #

# 2026-08-03. _local_atlassian_entry_exists() can prove at most that an
# `atlassian` entry is CONFIGURED in a local mcp.json -- never that it is
# authorized -- and Claude Desktop's hosted Connector has no local file at all.
# So "will Jira actually work?" stayed unanswerable from inside this stdio
# subprocess. The answer is the same boomerang the rest of the codebase uses:
# hand the CALLING AGENT a directive to make ONE read-only call with its own
# Atlassian MCP connection, then parse the raw result it hands back. No LLM
# call, no outbound HTTP from this module, and nothing is persisted.

# The verification blob is UNTRUSTED, but tiny by nature (an identity object or
# a one-line error marker), so it is capped far below _MAX_PAYLOAD_BYTES before
# json.loads ever sees it.
_MAX_VERIFY_CHARS = 4000

# Only short, sanitized FRAGMENTS of that blob are ever echoed to a tester.
_MAX_ECHO_CHARS = 200
_MAX_ERROR_ECHO_CHARS = 300

# Read-only, takes no parameters, and cannot fail for "wrong project" reasons --
# so a failure here really does mean "not connected".
_VERIFY_TOOL = "atlassianUserInfo"

# The real response shape is not confirmed in this repo and varies by client and
# by Atlassian MCP version, so every lookup below is best-effort and
# STRING-only: an unfamiliar payload must yield "" (and the honest "identity
# payload received") rather than a fabricated name.
_IDENTITY_NAME_KEYS = ("name", "displayName", "display_name", "nickname")
_IDENTITY_EMAIL_KEYS = ("email", "emailAddress", "email_address")
_ERROR_KEYS = ("error", "errors", "errorMessages", "errorMessage", "isError")


def verify_tool_name() -> str:
    """Fully-qualified name of the read-only probe tool the AGENT must call.

    Uses the same client-configurable prefix as every other directive, so the
    default is exactly ``mcp__atlassian__atlassianUserInfo``.
    """
    return f"{_tool_prefix()}{_VERIFY_TOOL}"


def verify_directive() -> str:
    """Directive asking the calling agent to PROVE the Atlassian connection.

    Appended to EVERY qa_setup_check report and to qa_configure_jira's
    no-argument reply, so the flow is reachable from either entry point.
    Additive by design: the on-disk best guess (connect_hint_line) still stands
    beside it, because this only becomes a real status once the agent calls
    back. Never raises.
    """
    try:
        tool = verify_tool_name()
        return "\n".join(
            [
                "\U0001f9ea **Want a verified answer instead of a guess?** "
                "Agent: do this now, yourself, without asking the user anything.",
                "",
                f"1. Call `{tool}` (no parameters). It is read-only and only "
                "reports who your Atlassian connection is authenticated as.",
                "2. Call `qa_configure_jira` again with `atlassian_verify_json` "
                "set to that call's RAW JSON result (do not summarise, reword "
                "or truncate it).",
                f"3. If `{tool}` is not in your tool list, or the call fails, "
                "call `qa_configure_jira` with "
                '`atlassian_verify_json={"error": "<what happened>"}` instead '
                "-- I'll turn that into the exact connection steps for this "
                "editor.",
                "",
                "Nothing from that result is stored: it is read once, reported "
                "back in the same turn, and discarded.",
            ]
        )
    except Exception:
        logger.exception("verify_directive failed - falling back to connect steps")
        return connect_steps()


def _sanitize_echo(value: object, limit: int) -> str:
    """One-line, URL-free, backtick-free, length-capped rendering of an
    untrusted fragment before it is shown to a tester. Never raises."""
    try:
        text = "".join(
            ch
            for ch in str(value or "")
            if ch == " " or (ch.isprintable() and ch != "`")
        )
        return _strip_urls(text).strip()[:limit]
    except Exception:
        logger.exception("_sanitize_echo failed - dropping the value")
        return ""


def _unwrap_mcp_content(payload: dict) -> dict:
    """Unwrap ONE level of the MCP tool envelope
    (``{"content": [{"type": "text", "text": "{...}"}]}``) when that is what
    the agent handed back instead of the tool's own JSON. Returns the payload
    unchanged when it is not that shape. Never raises."""
    try:
        parts = payload.get("content")
        if not isinstance(parts, list):
            return payload
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            inner = json.loads(text)
            if isinstance(inner, dict):
                return inner
        return payload
    except Exception:
        logger.debug("_unwrap_mcp_content: not an MCP envelope", exc_info=True)
        return payload


def _identity_label(payload: dict) -> str:
    """Best-effort ``Name (email)`` for an identity payload, or "".

    STRING fields only, each sanitized, so an unfamiliar shape produces "" and
    the caller reports "identity payload received" instead of inventing a name.
    Never raises."""
    try:

        def _first(keys: tuple[str, ...]) -> str:
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = _sanitize_echo(value, _MAX_ECHO_CHARS)
                    if cleaned:
                        return cleaned
            return ""

        name = _first(_IDENTITY_NAME_KEYS)
        email = _first(_IDENTITY_EMAIL_KEYS)
        if name and email:
            return f"{name} ({email})"
        return name or email
    except Exception:
        logger.exception("_identity_label failed - omitting the identity")
        return ""


def _error_text(payload: dict) -> str:
    """Sanitized failure reason when the payload looks like a failure, else "".

    Recognizes the marker this server ASKS the agent for
    (``{"error": "..."}``) plus the shapes Jira / MCP themselves use
    (``errorMessages``, ``isError``). Never raises."""
    try:
        for key in _ERROR_KEYS:
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None or value is False:
                continue
            if isinstance(value, (str, list, dict)) and not value:
                continue
            if isinstance(value, bool):
                text = ""
            elif isinstance(value, list):
                text = "; ".join(
                    part
                    for part in (
                        _sanitize_echo(item, _MAX_ECHO_CHARS)
                        for item in value
                        if isinstance(item, str)
                    )
                    if part
                )
            elif isinstance(value, dict):
                text = _sanitize_echo(value.get("message"), _MAX_ERROR_ECHO_CHARS)
            else:
                text = _sanitize_echo(value, _MAX_ERROR_ECHO_CHARS)
            return text or "the call did not come back with an identity"
        return ""
    except Exception:
        logger.exception("_error_text failed - reporting a generic failure")
        return "the call did not come back with an identity"


def verify_result_message(raw: object) -> str:
    """Turn the agent's raw ``atlassianUserInfo`` result into a verdict.

    Five outcomes, none of which raises and none of which is a dead end:

    * nothing supplied -> the directive again (nothing was actually checked);
    * an identity payload -> VERIFIED, with a best-effort identity;
    * an error marker -> NOT CONNECTED, plus this client's connect steps;
    * an empty object -> an honest "couldn't confirm", plus the hint line;
    * anything unreadable -> an honest "couldn't read that", plus the hint line.

    The payload is UNTRUSTED: size-capped BEFORE parsing, ``json.loads`` only
    (never eval), no assumed schema, and every echoed fragment sanitized.
    Nothing from it is stored or logged. Never raises.
    """
    try:
        text = str(raw or "").strip()
        if not text:
            return verify_directive()
        if len(text) > _MAX_VERIFY_CHARS:
            return (
                "\u26a0\ufe0f **That verification result was too long to "
                f"read** (over {_MAX_VERIFY_CHARS} characters). "
                f"`{verify_tool_name()}` returns a small identity object -- "
                "re-run it and pass its raw result, or pass "
                '`atlassian_verify_json={"error": "<what happened>"}` if the '
                "call failed.\n\n" + connect_hint_line()
            )
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return (
                "\u26a0\ufe0f **I couldn't read that verification result**, so "
                "I still can't confirm the Atlassian connection either way. "
                f"Re-run `{verify_tool_name()}` and pass its RAW JSON result "
                "as `atlassian_verify_json`, or pass "
                '`{"error": "<what happened>"}` if the call failed.\n\n'
                + connect_hint_line()
            )
        payload = _unwrap_mcp_content(payload)
        failure = _error_text(payload)
        if failure:
            return (
                "\u274c **Not connected** -- that Atlassian call did not "
                f"return an identity: {failure}\n\n" + connect_steps()
            )
        if not payload:
            return (
                "\u26a0\ufe0f **That verification result was empty**, so I "
                "still can't confirm the Atlassian connection. Re-run "
                f"`{verify_tool_name()}` and pass its RAW JSON result as "
                "`atlassian_verify_json`.\n\n" + connect_hint_line()
            )
        who = _identity_label(payload) or "connected (identity payload received)"
        return (
            f"\u2705 **Atlassian verified** -- connected as {who}.\n\n"
            "Jira ticket URLs will be read through this connection, with your "
            "own Jira permissions. Nothing was stored: this server holds no "
            "Jira credential, and it did not keep that identity either."
        )
    except Exception:
        logger.exception("verify_result_message failed - falling back")
        return (
            "\u26a0\ufe0f I couldn't read that verification result.\n\n"
            + connect_steps()
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
        if want_parent and settings.jira_fetch_sibling_stories:
            lines.append(
                f"{step}. If there WAS a parent, also call "
                f"`{prefix}searchJiraIssuesUsingJql` with `jql` = "
                '"parent = <THAT PARENT KEY> ORDER BY key", `fields` = '
                "[`summary`, `description`, `issuetype`, `status`, "
                f"`{settings.jira_ac_field}`], `responseContentFormat` = "
                f'"markdown" and `maxResults` = {_MAX_RELATED_ISSUES}. The '
                "sibling user stories under that parent carry requirements this "
                "ticket inherits, and their one-line titles alone are not "
                "enough. Skip this step when there is no parent."
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
            '  "parent_issue": { "key": "...", "fields": { ... } },',
            '  "sibling_issues": [ { "key": "...", "fields": { ... } } ]',
            "}",
            "```",
            "",
            "`parent_issue` is optional - omit it when there is no parent. "
            "`sibling_issues` is optional too: pass the `issues` array from the "
            "JQL result (or the whole result object) when you ran that step.",
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
        siblings: list[dict] = []
        parent_context = ""
        if settings.jira_fetch_parent:
            parent = _extract_parent_ref(fields)
            subtasks = _extract_subtasks(fields)
            issuelinks = _extract_issuelinks(fields)
            if parent:
                parent = {**parent, **_parent_body(payload.get("parent_issue"))}
            siblings = _extract_sibling_bodies(payload, key)
            parent_context = _build_parent_context(
                parent, subtasks, issuelinks, siblings
            )

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

        acceptance_criteria = (
            ac_src
            if _usable_ac_text(ac_src)
            else _extract_ac_from_description(description)
        )

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
            # Additive key (2026-08-03). Downstream consumers read the historical
            # set and ignore this; it exists so a caller can see WHICH sibling
            # stories were folded into parent_context.
            "sibling_stories": siblings,
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
