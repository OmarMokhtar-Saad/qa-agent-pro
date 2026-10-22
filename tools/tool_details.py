"""Tool-description detail moved out of the ``qa_*`` descriptions.

A tool description is billed on EVERY request of every chat (the whole
``list_tools`` JSON, ~27k chars before this change). The descriptions now keep a
summary, the arguments a caller must know and a pointer to the rules; the detail
that left them lives here and rides once per chat: ``MOBILE_TOOL_DETAIL`` after
``MOBILE_BRIEF`` and ``FLOW_TOOL_DETAIL`` after ``FLOW_BRIEF`` (joined in
``tools.tool_briefs``). Nothing was deleted, only moved.
"""

from __future__ import annotations

try:
    from tools.mobile import script_help
except ModuleNotFoundError as _exc:  # a build with no mobile package: never served
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    script_help = None  # type: ignore[assignment]

_STEP_FORMAT = getattr(script_help, "SCRIPT_FORMAT_DOC", "")

MOBILE_TOOL_DETAIL = (
    """

MOBILE TOOL DETAIL

qa_mobile_test: one call does the preflight itself (device, unlock, install), no list_devices first. Install, launch and grants happen by default (`dry_run=true` previews); grants are test setup, not a settings change. `note`: JSON {"text","kind":wait|avoid|fact,"when":{"op","rid"},"then":{"until_text|rid","ms"}} for THIS app; fact = known issue/lesson, shown at later starts. Once the device is released there is no status/stop. Never mark an unexercised check passed; ask before skipping a step. Prove the destination, not only text_absent. Credentials never go in the goal. Never use raw adb: this tool owns the destructive guard + evidence.

qa_submit_mobile_step: `note`: as on `qa_mobile_test`. `finding`: explore runs only, one observation per turn.

STEP SCRIPT FORMAT
"""
    + _STEP_FORMAT
)

FLOW_TOOL_DETAIL = """

TOOL DETAIL

qa_prepare_test_cases: feature_or_url is a description, a Jira/issue URL, a web page URL or a Swagger/OpenAPI URL. After a Jira DIRECTIVE call again with the SAME feature_or_url and `stage_token` (or `jira_content_json` as a JSON STRING, json.dumps(result)). `source_plan` is one of jira, jira_attach, jira_device, jira_both, device; never send image_gate_ack=true unless the user explicitly said the screens do not matter. Under-specified ticket: relay the questions, or pass proceed_anyway=true. If suites already exist for the source (Jira key match, any age) the first reply is a notice listing them; only for regenerate call again with proceed_anyway=true (saved as the next version).

qa_generate_test_cases: continue exactly as for qa_prepare_test_cases (qa_get_category_job(prep_id, "all") once, qa_submit_category per category, qa_submit_suite to finalize). Relay the .xlsx path; do NOT ask which export format or offer to push.

qa_stage_jira: call it after each getJiraIssue/JQL call the directive asked for; part is 'issue', 'parent' or 'siblings'; `json` is that ONE result, stringified, under ~20KB; for a bigger result save it to a file and pass `path=<absolute file>` instead.

qa_get_category_job: generation runs 6.5-24 minutes per category in your own chat model: tell the tester "Generating <category>..." before you start writing each one, so a long gap is never silent.

qa_submit_category: Path A, recommended: stage each category as soon as it is written. To stage several in ONE call pass `categories=[{category_name, suite_json}, ...]` (each is checked like a single call). Fewer cases than staged is REFUSED; replace_smaller=true only when deliberate. Do not re-submit a staged category unless a reply asked (check `qa_prep_status`).

qa_submit_suite: `suite_json` is one JSON object with a merged `test_cases` array per the payload's response_schema (an object, or a JSON string). Path A sidecar: a JSON object with `duplicate_groups` (empty list if none) and NO `test_cases`; suite_json="" also finalizes but forfeits that review. Relay the exported file path; do not ask which format. The acks (volume_floor_ack, image_relevance_ack, step_assertion_ack, quality_gate_ack) work only after the matching refusal and only on the USER's word, never your own judgement.

qa_prep_status: ready=yes means you may call qa_submit_suite with a review sidecar or suite_json=""; Path B (a full merged suite_json) does not need ready=yes.

qa_capture_screens: `peek`=true returns text; omit `serial` for a device picker; capture ids expire after 30 minutes.
"""
