"""Rules that used to sit in every ``qa_*`` tool description.

Why this module exists (S14): a tool description is re-sent to the model on
EVERY turn of every chat, so a long one is paid for again and again. The
descriptions in ``mcp_server.py`` are now capped (``TOOL_DESCRIPTION_MAX_CHARS``
each, ``TOOL_DESCRIPTIONS_TOTAL_MAX_CHARS`` in total) and keep only what the
model needs to CHOOSE and CALL a tool. Everything removed from them lives here
and rides once per chat in the reply of the tool that starts the flow:
``MOBILE_BRIEF`` in the mobile reply, ``FLOW_BRIEF`` in every
``qa_prepare_test_cases`` reply. Nothing was deleted, only moved.

This module imports nothing internal, so any layer may import it.
"""

from __future__ import annotations

from typing import Any, cast

from config.settings_guard import never_raise
from tools import tool_details
from tools.mobile_brief_text import MOBILE_BRIEF_TEXT

try:
    from tools.mobile import path_plan_text
except ModuleNotFoundError as _exc:  # a build with no mobile package: never served
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    path_plan_text = None  # type: ignore[assignment]
try:
    from tools.mobile.replay.guard_consts import NO_FABRICATION_NOTE
except ModuleNotFoundError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    NO_FABRICATION_NOTE = ""

TOOL_DESCRIPTION_MAX_CHARS = 350
TOOL_DESCRIPTIONS_TOTAL_MAX_CHARS = 8000
TOOL_LIST_JSON_MAX_CHARS = 20500
MOBILE_REMINDER_MAX_CHARS = 300

#: Rides every packet after the first of a chat, in place of ``MOBILE_BRIEF``.
MOBILE_REMINDER = (
    "Reminder (full rules: the first packet of this chat): act from the element list by its #n "
    "index. One step = every action possible on THIS screen (fill all fields, "
    "tick, tap Next); do not guess the next screen. Answer in the "
    "form given there, via qa_submit_mobile_step only."
)

MOBILE_BRIEF = MOBILE_BRIEF_TEXT
MOBILE_BRIEF = MOBILE_BRIEF.replace("@@NO_FABRICATION@@", NO_FABRICATION_NOTE)
for _tok, _name in (
    ("@@PATH_PLAN@@", "PATH_PLAN"),
    ("@@STOP_NOTE@@", "STOP_NOTE"),
    ("@@PERMISSION_NOTE@@", "PERMISSION_NOTE"),
):
    _text = getattr(path_plan_text, _name, "")
    # a blanked block takes its trailing space too: no stray double space
    MOBILE_BRIEF = MOBILE_BRIEF.replace(_tok if _text else _tok + " ", _text)

