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

TOOL_DESCRIPTION_MAX_CHARS = 1200
TOOL_DESCRIPTIONS_TOTAL_MAX_CHARS = 15000

MOBILE_BRIEF = """\
MOBILE RULES (qa_mobile_test / qa_submit_mobile_step / qa_mobile_status)

ANY action on an Android emulator or device -- install, launch, switch environment, log in, tap, type, explore -- goes through qa_mobile_test. For ad-hoc steps pass goal="..." with apply=true (no suite, no cases needed) and it drives the device step by step. Do NOT use raw adb or shell commands for device actions (adb shell input/am/pm, uiautomator dumps, ADB Keyboard broadcasts): the tool owns the destructive guard, keyboard install/restore, the run folder, evidence and per-step screenshots, and a raw command skips every one of them. To reset the app under test, pass `reset_app=true` (clears its data and relaunches it) or send a `clear_app_data` op to `qa_submit_mobile_step`; either needs `apply=true` and never touches a different app. A goal charter (explore lane) defaults to `destructive: "none"`, which REFUSES every irreversible op it meets -- a goal that reads like a reset gets a named hint to resend with `destructive: "reversible"` rather than failing on its first step with no explanation. An out-of-enum charter field (e.g. `depth: "functional"`) is never silently accepted either: the reply names the field, what was sent, what was used instead, and a fenced JSON block carrying the field's real `enum` values, sourced from this server's own charter vocabulary rather than restated by hand.

Call qa_mobile_test with NO arguments to start: it answers with whatever the machine needs next (a setup guide, an install source, a preflight list, or the start menu) and asks the tester itself. Every step that installs, downloads or launches needs apply=true, and nothing is installed or downloaded without it. With no Android SDK or no emulator (AVD) on this machine it answers with a setup guide (`setup_required: true`): relay its steps to the tester -- this server downloads no SDK (it can list, boot, create and delete AVDs: see AVDS below). Pass run_id to continue a run in ANY chat -- that takes the run over and the previous chat is told. It hands you ONE packet at a time; answer each with qa_submit_mobile_step.

The install menu's key goes in `source` and its value in `app` (a path, a URL or a package name); for `source=installed_package` specifically, `package` is also accepted for that value and is preferred when both are given. The run menu (current_suite, stored_suite, own_cases, explore, rerun_failures, resume) is a SEPARATE question answered by that same `source` argument in a later call -- send the run menu's key once the app is confirmed installed, or give `goal`/`cases`/`suite_id` instead and skip the question entirely.

Choosing to explore freely with no goal answers with a CHARTER INTAKE packet: the server's own questions about the run's terms (goal, depth, scope, destructive, budget). Put them to the tester in your own words, ask no others, and call again with `charter` set to a JSON object using the field names the packet gives. Never put a password, an OTP or any personal value in it: the charter is written into the run's report on disk, and it has no field for a secret. Anything left out takes a default, and the report names every default it used.

`locale` sets the DEVICE's language for the run -- `ar`, `ar-EG`, `en-US`. It is applied before anything is installed, read back off the device, and shown in the report header, so a run always states the language it actually ran in. When the device will not take it the call REFUSES and says what the device is in rather than running under the wrong language. Needs apply=true. It takes effect from the first frame only on an emulator this server booted itself; on one the tester already started, `persist.sys.locale` usually needs root, and the refusal says so and how to set it by hand.

If the tester already has an emulator running, pass its adb serial (e.g. `emulator-5554`) in `device_id` (`serial` is an alias for the same value; two different values are refused) -- that skips the AVD lookup entirely and never spawns a second one underneath it. `avd` names which AVD to boot when none is running and more than one is configured; leave both empty to let the server look and, if it finds exactly one booted device, use it.

If the same cases are already an unfinished run of the same app, qa_mobile_test answers with THAT run's id instead of starting another one. Pass new_run=true only when the tester has asked for a fresh run: a second run of the same cases produces a second report of the same work.

DUMP FIRST: plan from the element list. A PNG of the screen is attached only when the list is not enough (no dump, too few elements, a WebView, mostly unlabelled controls), when you pass screenshot=true, or when your last script ended with {"op": "assert", "kind": "visual", "note": "..."}; when attached, LOOK AT IT. Each packet's screen_image note says which. Coordinates always come from the element list. A screen byte-identical to the one this chat was just sent is replaced by a one-line same-screen note: the list you already have is still exact.

Submitting (qa_submit_mobile_step): the server validates the script against the action vocabulary, replays it on the device, and returns the verdict plus the NEXT packet. When a packet asked for a credential, ask the TESTER for that one field and pass it as tester_input with tester_input_field set to the field name: it is typed into the app and stored nowhere -- not in the report, the checkpoint or the audit log.

When a packet asks for TWO OR MORE fields in the same turn (a password plus a one-time code, say), pass them all at once as tester_inputs, a JSON object string mapping field name to value, e.g. '{"login_password": "...", "login_otp": "..."}' -- still masked, still typed into the app and stored nowhere. Example: type the ID, type the password ({"op": "type", "target": ..., "field": "login_password", "secret": true}), tap send, wait_until_text for the one-time-code screen, type the code the same way ({"op": "type", "target": ..., "field": "login_otp", "secret": true}), assert, then done -- with both login_password and login_otp supplied via tester_inputs on that SAME submit. The final assert and done belong in the same script as the step before them. Wait FOR something (wait_until_text, wait_until_gone, wait_until_changed, wait_until_idle), never for a number, and never sleep in a shell (sleep, Start-Sleep, timeout) between calls: the phone is not watched while you do.

Send {"op": "clear_app_data"} as a script action to wipe this run's OWN app's data and relaunch it -- gated by the same destructive guard as every other irreversible action, and never a different app. If the first packet already says app data was cleared at run start (reset_app=true), do not send clear_app_data again for that reason.

One script may carry SEVERAL actions -- up to actions.MAX_MODEL_ACTIONS (10), replayed in order until one needs the screen re-read or the case ends -- so batch what you already know you want done rather than one action per call: {"actions": [{"op": "tap", "text": "Email"}, {"op": "type", "text": "user@example.com"}, {"op": "tap", "text": "Continue"}, {"op": "assert", "kind": "new_text", "text": "Password"}]} is ONE submit, not four. A script that carries exactly one action still runs, but the reply says so: use more of the budget you already have per call.

To type into a form field, prefer {"op": "fill", "label": "Email", "text": "user@example.com"} over a tap then a type: it finds the editable field by its hint, text or description, or the nearest field below or right of a matching label, taps it, types and checks the text landed. For a secret send "secret": true with "field" naming the tester input, never the value. If two fields match, the step is refused with the candidates named: resend with a narrower label.

The NEXT packet carries a PNG of the screen only when the element list is not enough, when you pass screenshot=true, or when this script ENDS with {"op": "assert", "kind": "visual", "note": "<what to judge>"} (that stop is not charged as an escape, up to a cap). Its screen_image note says whether one is attached.

When the destructive guard stops a control and the TESTER confirms it, resubmit with confirm_destructive=true: it unlocks only the control THIS case's last stop named, not any control -- resubmit the SAME op against the SAME element the stop pointed at. A different op, a different element, or a submission before any stop was recorded, refuses by name and does NOT spend the confirm, so the next, correctly-aimed resubmission can still use it. A run whose charter says `destructive: none` still refuses.

Never report a screen state, field value or login outcome that was not read from a qa_* observation. If the server cannot type or act, stop and report the blocker by name. Do not fall back to raw adb input, and do not claim a result.

A finished run's reply STARTS with a short verdict block (verdict, each requested step, the typed-field tally). Relay that block to the tester word for word -- never upgrade, soften or summarise it into a plainer claim than it makes.

qa_mobile_status: the emulator, the lease holder and the cases done/failed/remaining. Call it after anything that outlives a tool call -- a large install, a cold boot. With no run_id it lists the runs on this machine. Pass report_now=true to also write that run's standalone HTML report and get its path back; a mid-run report is fine and says it is partial.

Saved flows (qa_submit_mobile_step `flow` / `save_flow`, managed with qa_mobile_flows): `flow`=name replays one as a single script; leave `script` empty and pass `flow_params` as a JSON object for its {placeholders}. `save_flow`=name saves this call's `script` only after it replays cleanly: typed text must be a {placeholder} (its value in `flow_params`), and a password a secret step with a `field`. A flow keeps steps and labels, never values. A step that no longer matches its screen stops the flow and hands back to you.

Saved routes (qa_submit_mobile_step `route` / `save_route`, managed with `qa_mobile_flows` kind=route): a route is a flow whose every screen is checked. `save_route`=name saves this call's `script` only after it replays cleanly, remembering the screen each step started on. `route`=name (empty `script`, `flow_params` for its {placeholders}) replays it ONLY from the screen it was saved from; each step first checks the screen, and any mismatch stops with nothing tapped and hands back to you. Typed text and passwords follow the flow rules. Use a route for a known trip (go to login, switch env to QA); it costs no planning turn.

SCREEN PEEK: `qa_capture_screens(peek=true)` returns the current Android screen's element list as text: nothing is tapped, saved or attached, and no `apply` is needed. If it says the list is not enough to act on, call `qa_capture_screens` without `peek` for the screenshot. A peek is a lookup, not a verdict.

AVDS: never run `avdmanager`, `sdkmanager` or `emulator` yourself in a shell. Use `qa_mobile_test(emulator=...)`: `list` (read-only), `boot` (`avd=NAME`, `apply=true`), `create` (`avd=NAME`, `system_image=` an installed image id shown by `list`, `apply=true`) and `delete` (`avd=NAME`, `apply=true` AND `confirm_destructive=true`, only after the TESTER says yes). A name is letters, digits, `.`, `_`, `-`, not starting with `.` or `-`. Create uses only system images that are already installed and downloads nothing: if none is, relay the steps in the reply. If the reply says this computer cannot run the emulator (no hypervisor), relay its fix text and suggest a real device; do not look for another way to start one.

Never sleep in a shell between calls: a shell sleep is a wait nobody watches; use a `wait` action with a condition.
"""

