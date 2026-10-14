"""MCP Prompt bodies: generate and Jira (leaf of tools/guidance.py)."""

from __future__ import annotations

_P_GENERATE = """\
Generate a full test suite for a feature, end to end.

Ask the tester for the feature first if they have not described it -- one
paragraph of behaviour is enough, and a link to a ticket is better.

1. Call `qa_prepare_test_cases` with what they gave you. It returns a prep_id,
   a system prompt, shared user context, a response schema, and a job per test
   category. It does NOT generate anything -- you do.
2. If the reply is a DIRECTIVE (a ticket URL was given), follow it first: call
   the Atlassian tool it names, pass the RAW JSON straight back into the
   argument it names, and re-call `qa_prepare_test_cases`.
3. If the reply is a markdown menu, your client could not show a dialog. Put
   the menu to the TESTER, then follow the mapping the menu itself prints --
   each option carries the backticked `key` that identifies it, and the menu
   says which tool and argument that key means. Never send a position, and
   never pick an option for them.
4. If the reply REFUSES and names an acknowledgement argument -- an ambiguity
   gate (`proceed_anyway`), a missing or stale screenshot (`image_gate_ack`,
   `image_carry_ack`), a suspected duplicate run -- summarise the concern in
   plain language and re-send with that argument ONLY after the tester says
   go, on a later turn. Acknowledging on their behalf defeats the gate.
5. Call `qa_get_category_job(prep_id, "all")` ONCE. That is every job packet in
   one round trip. Do not call it per category and do not call it twice.
6. Generate each category against ITS job instruction, the shared system prompt
   and user context, emitting only an object matching the response schema and
   setting each case's `category` to that job's category name exactly. Run any
   step-zero jobs the payload lists yourself, in the parent turn, first.
7. Call `qa_submit_category` for each category the moment it is finished. Do
   not hold eight categories in memory to submit at the end -- staged work
   survives a reload, memory does not. `qa_prep_status` tells you what is left.
8. Call `qa_submit_suite` with the prep_id to finalize. If it comes back with
   coverage gaps or weak cases instead, fix those and resubmit under the SAME
   prep_id -- that is a step in the flow, not an error.
9. The submit already wrote an .xlsx and gave you its path: hand the tester
   THAT path as the deliverable, and never ask which format they want. Call
   `qa_export_suite` only if they ask for another one themselves (csv,
   gherkin, playwright, testrail).

Read the prepare payload's own instructions and follow them where they are more
specific than these steps: they are resolved for this run and this ticket.
"""

_P_JIRA = """\
Generate a test suite from a Jira ticket.

This server never contacts Jira. Your client's own Atlassian MCP connection
does, and the server tells you exactly what to call.

1. Call `qa_prepare_test_cases` with the ticket URL or key.
2. The reply is a DIRECTIVE. It names the tool -- `mcp__atlassian__getJiraIssue`
   under the standard prefix, but use the prefix the directive prints, because
   clients namespace differently -- the fields to request, and the argument to
   put the answer in. It may ask for a SECOND call for a parent issue.
3. Make those calls and pass the RAW JSON result back UNMODIFIED. Do not
   summarise it, re-key it, or hand back your own prose: the server parses the
   real response shape and re-asks if it gets anything else.
4. If you have no Atlassian connection, the reply lists the setup steps for
   your client instead of failing. Relay them and stop -- `qa_configure_jira`
   covers the server-side half.
5. From here the flow is the ordinary one: `qa_get_category_job(prep_id,
   "all")` once, generate, `qa_submit_category` per category as it lands,
   `qa_submit_suite` to finalize. The submit hands back a finished .xlsx path
   -- relay it, and never ask which format they want; call `qa_export_suite`
   only if they ask for another format themselves.

Attachments and screenshots on the ticket are not fetched by this server
either. If the reply asks for images, get them through your client and attach
them to the call it names.
"""