FLOW_BRIEF = """\
TEST-CASE FLOW RULES (qa_prepare_test_cases / qa_generate_test_cases / qa_submit_category / qa_submit_suite / qa_export_suite / qa_get_category_job)

JIRA URLS ARE A TWO-STEP BOOMERANG. This server holds no Jira credentials. For a Jira URL the FIRST reply is a DIRECTIVE telling you to call your OWN `mcp__atlassian__getJiraIssue` (and once more for the parent issue, when there is one), calling `qa_stage_jira` with each raw result right after that fetch (a result over 20 KB: save it to a file and pass `path=<absolute file>` instead of `json`), then call the prepare tool AGAIN with the same feature_or_url plus the SAME `stage_token` -- the preferred path, since no fetch result has to survive inside one argument. A client that cannot stage incrementally may still pass `jira_content_json` set to the raw result as a JSON STRING -- stringified JSON, i.e. `json.dumps(result)`, because that parameter is typed `str`; do not pass the object itself (an object is rejected by schema validation before this server sees the ticket). Do not summarise, translate or invent ticket content, and do not generate from the URL alone. If you have no `atlassian` MCP server connected, show the user the connection steps the directive includes.

WHAT TO DO WITH THE RESULT: when the payload includes `orchestration` (mode `staged_categories`), generate the categories yourself and stage each one with `qa_submit_category` AS SOON AS it is written (Path A), then `qa_prep_status` until ready and `qa_submit_suite` with an empty suite_json or the review sidecar. Path A is recommended for two reasons, neither of which is speed: staged categories survive a chat reload, and the server's duplicate prescreen runs only over a staged set. Path B (merge everything, one `qa_submit_suite` call) is supported for a client that cannot hold a multi-call session, but nothing is saved until that single call. Without orchestration, generate the full merged suite yourself and call `qa_submit_suite`. The server validates, de-duplicates, scores, exports and persists it, and replies with the finished suite + file path OR gaps to regenerate under the SAME prep_id. Use `qa_get_category_job` with category_name="all" for every category packet in ONE call (or one name for a single packet). Never fetch packets one call per category.

When the user asks for test cases WITHOUT saying where the feature comes from, call qa_generate_test_cases immediately with feature_or_url omitted -- the server asks them itself (describe / Jira / web / Swagger / mobile screens / Jira + mobile) via a dialog or menu. qa_generate_test_cases returns a PREPARE PAYLOAD, not a finished suite: a prep_id, the grounded generation prompts and the per-category job list. NOTHING is generated yet -- this server calls no model, so YOU write the cases. Continue exactly as for qa_prepare_test_cases: call qa_get_category_job(prep_id, "all") ONCE for every job packet, generate each category, qa_submit_category each one as soon as it is written, then qa_submit_suite with the same prep_id to finalize. THAT finalize reply is the one carrying the persisted suite_id and the path to the written .xlsx: relay that path to the user as the deliverable and do NOT ask which export format they want or offer to push anywhere. Call qa_export_suite only when the user names a different format themselves.

For an under-specified or no-UI ticket the reply may instead be a short list of clarifying questions (no suite is generated) -- relay them to the user. Once they answer, call again with the fuller text, or set proceed_anyway=true to generate anyway with whatever is available. This server never sends ticket screenshots to you; only screens you attested or supplied yourself are in your conversation.

IMAGE GATE (always on). ASK AFTER STAGING: for a Jira URL, follow the fetch directive FIRST (fetch + `qa_stage_jira`), THEN ask the USER where the ticket's SCREENS come from and pass `source_plan` on the prepare call that carries the `stage_token` -- this server cannot read images out of Jira, only text, so it has to know, and asking then costs ZERO extra tool calls. Only pass `source_plan` if the user ANSWERED -- never guess it, and never send `image_gate_ack=true` unless the user explicitly said the screens do not matter: that pair skips BOTH asks, including the informed one that names the screens the fetched ticket really has, which makes the gate quieter rather than cheaper. If you call without a plan, the FIRST reply is ONLY that question (nothing is fetched and nothing is prepared) and you must call again with the SAME feature_or_url plus `source_plan` (`jira` = ticket text only, `jira_attach`, `jira_device`, `jira_both`, `device`). For `jira_attach` also pass `attached_image_count` = how many images the user attached to THIS chat (the bytes stay with you; the payload then asks you to describe them and return `image_descriptions`). For `jira_device` call `qa_capture_screens` first and pass its `capture_ids` -- many screens are fine, and the ids stay valid across the Jira fetch directive and any failed attempt, so re-send them unchanged. Once the ticket is fetched a SECOND short reply may NAME the screens the ticket actually has and ask again for `jira_attach`/`jira_device`/`jira_both`/`device`; supply them, or ASK THE TESTER FIRST whether skipping is acceptable and, only if they agree, pass `image_gate_ack=true`: the server then shows the tester a confirmation dialog and only their own skip pick generates from the ticket text. `source_plan='jira'` (ticket text only) never gets that second ask -- picking it IS the tester's own answer to where the screens come from. (Send `image_gate_ack=true` together with `source_plan='jira'` up front when the user has already said the screens do not matter.)

RE-PREPARING THE SAME SOURCE: if a recent preparation for this source was grounded on screens and your new call carries none, this server either CARRIES THEM FORWARD (device captures it still holds -- re-sending the same `capture_ids` also still works) or REFUSES and names them. `proceed_anyway=true` does NOT dismiss that refusal -- either re-send the screens (`qa_capture_screens` again, or re-attach them with `attached_image_count`), or pass `image_carry_ack=true` once the user has agreed to generate without them.

qa_capture_screens: screens are named automatically -- from the ticket's own image labels when a prepare disclosed them, else screen_1..N. Pass `names` (comma-separated, in capture order, e.g. "Login screen, OTP screen") ONLY if the user told you what to call them. Never ask them a separate question about it. Omit device_id to get a device picker (it includes a Rescan option for a phone plugged in after the list was built); pass rescan=true to force a fresh scan. The capture_ids stay valid until a preparation actually uses them, and expire after 30 minutes.

qa_submit_suite: call it AFTER qa_prepare_test_cases with the `prep_id` and `suite_json` -- the ONE JSON object you generated from the payload (a single merged `test_cases` array conforming to the payload's response_schema). Pass it as a JSON OBJECT when your client can send one -- there is no need to serialise it into a string first. A JSON string is still accepted unchanged, so either form works. The reply is EITHER the finished suite summary plus the exported file path, OR a short structured list of coverage gaps and vague cases to fix; if so, regenerate just those and call again with the SAME prep_id. Relay the file path to the user as the deliverable; do not ask which export format they want. Two routes finalize: PATH B is the merged `suite_json`. PATH A is per-category: stage each category with `qa_submit_category`, then call qa_submit_suite with the same prep_id and a small review SIDECAR -- a JSON object carrying `duplicate_groups` (an empty list if you found none) and NO `test_cases`. The sidecar is what KEEPS the cross-category duplicate review; finalizing with `suite_json=""` also works and is equally crash-safe, but FORFEITS that review. Use qa_submit_category for Path A, the recommended route: pass the category name (canonical or known alias); names are normalized server-side. Re-submitting REPLACES that category (newest wins) and the reply SAYS SO -- do NOT re-submit a category that is already staged unless a reply asked you to; check `qa_prep_status` first. A re-submission carrying FEWER cases than the staged row is REFUSED (nothing is saved, the staged row survives) because that is usually a truncated output; pass `replace_smaller=true` only when dropping those cases is deliberate -- it is always reported. The sidecar works on EITHER route; what it needs is the field, not a particular route.

If the reply refuses the submission for being below the per-category volume this prep's payload asked for, generate the missing cases and resubmit the COMPLETE suite under the same prep_id. `volume_floor_ack` is IGNORED on the first submit by design: it only works after that refusal, and only the USER may decide it -- show them the numbers and pass it on the retry if they confirm, never on your own judgement.

If the reply refuses the submission because an attached screen was judged `relevant: "no"` (or no verdict came back at all), capture or attach the correct screen and prepare again, or resubmit the same suite with the per-image verdicts filled in. `image_relevance_ack` follows the SAME two-beat rule as `volume_floor_ack`: ignored on the first submit, honoured only after that refusal, and only ever on the USER's word.

If the reply refuses the submission because a whole category's steps have an `expected_result` that only restates the `action` ("The step completes successfully: ..."), rewrite those expected results to name the concrete observable outcome -- the on-screen message, the field/button state, or the resulting screen -- and resubmit under the same prep_id. A step that restates its action passes whether the software works or not, so it measures nothing. `step_assertion_ack` follows the SAME two-beat rule as the two acks above: ignored on the first submit, honoured only after that refusal, and only ever on the USER's word.

If the reply refuses the submission for CASE QUALITY -- steps with no concrete `test_data`, an `expected_result` that only restates its own `action`, or two cases sharing a title -- fix those cases and resubmit under the same prep_id. `quality_gate_ack` follows the SAME two-beat rule as the three acks above: ignored on the first submit, honoured only after that refusal, and only ever on the USER's word. Nothing is exported or saved on a refusal, so nothing is lost by fixing the cases instead.

qa_export_suite: live-push dry-run defaults are preserved (it writes files, it never pushes to a TMS). `output_dir` is OPTIONAL and is where the tester wants the file: pass a FULL path (`~/Desktop`, `/Users/you/Documents`). A bare relative answer like `desktop` is refused with the full path it probably meant, and the configured default is used instead. Leave it empty and each format keeps its own default location, a secure temp folder. The .xlsx that generation auto-exports is unaffected: it always lands in QA_EXPORT_DIR with no question asked. `prep_id` is NOT an export key and exports nothing on its own. It is accepted only so that a host holding a prep that was never finalized gets told the next call instead of a raw validation error. Never hand-write a CSV outside the pipeline. A prep becomes exportable only once `qa_submit_suite` finalizes it and returns a suite_id.

qa_get_category_job: use it to fetch one category's packet without re-parsing the full prepare payload; category_name should match orchestration.expected_categories. Generation itself is invisible to this server (no model call happens here) and can run 6.5-24 minutes per category from the caller's own chat model. Tell the tester a short status line -- "Generating <category>..." -- before you start writing each category, so a long gap is never silent.

RULES: qa_* MCP tools are the only path -- never import handlers or spawn your own MCP client; never read tokens/keychains; generate NEW cases, never resubmit an old export; never edit or strip the staged ticket content.
"""