FLOW_BRIEF = """\
TEST-CASE FLOW RULES (qa_prepare_test_cases / qa_generate_test_cases / qa_submit_category / qa_submit_suite / qa_export_suite / qa_get_category_job)

JIRA URLS ARE A TWO-STEP BOOMERANG. This server holds no Jira credentials. For a Jira URL the FIRST reply is a DIRECTIVE telling you to call your OWN `mcp__atlassian__getJiraIssue` (and once more for the parent issue, when there is one), calling `qa_stage_jira` with each raw result right after that fetch, then call the prepare tool AGAIN with the same feature_or_url plus the SAME `stage_token` -- the preferred path, since no fetch result has to survive inside one argument. A client that cannot stage incrementally may still pass `jira_content_json` set to the raw result as a JSON STRING -- stringified JSON, i.e. `json.dumps(result)`, because that parameter is typed `str`; do not pass the object itself (an object is rejected by schema validation before this server sees the ticket). Do not summarise, translate or invent ticket content, and do not generate from the URL alone. If you have no `atlassian` MCP server connected, show the user the connection steps the directive includes.

WHAT TO DO WITH THE RESULT: when the payload includes `orchestration` (mode `staged_categories`), generate the categories yourself and stage each one with `qa_submit_category` AS SOON AS it is written (Path A), then `qa_prep_status` until ready and `qa_submit_suite` with an empty suite_json or the review sidecar. Path A is recommended for two reasons, neither of which is speed: staged categories survive a chat reload, and the server's duplicate prescreen runs only over a staged set. Path B (merge everything, one `qa_submit_suite` call) is supported for a client that cannot hold a multi-call session, but nothing is saved until that single call. Without orchestration, generate the full merged suite yourself and call `qa_submit_suite`. The server validates, de-duplicates, scores, exports and persists it, and replies with the finished suite + file path OR gaps to regenerate under the SAME prep_id. Use `qa_get_category_job` with category_name="all" for every category packet in ONE call (or one name for a single packet). Never fetch packets one call per category.

When the user asks for test cases WITHOUT saying where the feature comes from, call qa_generate_test_cases immediately with feature_or_url omitted -- the server asks them itself (describe / Jira / web / Swagger / mobile screens / Jira + mobile) via a dialog or menu. qa_generate_test_cases returns a PREPARE PAYLOAD, not a finished suite: a prep_id, the grounded generation prompts and the per-category job list. NOTHING is generated yet -- this server calls no model, so YOU write the cases. Continue exactly as for qa_prepare_test_cases: call qa_get_category_job(prep_id, "all") ONCE for every job packet, generate each category, qa_submit_category each one as soon as it is written, then qa_submit_suite with the same prep_id to finalize. THAT finalize reply is the one carrying the persisted suite_id and the path to the written .xlsx: relay that path to the user as the deliverable and do NOT ask which export format they want or offer to push anywhere. Call qa_export_suite only when the user names a different format themselves.

For an under-specified or no-UI ticket the reply may instead be a short list of clarifying questions (no suite is generated) -- relay them to the user. Once they answer, call again with the fuller text, or set proceed_anyway=true to generate anyway with whatever is available. If any ticket screenshots were available they are attached as image content -- inspect them directly.

IMAGE GATE (always on). ASK FIRST: for a Jira URL, ask the USER where the ticket's SCREENS come from BEFORE your first call and pass `source_plan` on it -- this server cannot read images out of Jira, only text, so it has to know, and asking up front costs ZERO extra tool calls. Only pass `source_plan` if the user ANSWERED -- never guess it, and never send `image_gate_ack=true` unless the user explicitly said the screens do not matter: that pair skips BOTH asks, including the informed one that names the screens the fetched ticket really has, which makes the gate quieter rather than cheaper. If you call without a plan, the FIRST reply is ONLY that question (nothing is fetched and nothing is prepared) and you must call again with the SAME feature_or_url plus `source_plan` (`jira` = ticket text only, `jira_attach`, `jira_device`, `jira_both`, `device`). For `jira_attach` also pass `attached_image_count` = how many images the user attached to THIS chat (the bytes stay with you; the payload then asks you to describe them and return `image_descriptions`). For `jira_device` call `qa_capture_screens` first and pass its `capture_ids` -- many screens are fine, and the ids stay valid across the Jira fetch directive and any failed attempt, so re-send them unchanged. Once the ticket is fetched a SECOND short reply may NAME the screens the ticket actually has and ask again for `jira_attach`/`jira_device`/`jira_both`/`device`; supply them, or ASK THE TESTER FIRST whether skipping is acceptable and, only if they agree, pass `image_gate_ack=true`: the server then shows the tester a confirmation dialog and only their own skip pick generates from the ticket text. `source_plan='jira'` (ticket text only) never gets that second ask -- picking it IS the tester's own answer to where the screens come from. (Send `image_gate_ack=true` together with `source_plan='jira'` up front when the user has already said the screens do not matter.)

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
mode is one of jira (analyse a feature description or Jira/issue URL), mobile (capture screens from a connected device), or jira_mobile (merge the ticket with captured screens). The mobile modes also ask for the device and offer a capture-another-screen loop, and the captured screens are attached to the reply as images for YOUR model to read -- this server makes no vision call.
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
    try:
        images = getattr(result, "images", None)
        if images is not None and type(result) is not str:
            return type(result)(text, images)
        return text
    except Exception:
        return result
