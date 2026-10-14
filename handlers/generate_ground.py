"""Host-mode prepare, front half: URL/Jira fetch, Swagger ingest and UI extraction."""

from __future__ import annotations

import logging
import sys

from agents.host_gen_types import _Grounding
from config.settings_guard import never_raise
from handlers import _common, jira
from handlers.generate_args import GroundArgs
from handlers.generate_host import host
from handlers.seam_alias import alias
from tools.jira_mcp import looks_like_jira_url, not_connected_message
from tools.swagger_fetcher import looks_like_openapi_url

logger = logging.getLogger("tools.mcp_handlers")


# needs_jira_mcp: the agent has not fetched the ticket yet. The
# error IS the directive -- relay it verbatim so the agent acts
# on it, instead of proceeding with no ticket content (which is
# what fabricated suites from an empty Jira SPA shell).
#
# 2026-08-31. Every OTHER error used to fall through to
# generation. That silently swallowed the two anti-fabrication
# refusals normalize_issue_payload raises with
# needs_jira_mcp=False -- "that payload has no `fields` object"
# and "no summary, no description and no acceptance criteria" --
# and a full suite was written from nothing. When the SOURCE is
# Jira, an unreadable source is a refusal, not a warning. A
# generic page that failed to fetch still falls through: the
# tester's own words may carry the feature.
def _page_fetch_refusal(text: str, url_content: dict) -> str | None:
    """The markdown to relay when the ticket / page fetch failed in a way that
    must stop generation, else None (a generic page may still fall through)."""
    if url_content.get("needs_jira_mcp"):
        return str(url_content.get("error") or not_connected_message())
    hint = jira.jira_config_hint(text)
    if hint:
        return hint
    if looks_like_jira_url(text) or url_content.get("jira_page_without_issue"):
        return str(url_content.get("error"))
    return None


async def _extract_page_ui(
    text: str, attached_images: list | None, url_content: dict
) -> dict | None:
    """UI extraction for a fetched page; None when skipped or when it fails."""
    if jira._skip_ui_extraction(text, attached_images):
        logger.info(
            "ui_extractor: skipped -- %s carries no app UI to extract",
            "the Jira ticket page",
        )
        return None
    with never_raise(logger, "mcp: UI extraction failed -- continuing", exc_info=True):
        return await host().extract_ui_elements(text, prefetched=url_content)
    return None


async def _openapi_text(text: str, progress) -> str | None:
    """The OpenAPI spec summary for a spec URL, else None."""
    # QA_SWAGGER_ENABLED was DELETED on 2026-08-13 (flag-surface
    # reduction, batch 6) and hardcoded ON -- the value the distribution
    # shipped and the README advertises. looks_like_openapi_url is the
    # only gate left.
    if not looks_like_openapi_url(text):
        return None
    await _common.emit(progress, "\U0001f517 Fetching the OpenAPI spec…")
    spec_result = await host().fetch_openapi_spec(text)
    if spec_result.get("error"):
        return None
    return spec_result.get("summary") or None


async def _jira_gate(args: GroundArgs) -> str | None:
    """Setup hint or jira_preflight short-circuit text, else None."""
    hint = jira.jira_config_hint(args.text)
    if hint:
        return hint
    return await jira.jira_preflight(args.text)


async def _grounding_fetch(args: GroundArgs) -> "str | _Grounding":
    """URL/Jira fetch: setup hint, jira_preflight, Swagger ingest and UI
    extraction. Returns a markdown STRING to short-circuit or a _Grounding."""
    if not _common._is_url(args.text):
        return _Grounding(url_content=None, ui_content=None, openapi_text=None)
    early = await _jira_gate(args)
    if early is not None:
        return early
    openapi_text = await _openapi_text(args.text, args.progress)
    if openapi_text is not None:
        return _Grounding(url_content=None, ui_content=None, openapi_text=openapi_text)
    page = await _fetch_page_content(args)
    if isinstance(page, str):
        return page
    return _Grounding(url_content=page[0], ui_content=page[1], openapi_text=None)


async def _fetch_step(args: GroundArgs) -> dict:
    """Announce and run the ticket / page fetch."""
    await _common.emit(args.progress, "\U0001f517 Fetching the ticket / page…")
    # jira_content_json is the BACK half of the Jira boomerang: when the
    # calling agent has already fetched the issue with its own
    # mcp__atlassian__getJiraIssue, it hands the raw JSON back here and
    # fetch_url_content normalizes it instead of asking again. Empty on
    # the first call, and ignored entirely for a non-Jira URL.
    # Called with ONE argument when there is nothing to hand back, so
    # every existing single-parameter test double for fetch_url_content
    # keeps working unchanged.
    if args.jira_content_json:
        return await host().fetch_url_content(
            args.text, jira_content=args.jira_content_json
        )
    return await host().fetch_url_content(args.text)


async def _fetch_page_content(args: GroundArgs) -> "str | tuple":
    """Fetch the ticket / page and extract its UI. Returns a refusal STRING or
    the (url_content, ui_content) pair."""
    url_content = await _fetch_step(args)
    if url_content.get("error"):
        refusal = _page_fetch_refusal(args.text, url_content)
        if refusal is not None:
            return refusal
    ui_content = await _extract_page_ui(args.text, args.attached_images, url_content)
    return url_content, ui_content


async def _grounding_gates(
    *, proceed_anyway: bool, attached_images: list | None, audit_source: str
) -> None:
    """The (fail-safe) ambiguity gate: audit-only, the review runs on the host."""
    if proceed_anyway or attached_images:
        return
    # The server-side ambiguity pre-pass is GONE (P2-J, 2026-08-16), so
    # there is no branch here any more and no progress line announcing a
    # check this process does not perform. The audit event is KEPT: an
    # operator reading the trail must still see that the gate was
    # DELEGATED rather than silently absent.
    logger.info(
        "prepare: ambiguity gate runs on the host (no server-side classifier call)"
    )
    await _common.audit(
        "mcp_ambiguity_gate",
        detail={"source": audit_source, "skipped": "host_ambiguity_review"},
    )


# Comment reconciliation left that list on 2026-08-15: dead-code deletion
# batch D5 deleted tools/comment_reconciler.py, the amendment gate that could
# short-circuit here, and the suppress_comment_llm parameter that governed it.
# A ticket's comment thread now reaches generation exactly as it did before
# the pipeline existed -- as the raw "## Comments" dump jira_mcp leaves in the
# ticket text -- which is what every install has actually done since the seam
# was pinned False on 2026-08-14. See docs/RETIRED_CAPABILITIES.md section 4.
#
# A faithful re-derivation of the front half of handle_generate_test_cases; it
# is intentionally a SEPARATE helper rather than a refactor of that handler, so
# this additive batch cannot change server-mode behaviour. The duplication is
# accepted deliberately: server byte-identity beats DRY here, and ops-3d-3 may
# converge the two only if it can be shown byte-identical against the
# equivalence fixtures.
async def ground_and_gate(args: GroundArgs) -> "str | _Grounding":
    """Run the shared front half: URL/Jira fetch, jira_preflight, Swagger
    ingest, UI extraction and the (fail-safe) ambiguity gate. Returns a markdown
    STRING to short-circuit (setup hint / preflight / clarifying questions) or a
    _Grounding to proceed."""
    grounding = await _grounding_fetch(args)
    if isinstance(grounding, str):
        return grounding
    await _grounding_gates(
        proceed_anyway=args.proceed_anyway,
        attached_images=args.attached_images,
        audit_source=args.audit_source,
    )
    return grounding


_ground_and_gate = alias(sys.modules[__name__], "ground_and_gate")
