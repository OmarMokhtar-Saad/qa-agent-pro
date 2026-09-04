"""Protocol-native guidance: the server ``instructions`` block and the MCP
Prompts, as pure text.

Every tester-facing workflow this server implements is a HANDSHAKE -- prepare,
then submit -- because no model runs here: the calling agent's own chat model
does every generative step. That contract lives in the tool descriptions and in
the payloads ``agents/host_mode.py`` builds, which means a client only learns it
AFTER it has already called a tool, and only for the tool it happened to call.
Two things fix that at the protocol level, and this module is the text for both:

* ``server_instructions`` -- the ``instructions`` string FastMCP sends once at
  ``initialize``. Ambient, always in the client's context, so it has to be
  SHORT: it names the tool ORDER and the three replies that are NOT the
  answer, and nothing else.
* ``prompt_texts`` -- named MCP Prompts the tester invokes deliberately
  (``/mcp__qa-agents__qa_generate`` in Claude Code). These may be long, because
  the client pays for them only when one is chosen.

Two rules this module exists to keep:

1. **It DESCRIBES the flow; it never duplicates a payload.** The authoritative
   instruction text for a generation run is the one ``host_mode`` puts in the
   prepare payload, resolved against that run's flags and that ticket's
   content. A copy here would drift silently and a tester would never know
   which one was stale. So the text says *which tool comes next and what to do
   with each reply shape*, and stops there.
2. **It is EDITION-AWARE by parameter, not by import.** Both entry points take
   the gates as booleans, so this module imports nothing internal -- no
   ``config.settings``, no ``tools.mcp_handlers`` -- and the caller
   (``mcp_server.build_server``) resolves each gate exactly once, from the same
   expression that decides whether the tool is registered at all. Guidance
   naming a tool the tester's edition does not have is worse than no guidance:
   it sends the agent to a tool call that can only fail.

There is NO settings flag here. Per the flag policy this ships ON: guidance a
tester never sees is dead text, and an off-path nobody exercises is a bug
waiting for the one install that flips it.
"""

from __future__ import annotations

# --- Server instructions ---------------------------------------------------- #
# Sent once at initialize and resident in the client's context for the whole
# session, so length is a real cost paid on every turn. ``MAX_INSTRUCTION_LINES``
# is the budget, pinned by a test: an ambient block that grows without a ceiling
# is how a helpful paragraph becomes a permanent tax.
# 2026-09-02: 40 -> 42, and the arithmetic is the justification rather than
# headroom-by-feel. Measured before the change: 36 lines (full), 39 (full +
# api), 32 (test-cases-only). The mobile block is two lines of text and
# ``server_instructions`` joins its parts with a blank line, so the new worst
# case -- core + full + api + mobile -- is exactly 42. The block is edition
# gated on a kill-switch that is OFF by default forever, so the ambient cost
# for a default install is unchanged at 39.
MAX_INSTRUCTION_LINES = 42