# One note per tool, appended to THAT tool's own reply by
# ``append_tool_note`` (called from ``mcp_server._tracked``). Text moved out of
# the tool's description.
TOOL_NOTES: dict[str, str] = {
    "qa_feature_analysis": """\
mode is one of jira (analyse a feature description or Jira/issue URL), mobile (capture screens from a connected device), or jira_mobile (merge the ticket with captured screens). The mobile modes also ask for the device and offer a capture-another-screen loop, and the captured screens stay on the server (never sent to you); plan from the element lists -- this server makes no vision call.
""",
    "qa_configure_jira": """\
qa_configure_jira is deprecated. Jira tickets are read through YOUR OWN Atlassian MCP connection (mcp.atlassian.com, OAuth, Jira Cloud). The verify result is read once and discarded; the server reports a real verified / not-connected verdict plus the exact connection steps for this editor.
""",
    "qa_selfcheck": """\
qa_selfcheck calls every handler with its own defaults, so no acknowledgement and no `apply=true` is ever sent. It uses the live server because that is the only reference to the built registry, and that server is the CONFIGURED edition, so the answer is about the install in front of you.
""",
    "qa_network_watch": """\
qa_network_watch uses the SAME mechanism a mobile run uses (emulator console pcap plus a `/proc/net/tcp` owner sampler), given an entry point that needs no run. It works on an emulator only -- a physical device is refused by name, because the console does not exist there.
""",
    "qa_host_check": """\
qa_host_check never prompts for a password and stores no credential. Its verdict is cached for this server process and the reply says so; pass `refresh=true` to probe again after IT changes something. The reply tells you which steps are attemptable.
""",
    "qa_prepare_api_tests": """\
qa_prepare_api_tests with project="<name>" scopes the endpoint and auth-flow registry, so a dependency you already built is reused instead of rebuilt. Without it, everything still works for this session only.
""",
    "qa_machine_report": """\
qa_machine_report returns `backend` (version + edition in one call), `doctor` (from the same producers `qa-doctor` uses) and `provisioning` (one fixed `off` row: this server provisions no SDK or emulator). It repairs nothing, writes no `.env` and edits no client config.
""",
    "qa_setup_capture": """\
qa_setup_capture with action="prepare" runs the SAME routine the consent step inside `qa_mobile_test` calls, so this tool never re-decides anything a run already decided; action="remove" forgets the device's trust and, like every other device-touching step in this lane, needs `apply=true`.
""",
    "qa_doctor": """\
The doctor check is read-only by default; fix=true writes the hosted `atlassian` entry and keeps a timestamped `.env.bak-*`. Call again with fix=true once you see a repair it can make.
""",
    "qa_write_api_test": """\
qa_write_api_test with apply=true writes via the framework repo's own ops pipeline (branch -> write -> spotless -> test-compile -> commit).
""",
    "qa_api_project": """\
qa_api_project adopts an existing api-automation-framework checkout in place; pass use="<name or path>".
""",
}


def append_tool_note(name: str, result: object) -> object:
    """*result* with the tool's note appended; unchanged when there is none.

    Only text replies are touched. A ``str`` subclass that carries attachments
    (``FeatureAnalysisReply``) is rebuilt as the same type so ``.images``
    survives. Never raises.
    """
    note = TOOL_NOTES.get(str(name).replace("-", "_"))
    if not note or not isinstance(result, str):
        return result
    text = str(result) + "\n\n---\nTool note: " + note.strip()
    with never_raise():
        images = getattr(result, "images", None)
        if images is not None and type(result) is not str:
            return cast("Any", type(result))(text, images)
        return text
    return result


# Detail moved out of the tool descriptions (fix/1510 WP-G): once per chat.
MOBILE_BRIEF += tool_details.MOBILE_TOOL_DETAIL
FLOW_BRIEF += tool_details.FLOW_TOOL_DETAIL
