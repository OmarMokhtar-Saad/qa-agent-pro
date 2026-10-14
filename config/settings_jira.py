"""Jira context-fetch fields (base URL, AC field, parent and size caps).

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class JiraSettings(BaseModel):
    # Still read: used ONLY to recognise a self-hosted Jira on a custom domain
    # (tickets.example.com) as a ticket URL rather than a generic web page.
    jira_base_url: str = ""
    # JIRA_EMAIL / JIRA_API_TOKEN were DELETED on 2026-08-15 (dead-code
    # deletion, batch D1) together with tools/jira_attachments.py, which
    # was their ONLY reader in the whole tree. Nothing has sent this pair
    # anywhere since 2026-08-13, when the credentialed attachment fetch
    # was hardcoded OFF. An existing .env still carrying the keys loads
    # cleanly and silently: model_config uses extra="ignore".
    #
    # Re-adding them is part of reviving that fetch, and the safety
    # contract a revival must satisfy (per-hop host re-check, HTTPS-only
    # Basic auth to the configured Jira host, MIME allowlist, size caps,
    # never-raise) is in docs/RETIRED_CAPABILITIES.md.
    # Tool-name prefix the CALLING agent uses for its Atlassian MCP tools.
    # Claude Code / Desktop expose them as `mcp__atlassian__getJiraIssue`;
    # other clients namespace differently, so the directive this server returns
    # must be adjustable rather than hardcoded. Empty falls back to the Claude
    # form. Not a feature flag -- it changes wording, never behaviour.
    qa_jira_mcp_tool_prefix: str = "mcp__atlassian__"
    # Jira custom-field id that holds Acceptance Criteria. Different instances use
    # different ids, so this is configurable (QW-11 / I-023 / B-015). When it is
    # empty -- on a ticket or by configuration -- jira_fetcher falls back to
    # scanning the description for an "Acceptance Criteria" heading, AC_JOB
    # derives criteria on the host, and build_fetch_directive drops the field from
    # the request rather than asking Jira for an empty field name.
    #
    # 2026-08-31: the default used to be "customfield_10016", and this tree
    # already documented in four places (rtm.py, jira_mcp.py x2, mcp_handlers.py)
    # that on a real workspace that id is a DATE field -- whose timestamp once
    # became the only "acceptance criterion" on a live run. A live Cursor session
    # confirmed every unconfigured install still ships it inside the fetch
    # directive AND the sibling JQL. Guessing a field id we have evidence is
    # wrong is worse than asking for none.
    jira_ac_field: str = ""

    # Searching the OTHER custom fields for one whose value reads like
    # requirements when the configured `jira_ac_field` does not is OFF, and
    # unsettable: QA_JIRA_AC_FIELD_DISCOVERY was DELETED 2026-08-13
    # (flag-surface reduction, batch 8a) and hardcoded to its default, False.
    # Adopting the wrong field is exactly the failure that made a date field's
    # timestamp the only "acceptance criterion" on a real run, and the search was
    # never validated against a real workspace. `qa-doctor` still discloses which
    # field was resolved, so a mis-configured field stays visible without this.
    # The surviving seam is tools.jira_mcp._ac_field_discovery_on().
    # See docs/FEATURE_FLAGS.md.

    # Asking the tester's own chat model to classify every generated case as
    # entailed / ungrounded / unspecified against the ticket is OFF, and
    # unsettable: QA_HOST_GROUNDING_REVIEW_ENABLED was DELETED 2026-08-13
    # (flag-surface reduction, batch 8a) and hardcoded to its default, False.
    # It changed what the host was ASKED to do and spent the tester's tokens on
    # one judgement per case for a precision nobody had measured, which is what
    # made it an experiment rather than an advisory. tools/grounding_verdicts.py
    # and agents.host_grounding.build_grounding_section are RETAINED with every bound
    # they carry (ids matched against the suite's own, verdicts enum-gated, notes
    # capped, a 40% proportional ceiling, cases MOVED rather than deleted), so a
    # submission that carries verdicts anyway is still handled safely. What is
    # gone is only the INSTRUCTION that asks for them; the seam is
    # agents.host_grounding.grounding_review_enabled(). See docs/FEATURE_FLAGS.md.
    # Parent-story context (JIRA_FETCH_PARENT). Default **OFF** since
    # 2026-09-25 (Air onboarding test) -- this REVERSES the "deliberate
    # exception to the defaults-OFF rule" this comment used to record: a
    # tester who never asked for parent-story context got it fetched and
    # injected anyway, with no way to know why extra Jira calls were
    # happening. Category: operator-choice (unchanged) -- opt in per install
    # with JIRA_FETCH_PARENT=true. The two flags this comment used to name
    # alongside it are both gone: QA_AUTO_EXPORT_XLSX was hardcoded ON in
    # batch 4 (2026-08-12) and QA_AMBIGUITY_GATE_SEVERITY was deleted by
    # P2-J2 with the gate it switched.
    # A Jira SUB-TASK ("Add Apple Pay button") carries almost no requirements
    # — they live on the parent story — so fetching only the sub-task makes
    # the generator either fabricate a suite or trip the ambiguity gate, which
    # is exactly the failure this flag exists to prevent when turned ON.
    # parent/subtasks/issuelinks are already in the DEFAULT REST field set
    # (free to extract); only the parent BODY costs one extra authenticated
    # GET, and a ticket with no parent makes no extra call at all. The result
    # is injected as clearly-labelled BACKGROUND, never as the thing under
    # test. Set JIRA_FETCH_PARENT=true to opt in.
    jira_fetch_parent: bool = False
    # Character cap on the composed parent/related-issue BACKGROUND block so a
    # huge epic description can never crowd out the sub-task under test. 0
    # means "emit no background block" (same convention as jira_max_comments),
    # which is why this field is deliberately NOT in _POSITIVE_INT_FIELDS.
    # 2026-08-31 (F1/F2): the whole ticket used to be head-sliced at a
    # hardcoded 3000 chars BEFORE wrap_untrusted, so its "...[truncated]"
    # marker never fired and the appended comment thread silently never
    # reached a model. Both caps are now passed INTO wrap_untrusted, which
    # marks the cut, and both are raised: the atomic checklist already reads
    # 12000 chars of the SAME description, so a smaller generation budget
    # guaranteed the coverage denominator held requirements the generator was
    # never shown. 0 keeps the wrap_untrusted default rather than emitting
    # nothing, so a mis-set value cannot blank the prompt.
    jira_max_context_chars: int = 12000
    # Same fix for the acceptance-criteria block, cut at 2000. Measured on
    # TICKET-10051 that dropped BR08..BR14 of a fourteen-rule ticket, mid-word.
    jira_max_ac_chars: int = 6000
    jira_max_parent_chars: int = 2500