_INSTRUCTIONS_CORE = """\
QA Agents turns a feature or a ticket into a professional test suite, a bug
report, or a guided exploratory session, for testers who do not write code. NO
model runs on this server: YOU generate every artifact and hand it back through
the matching submit tool. New machine, or odd behaviour: `qa-doctor` first.

TEST CASES -- in this order:
1. `qa_prepare_test_cases` returns a prep_id, the prompts, and the per-category
   job list. Nothing is generated yet.
2. `qa_get_category_job(prep_id, "all")` returns EVERY job packet in ONE call.
   Never one call per category, never re-fetch a packet you hold.
3. Generate each category and call `qa_submit_category` for each AS SOON AS
   it is written, so a reload cannot lose it, and so the server's duplicate
   prescreen sees the staged set. `qa_prep_status` shows what is outstanding.
4. `qa_submit_suite` with the prep_id finalizes -- or hands back gaps to fix
   and resubmit under the SAME prep_id.
5. The submit already wrote an .xlsx -- relay ITS path, and never ask which
   format they want. `qa_export_suite` only if the tester names another.
When they have not said where the feature comes from, call
`qa_generate_test_cases` with no arguments -- it asks them itself.

JIRA: this server never calls Jira. A ticket URL comes back as a DIRECTIVE
naming the tool YOUR client must call (`mcp__atlassian__getJiraIssue`, at the
prefix it prints); make that call, pass its RAW JSON back unmodified in the
argument named. Setup: `qa_configure_jira`. Prior work: `qa_search_corpus`.

REPLIES THAT ARE NOT THE ANSWER, and the only correct response to each:
* a DIRECTIVE -- run the named call, return with its raw result;
* a numbered markdown menu -- your client could not show a dialog; put it to
  the TESTER, re-call with the number THEY pick;
* a refusal naming an acknowledgement argument (`proceed_anyway`,
  `image_gate_ack`, ...) -- explain the concern, re-send with that argument
  only after they say go, never on the turn you were refused.
"""

_INSTRUCTIONS_FULL = """\
BUGS AND EXPLORATION are prepare/submit pairs too -- `qa_bug_report` then
`qa_submit_bug_report`, `qa_explore_step` then `qa_submit_explore_step`: YOU
write the text between them. `qa_wizard` helps a tester choose.
"""

_INSTRUCTIONS_API = """\
API TESTS: `qa_api_project`, then `qa_prepare_api_tests`,
`qa_submit_api_tests`, `qa_write_api_test`.
"""
# Two lines, and they buy their place: the two failure modes an ambient block
# can actually prevent here are re-fetching a packet the model already holds
# (it costs the tester's own tokens) and starting over in a new chat instead of
# resuming. Everything else -- credentials, the guard, the budgets -- is in the
# `qa_mobile_run` PROMPT, which a client pays for only when it is invoked.
_INSTRUCTIONS_MOBILE = """\
MOBILE: `qa_mobile_test` gives ONE packet at a time -- answer it with
`qa_submit_mobile_step`, never re-fetch it; resume in any chat by run id.
"""


def server_instructions(
    *, test_cases_only: bool, api_tests: bool, mobile: bool = False
) -> str:
    """The ``instructions=`` block for this edition.

    ``test_cases_only`` mirrors ``tools.mcp_handlers._test_cases_only()``,
    ``api_tests`` mirrors ``settings.qa_api_test_enabled`` and ``mobile``
    mirrors ``tools.mcp_handlers._mobile_lane_enabled()`` -- the caller passes
    the SAME expressions that gate registration, so a tool can never be named
    here and absent there.

    ``mobile`` defaults to False so an existing caller keeps its behaviour. Its
    block is a SIBLING of the full block, never nested inside it: the caller's
    ``mobile`` argument is already ``_mobile_lane_enabled()``, so nesting it
    under ``not test_cases_only`` was a SECOND copy of an edition gate -- the
    same defect that kept the lane off every distribution build until
    2026-09-04, and it would have survived that fix, leaving a dist tester with
    three registered tools and an instructions block that never names them.
    ``prompt_texts`` gates the ``qa_mobile_run`` prompt on ``mobile`` alone;
    these two now agree.
    """
    parts = [_INSTRUCTIONS_CORE]
    if not test_cases_only:
        parts.append(_INSTRUCTIONS_FULL)
        if api_tests:
            parts.append(_INSTRUCTIONS_API)
    if mobile:
        parts.append(_INSTRUCTIONS_MOBILE)
    return "\n\n".join(p.strip("\n") for p in parts)


# --- Prompts ----------------------------------------------------------------- #
# One entry per MCP Prompt: name -> (one-line description, body). The
# description is what a client lists in its prompt picker, so it reads as an
# imperative the tester recognises, not as a module summary.

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
3. If the reply is a numbered menu, your client could not show a dialog. Put
   the menu to the TESTER, get their number, re-call with it. Never pick one
   for them.
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

_P_BUG = """\
Write a structured bug report from what the tester describes.

1. Call `qa_bug_report` with their description, however rough. It returns a
   task_id and a brief: the sections to write, the evidence to chase, and what
   this tracker expects.
2. Ask the tester for anything the brief flags as missing -- build, device,
   steps, actual versus expected. One round of questions, not an interrogation.
3. Write the report yourself against the brief. You are the model here.
4. Call `qa_submit_bug_report` with the task_id and your report text.

If the reply is a numbered menu, your client could not show a dialog: put the
menu to the tester and re-call with their choice.
"""

_P_EXPLORE = """\
Coach a tester through an exploratory testing session, one step at a time.

1. Call `qa_explore_step` with the charter or feature under test. It returns a
   task_id and the brief for the NEXT step -- one step, not a plan.
2. Turn the brief into a concrete instruction the tester can act on now, and
   wait for what they observed.
3. Call `qa_submit_explore_step` with the task_id and their finding written up.
4. Repeat from step 1 for the next step. The session adapts to what they hit,
   so do not batch steps ahead or invent the next one yourself.

Stop when the tester says the charter is covered, and offer to turn what they
found into a bug report.
"""

_P_API = """\
Generate REST-Assured / TestNG API tests into a Java project.

1. Call `qa_api_project` to create a project from the configured template, or
   to continue with an existing one. The reply names the project marker it
   wrote or found -- that file, not a convention, decides the package.
2. Call `qa_prepare_api_tests` with the endpoints, spec URL, or description.
   It collects what it still needs first; while the intake status is
   `collecting` it is asking you for more, and it turns `confirmable` only when
   it has enough. Confirm with the TESTER at that point, not before.
3. Generate the cases and call `qa_submit_api_tests` with them.
4. Call `qa_write_api_test` to render Java into the project. Always call it
   WITHOUT `apply` first and show the tester the dry run. A real write needs
   `apply=true` AND a framework path AND this install configured for real
   writes -- installs ship dry-run-first, so `apply=true` alone still returns a
   dry run. The dry run itself names which of the three this machine is
   missing, so read its reply rather than guessing -- `qa-doctor` does NOT
   report the API framework path or the write flags (measured 2026-09-02; it
   used to be pointed at here, and did not carry the answer).

Never write into the tester's repo without showing them the dry run first.
"""

_P_MOBILE = """\
Run test cases, or explore freely, on an Android emulator.

Nothing runs until the tester has agreed to it: every step that installs an
app, downloads an SDK or launches anything needs `apply=true`, and you send
that only after the tester has said go, on a later turn.

1. Call `qa_mobile_test` with no arguments. It answers with whatever the
   machine needs next -- a provisioning preview, an install menu, a preflight
   list with a fix per failed check, or the start menu.
2. A numbered markdown menu means your client could not show a dialog. Put it
   to the TESTER, and re-call with the option THEY pick. Never choose for them.
3. Once a run starts you get ONE packet: a case (or an exploratory goal), the
   pruned screen, and the exact action vocabulary you may use. Plan the whole
   case as a short action script and send it straight back with
   `qa_submit_mobile_step`. Do not echo the screen, and do not ask for a packet
   you already hold.
4. If a step comes back needing a credential, ask the TESTER for that ONE field
   in chat and pass the value in `tester_input` with `tester_input_field` set
   to the field name. It is typed into the app and stored nowhere -- not in the
   report, the checkpoint or the audit log. Never invent one, never reuse one
   from an earlier run, and never repeat it back in your own message.
5. If a step stops in front of something irreversible -- a confirm, a delete, a
   payment -- nothing was tapped. Say so, ask the tester, and resubmit the same
   script only if they agree.
6. Every case is checkpointed as it finishes. To continue in a NEW chat, call
   `qa_mobile_test` with the `run_id` and no session token: that takes the run
   over, and the other chat is told at its next call. `qa_mobile_status` reads
   the whole run back from disk and changes nothing.

Device selection: if the tester already has an emulator running, pass its adb
serial (e.g. `emulator-5554`) in `serial` on your NEXT `qa_mobile_test` call
rather than letting the server guess or start a second one. Several booted
devices come back as a numbered menu -- put it to the tester and resend with
their pick.

A reply naming "booting" or "provisioning" is not something to retry by
calling `qa_mobile_test` again: call `qa_mobile_status` instead and keep
polling it until it reports ready, then continue with the `run_id`.

Resuming works on an UNFINISHED run. A run whose cases have all reached a
verdict is complete, and `unverified` and `blocked` are verdicts -- so resuming
it hands back the report, not more work. To attempt such a case again, start a
new run for it, and give the script an `assert`: `unverified` means nothing was
checked, and a script that checks nothing cannot pass however many times it is
run.

7. When the run finishes, this server writes a self-contained HTML report next
   to the run's own files and opens it. Relay the PATH it names. A report can be
   built at any time, mid-run included, with `qa_mobile_status` and
   `report_now=true`.

A reply that says the call stopped short of its own time budget is not an error
either: no step was half-done, and calling `qa_mobile_test` again with the run
id carries on from the same place.

A step that says the emulator is still starting, or an install is still
running, is not an error: those outlive a single tool call by design. Call
`qa_mobile_status` and continue when it reports ready.
"""

_PROMPTS: dict[str, tuple[str, str]] = {
    "qa_generate": ("Generate a full test suite for a feature", _P_GENERATE),
    "qa_jira_to_tests": ("Generate a test suite from a Jira ticket", _P_JIRA),
    # NOT `qa_bug_report`: that is the TOOL's name. Prompts and tools are
    # separate MCP registries, so the protocol allows the clash -- but a
    # client picker lists both, and two identical rows with different
    # behaviour is a trap for the tester. Every other prompt name here
    # already differs from every tool name; the test pins that.
    "qa_bug_report_workflow": ("Write a structured bug report", _P_BUG),
    "qa_explore": ("Run a guided exploratory testing session", _P_EXPLORE),
    "qa_api_tests": (
        "Generate REST-Assured API tests",
        _P_API,
    ),  # NOT `qa_mobile_test`, for the reason recorded above `qa_bug_report_workflow`:
    # a prompt sharing a tool's name puts two identical rows with different
    # behaviour in the client's picker. Pinned by the same test.
    "qa_mobile_run": ("Run tests on an Android emulator", _P_MOBILE),
}

#: Prompts that name a tool only the FULL edition registers.
_FULL_ONLY = frozenset({"qa_bug_report_workflow", "qa_explore"})

#: Prompts that name a tool registered only when the API test agent is on.
_API_ONLY = frozenset({"qa_api_tests"})
#: Prompts that name a tool registered only when the mobile lane is on.
_MOBILE_ONLY = frozenset({"qa_mobile_run"})


def prompt_texts(
    *, test_cases_only: bool, api_tests: bool, mobile: bool = False
) -> dict[str, str]:
    """``{prompt_name: body}`` for this edition, in registration order.

    The same gates as :func:`server_instructions`, resolved by the caller. A
    prompt is omitted rather than degraded: half a workflow whose middle step
    names a tool that is not there wastes more of the tester's time than no
    prompt at all.
    """
    out: dict[str, str] = {}
    for name, (_desc, body) in _PROMPTS.items():
        if name in _FULL_ONLY and test_cases_only:
            continue
        if name in _API_ONLY and not (api_tests and not test_cases_only):
            continue
        if name in _MOBILE_ONLY and not mobile:
            continue
        out[name] = body
    return out


def prompt_description(name: str) -> str:
    """The one-line description a client shows in its prompt picker."""
    entry = _PROMPTS.get(name)
    return entry[0] if entry else ""
