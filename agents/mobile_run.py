"""Prompt builders for the mobile lane, in the ``agents/host_mode.py`` style.

No model runs on this server, so every decision a mobile run needs -- what to
tap, whether the goal was reached, what to do with a screen the plan did not
anticipate -- is a PACKET handed to the tester's own chat model and answered
through a submit tool. This module builds those packets and nothing else: it
touches no device, opens no file and makes no network call.

Three rules it holds, each pinned by a test:

* **Nothing here imports ``tools/mcp_handlers.py``.** That edge is what lets a
  packet be built and asserted on without the MCP transport.
* **The RAW dump never enters a packet.** Packets carry ``perception``'s pruned
  screen block, already neutralised and length-capped. A packet containing
  ``<node`` or ``<hierarchy`` is a bug, and a test says so.
* **A credential never enters a packet.** The planner is told to ask for a field
  BY NAME through ``ask_tester`` and to reference it by name in a ``type``
  action; the value only ever exists in the tester's own chat turn and on the
  device's stdin. ``build_tester_request`` carries the field name and the
  prompt, never a value.
"""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod
from tools.mobile import perception, run_store, screen_audit
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

MAX_TRACE_ENTRIES = 40
MAX_CASE_STEPS = 40

_SYSTEM_PROMPT = (
    "You are driving a real Android app on an emulator for a manual QA tester.\n"
    "\n"
    "You are given ONE screen, described as a list of elements with short ids, "
    "and ONE test case. You return ONE script: a bounded list of actions from a "
    "fixed vocabulary. The server replays your script on the device, re-reads "
    "the screen after every action that can change it, and hands control back "
    "to you the moment something does not match -- so you never have to guess "
    "what happens two taps from now.\n"
    "\n"
    "How to be good at this:\n"
    "- Type into the element listed under `fields`; tap the control listed "
    "under `controls` whose label is the one you want. A control labelled "
    "Voice, Record or Mic is NEVER the Send control, however close to it it "
    "sits on the screen.\n"
    "- Target by `role` or `label` for anything that follows another action: those "
    "are computed by the server from the element's own content and survive a "
    "re-layout. `rid` is next best. The short ids are THIS SCREEN ONLY -- an id "
    "describes the element as it was on the screen you were given, bounds "
    "included, so it stops matching the moment that element moves. It can never "
    "match a DIFFERENT element, so a stale one is handed back rather than "
    "mistapped -- but being handed back costs you a turn.\n"
    "- Never send TWO selectors that name different elements -- any pair, not "
    "just an id with a text -- and never send one that matches nothing "
    "alongside one that matches: both read as a plan built on an older screen "
    "and are handed back. Never invent coordinates: you cannot see the screen, "
    "only its structure.\n"
    "- Plan only as far as you can SEE. Stop the script at the first action "
    "whose target is not on this screen. Being handed the next screen costs one "
    "round trip; a wrong tap costs the tester a re-run.\n"
    "- Assert what the case says to verify, using assert actions, not prose. An "
    "assert that fails comes back to you with the screen that failed it.\n"
    "- For 'the app replies', assert new_text (optionally with contains), or "
    "text_present naming something the reply must say. NEVER screen_changed: "
    "it only says the screen moved, which any navigation does, and it is not "
    "evidence that an answer arrived.\n"
    "- If the app needs a credential, an OTP or any personal value, do NOT "
    "invent one and do NOT ask the tester in prose: emit ask_tester(prompt, "
    "field), then reference that same field name from a type action with "
    "secret=true and no text. The value never passes through you.\n"
    "- Everything you need is in this packet. Do NOT drive the device yourself: no "
    "`adb shell`, no `uiautomator dump`, no `input tap`, no terminal at all. A raw "
    "shell call bypasses the destructive-action guard, the IME, the evidence "
    "capture and the step record, so nothing it does reaches the tester's report "
    "-- and one submit replays a whole SCRIPT where a shell call replays one "
    "action. If the vocabulary cannot express what this case needs, end with "
    "done(verdict='blocked') NAMING the op you were missing.\n"
    "- Finish with done(verdict, reason). 'pass' means you verified the case's "
    "expected results; 'fail' means you verified they did not hold; 'blocked' "
    "means you could not tell. Guessing 'pass' is the worst answer available."
)

#: Both bounds, in the model's own words, taken from the vocabulary module so
#: the packet can never quote a number the server does not enforce.
_BUDGET_NOTE = (
    "One submit replays for at most "
    + str(actions_mod.SUBMIT_BUDGET_MS)
    + " ms of device time, and the waits in one script may total at most "
    + str(actions_mod.MAX_TOTAL_WAIT_MS)
    + " ms -- over that total the whole script is refused. Split a long case "
    "into several short scripts: the server hands you the screen back between "
    "them, which costs one round trip and never a re-run."
)

#: The two recoveries, offered on EVERY escape rather than only when the
#: executor classified one: the advice is correct whenever the screen above is
#: not the app's, and a conditional string is one more thing to drift out of
#: step with the outcome that would have triggered it.
_RECOVERY_NOTE = (
    "If the screen above belongs to another app or is a system permission "
    "dialog, the case has not failed. A permission dialog is ORDINARY screen "
    "content: its buttons are in the element list above, so if the app needs "
    "that permission, tap the button that grants it by its own label. Send "
    "`back` only when the app does not need it -- back denies the permission, "
    "and a case that fails because you denied it failed for your reason and not "
    "the app's. Otherwise send `launch` to bring the app under test back to the "
    "front. Put that "
    "recovery FIRST IN THE SAME SCRIPT as the rest of your plan -- a script "
    "that reaches its end without done() ENDS the case, so a recovery sent on "
    "its own finishes the case instead of continuing it. "
)

#: Said on the packet BEFORE the last hand-back, not after it. A model that
#: learns the budget is spent only when the case is already `blocked` has been
#: told nothing it could act on.
_LAST_ESCAPE_NOTE = (
    "This is the LAST hand-back for this case: if it is not finished from "
    "here, it is recorded blocked and will not be handed out again in this "
    "run. Either finish it, or end it with done(verdict='blocked') saying "
    "what stopped it -- an honest blocked is worth more than a fourth attempt "
    "you do not get. "
)

_CASE_INSTRUCTION = (
    "Return ONE script for the test case below, planned from the screen above. "
    "Emit ONLY a JSON object matching response_schema -- no prose, and do not "
    "echo the screen back."
)

_ESCAPE_INSTRUCTION = (
    "Your previous script stopped part-way. The trace below shows what ran and "
    "why it stopped, and the screen above is the CURRENT one. Return a NEW "
    "script that continues the case from here -- do not repeat actions the "
    "trace records as already done. Emit ONLY a JSON object matching "
    "response_schema."
)

_EXPLORE_INSTRUCTION = (
    "This is one turn of a bounded exploratory session. The budget is TURNS, "
    "not actions, so put the WHOLE of your next intent in ONE script rather "
    "than spending a turn per action -- a script may carry up to "
    + str(actions_mod.MAX_ACTIONS)
    + " actions under a "
    + str(actions_mod.SUBMIT_BUDGET_MS // 1000)
    + "s device budget, and you get the screen back when it ends. A whole "
    "round belongs in a single script: tap the input, type into it, tap the "
    "control that submits it BY ITS OWN LABEL, then wait for the reply text. "
    "Plan it from the screen "
    "above, plus your reading of what you saw. Emit ONLY a JSON object matching "
    "response_schema. Set goal_reached true ONLY when the goal is demonstrably "
    "met on screen; if you need more budget, set request_extension true AND "
    "give extension_reason naming what is still unexplored -- an extension with "
    "no reason is refused."
)


def _packet_base(run_id: str, tc_id: str = "") -> dict:
    return {
        "run_id": str(run_id or ""),
        "tc_id": str(tc_id or ""),
        "system_prompt": _SYSTEM_PROMPT,
        "untrusted_data_notice": _GUARD,
        "vocabulary": actions_mod.describe_vocabulary(),
        "response_schema": actions_mod.response_schema(),
    }


def _screen_block(screen: object) -> str:
    """The pruned screen as a wrapped prompt block. Never the raw XML.

    The accessibility audit rides ALONGSIDE it, in its own wrapper, because the
    two are different claims: the element lines say what is on the screen, and
    the audit says whether a person could use it. It is emitted even when
    `to_prompt_block` returns "" -- a canvas screen has no element lines and is
    exactly the screen whose audit says "no nodes, nothing was audited, look at
    the picture instead".

    The names it quotes are DEVICE text, so it goes through `wrap_untrusted`
    like everything else here. Nothing in this block is a verdict, and the
    summary says so in its own words.
    """
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    block = perception.to_prompt_block(screen)
    note = screen_audit.summary_text(body.get("accessibility"))
    wrapped = wrap_untrusted("accessibility", note, limit=4000) if note else ""
    if block and wrapped:
        return block + "\n" + wrapped
    return block or wrapped


def _case_block(view: object) -> str:
    """The case as compact, wrapped text.

    Wrapped because a case can have come from a Jira ticket: its title and steps
    are as attacker-influenceable as the ticket text, and this block is going
    straight into a prompt.
    """
    body = view if isinstance(view, dict) else {}
    lines = [
        str(body.get("tc_id") or "") + " " + str(body.get("title") or ""),
        "priority "
        + str(body.get("priority") or "?")
        + " | type "
        + str(body.get("type") or "?")
        + " | module "
        + str(body.get("module") or "?"),
    ]
    preconditions = str(body.get("preconditions") or "")
    if preconditions:
        lines.append("preconditions: " + preconditions)
    for step in (body.get("steps") or [])[:MAX_CASE_STEPS]:
        if not isinstance(step, dict):
            continue
        lines.append(
            str(step.get("step_number") or "?")
            + ". "
            + str(step.get("action") or "")
            + (
                " [data: " + str(step.get("test_data")) + "]"
                if str(step.get("test_data") or "")
                else ""
            )
            + " -> expected: "
            + str(step.get("expected_result") or "")
        )
    return wrap_untrusted("test_case", "\n".join(lines), limit=8000)


def _observation_key(screen: object) -> str:
    """The key of THIS LOOK at the screen this packet describes, or ``""``.

    Delegates to ``run_store.observation_id`` -- "THE one producer of this
    string" -- and mints nothing. Three builders need the value, and three copies
    of a hand-rolled ``screen_id + "-" + hash[:12]`` is precisely how the writer
    and the reader end up keying on two different strings.

    NOT the bare ``screen_id``: that is the DEDUP identity and is invariant
    across the turns of a conversation, so it names a screen and never says what
    was on it. A hashless screen degrades to the bare id inside the producer,
    where every existing reader already expects that.

    A ROUTING value, not packet content: ``mcp_handlers._mobile_packet_text``
    reads it to file this turn's PNG under the key the report's frames resolve
    on, and pops it off its own copy before the packet is rendered -- so nothing
    here changes a byte of what the model reads.
    """
    try:
        return run_store.observation_id(screen)
    except Exception:  # pragma: no cover - the store never raises; a packet may not
        logger.warning("mobile_run._observation_key failed", exc_info=True)
        return ""


def build_case_job(
    view: object, screen: object, *, run_id: str, tc_id: str, escapes: int = 0
) -> dict:
    """The first planning packet for one case. Never raises; ``{}`` on failure."""
    try:
        packet = _packet_base(run_id, tc_id)
        packet.update(
            {
                "kind": "case",
                "observation_id": _observation_key(screen),
                "case_block": _case_block(view),
                "screen_block": _screen_block(screen),
                "instruction": _CASE_INSTRUCTION,
                "escapes_used": int(escapes or 0),
                "escapes_left": max(0, 3 - int(escapes or 0)),
                "worker_instructions": (
                    "One JSON object, key `actions`. Every action's `op` must be "
                    "one of the listed ops. Stop the script where the screen "
                    "stops telling you what happens next. " + _BUDGET_NOTE
                ),
            }
        )
        return packet
    except Exception:  # pragma: no cover - defensive
        logger.warning("build_case_job failed", exc_info=True)
        return {}


def _trace_block(trace: object) -> list[dict]:
    """The trace, already redacted by ``executor``, trimmed for a packet."""
    out: list[dict] = []
    for entry in list(trace or [])[:MAX_TRACE_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "index": entry.get("index"),
                "action": actions_mod.redact_action(entry.get("action")),
                "outcome": str(entry.get("outcome") or ""),
                "detail": str(entry.get("detail") or "")[:300],
            }
        )
    return out


def build_escape_job(
    view: object,
    screen: object,
    trace: object,
    *,
    run_id: str,
    tc_id: str,
    escapes: int,
    reason: str = "",
) -> dict:
    """The escape-hatch packet: the trace, the NEW screen, continue from here."""
    try:
        packet = _packet_base(run_id, tc_id)
        packet.update(
            {
                "kind": "escape",
                "observation_id": _observation_key(screen),
                "case_block": _case_block(view),
                "screen_block": _screen_block(screen),
                "trace": _trace_block(trace),
                "stopped_because": str(reason or "")[:600],
                "instruction": _ESCAPE_INSTRUCTION,
                "escapes_used": int(escapes or 0),
                "escapes_left": max(0, 3 - int(escapes or 0)),
                "worker_instructions": (
                    "One JSON object, key `actions`. If this case cannot be "
                    "completed from here, say so with done(verdict='blocked', "
                    "reason=...) rather than retrying the same action. "
                    + _RECOVERY_NOTE
                    + (_LAST_ESCAPE_NOTE if max(0, 3 - int(escapes or 0)) <= 1 else "")
                    + _BUDGET_NOTE
                ),
            }
        )
        return packet
    except Exception:  # pragma: no cover - defensive
        logger.warning("build_escape_job failed", exc_info=True)
        return {}


#: The fail-safe ask for a sentinel no consumer registered. A NAMED constant so
#: the ratchet can assert a registered sentinel does NOT get it: a mutation that
#: bypassed the registry entirely landed every sentinel here, and every wording
#: assertion passed, because this text is deliberately inoffensive. "Not false"
#: is the floor, not the bar -- the specific remedy is the product.
GUARD_ASK_SCREEN_GENERIC = (
    "The replay stopped before a press: this screen cannot be vouched for, so "
    "what that press would submit cannot be judged. Nothing was touched. "
    "Re-sending the same press stops here again -- read the stop's `detail` "
    "for what was seen, and plan a different action."
)


def screen_asks(executor_mod) -> dict:
    """Sentinel -> what the MODEL is told, for every screen-level stop.

    A REGISTRY, matching ``executor.screen_stop_details`` at the other
    consumer. Keyed off the passed-in module rather than a module-level import,
    because the import here is deliberately lazy -- see :func:`_guard_ask`.

    Every sentence must survive the 60-character truncation
    ``build_tester_request`` applies to the TERM, name a move that can actually
    work, and avoid the two claims that are false for any screen stop. The
    ratchet grades this dict against :func:`executor.screen_sentinels`.
    """
    return {
        executor_mod.SCREEN_NOT_THE_APP: (
            "The replay stopped before a press: the screen in front is not the "
            "app under test, so what that press would submit cannot be judged. "
            "Nothing was touched. Re-sending the same press stops here again -- "
            "bring the app back with `launch`, or send `back` if a dialog is in "
            "front of it, and then carry on in the same script. The stop's "
            "`detail` names both packages."
        ),
        executor_mod.SCREEN_NOT_FULLY_SEEN: (
            "The replay stopped before a press: this screen has more elements "
            "than one packet carries, so what that press would submit cannot "
            "be judged. Nothing was touched. Re-sending the same press stops "
            "here again -- plan a TAP on the control this case means instead, "
            "which is judged by its own label. Ask the tester only if you "
            "cannot tell which control that is."
        ),
        executor_mod.SCREEN_CONTROL_NOT_READ: (
            "The replay stopped before acting: the element under that touch was "
            "not read in full -- the app does not expose its text to the "
            "accessibility layer this lane reads, so what the action would "
            "trigger cannot be judged. Nothing was touched. Repeating it stops "
            "here again, and no re-wording of the script recovers a string the "
            "app never exposed -- ask the tester to turn accessibility on for "
            "the app under test, or to use an instrumented build. Until then, "
            "plan a tap on a control the packet does name."
        ),
    }


def _guard_ask(guard_term: str) -> str:
    """What the model is told about a guard stop, per REASON.

    Only ONE of the stops that reach here is about a control -- a matched
    lexicon term. Every SCREEN-LEVEL stop (the ``SCREEN_*`` sentinels the
    executor exports) is about the screen instead, so the fallback's two claims
    are both false for it: there is no control to name, and "re-submit the same
    script if the tester agrees" is deterministically wrong -- the same press
    meets the same screen and stops again, so the only door that wording leaves
    open is turning the guard off for the whole script, including a real
    Confirm button. Each sentinel therefore needs its OWN branch, naming a
    remedy that can actually work.

    NO COUNT IN THIS DOCSTRING, deliberately. It said "two stops" while three
    reached here: ``SCREEN_NOT_THE_APP`` was added at the producer
    (``executor.screen_hit``) and wired into only one of that value's two
    consumers, and this one fell through to the control wording -- telling the
    model a non-existent control had been hit and to re-send a script that
    re-stops. The ratchet that now fails at authoring time instead is
    ``tests/mobile/test_mobile_sentinel_ratchet.py``, which derives the
    sentinel list from the executor rather than listing it.

    Imported lazily: ``executor`` owns the term, this module owns the wording,
    and a module-level import would put an agent's import at the top of a file
    the executor's own callers import.
    """
    from tools.mobile import executor as executor_mod

    said = screen_asks(executor_mod).get(guard_term)
    if said is not None:
        return said
    if not guard_term or executor_mod.destructive_hit(guard_term) != guard_term:
        # A term this module has no words for, and one the lexicon does not
        # match either -- so it is not a control, and the fallback's two
        # claims would both be false. True and unhelpful beats false and
        # actionable. See executor.guard_detail, which asks the same question
        # at the other consumer.
        logger.error("mobile_run: screen sentinel %r has no ask registered", guard_term)
        return GUARD_ASK_SCREEN_GENERIC
    return (
        "The replay stopped in front of a control that looks irreversible ("
        + guard_term
        + "). Nothing was tapped. Ask the tester whether to go ahead, and "
        "re-submit the same script only if they say yes."
    )


def build_tester_request(
    field: str, prompt: str, *, run_id: str, tc_id: str, guard_term: str = ""
) -> dict:
    """Ask the TESTER for one field, or for a go-ahead past the guard.

    Carries the field NAME and the question. It never carries, and has no way to
    carry, a value: the value is supplied on the next submit call and typed
    straight to the device.
    """
    try:
        return {
            "run_id": str(run_id or ""),
            "tc_id": str(tc_id or ""),
            "kind": "tester",
            "field": str(field or "")[:80],
            "prompt": str(prompt or "")[:600],
            "guard_term": str(guard_term or "")[:60],
            "ask_the_tester": (
                _guard_ask(str(guard_term or ""))
                if str(guard_term or "")
                else (
                    "Ask the tester for this one field, in chat, and pass the "
                    "value back in `tester_input` with `tester_input_field` set "
                    "to the field name. It is typed straight into the app and "
                    "is not stored anywhere -- not in the report, not in the "
                    "checkpoint, not in the audit log."
                )
            ),
            "never_do": (
                "Do not invent a value, do not reuse one from an earlier run, "
                "and do not repeat the value back in your own message."
            ),
        }
    except Exception:  # pragma: no cover - defensive
        logger.warning("build_tester_request failed", exc_info=True)
        return {}


def build_explore_turn(
    state: object, screen: object, *, run_id: str, remaining: object = None
) -> dict:
    """One exploratory turn packet."""
    try:
        body = state if isinstance(state, dict) else {}
        left = remaining if isinstance(remaining, dict) else {}
        packet = _packet_base(run_id)
        packet.update(
            {
                "kind": "explore",
                "observation_id": _observation_key(screen),
                "goal": wrap_untrusted("goal", str(body.get("goal") or ""), limit=1200),
                "watch_for": list(body.get("watch_for") or []),
                "turn": int(body.get("turn") or 0),
                "turns_left": int(left.get("turns") or 0),
                "seconds_left": int(left.get("seconds") or 0),
                "extensions_left": max(0, 1 - int(body.get("extensions_used") or 0)),
                "guard_destructive": bool(body.get("guard", True)),
                "screen_block": _screen_block(screen),
                "instruction": _EXPLORE_INSTRUCTION,
                # The turn fields come from `actions.TURN_FIELD_SCHEMA`, the
                # same table `decode_reply` splits on. They were literals here,
                # and v1.79.0 shipped a release in which this packet advertised
                # `finding` while the transport refused it -- one source is
                # what stops the two drifting again.
                "response_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["actions"],
                    "properties": dict(
                        {
                            "actions": actions_mod.response_schema()
                            .get("properties", {})
                            .get("actions", {"type": "array"})
                        },
                        **actions_mod.TURN_FIELD_SCHEMA,
                    ),
                },
                "worker_instructions": (
                    "Spend turns, not actions: one script that finishes a whole "
                    "round costs one turn, and four scripts that each do one "
                    "step of it cost four. "
                    "Record anything a tester would want to know in `finding`, "
                    "one sentence, even when the turn went fine."
                ),
            }
        )
        return packet
    except Exception:  # pragma: no cover - defensive
        logger.warning("build_explore_turn failed", exc_info=True)
        return {}

#: The FIRST packet of an explore run. A packet KIND on the existing return
#: path, deliberately NOT a tool: the test for whether something earns a tool
#: is whether the model could call it out of order and still be right, and
#: intake is only ever first.
CHARTER_INTAKE_KIND = "charter_intake"


def build_charter_intake(
    *, run_id: str = "", package: str = "", charter: object = None
) -> dict:
    """Ask the tester for the run's TERMS -- the server's questions, once.

    The SERVER owns the question set and the typed schema
    (``tools/mobile/charter.py``); the model owns only the wording it puts the
    questions in. A model that improvises the questions gives every run a
    different charter shape, so nothing is resumable and two runs of "the
    same" exploration are not comparable.

    At most ``charter.MAX_QUESTIONS`` questions reach this packet, because
    ``charter.questions_for`` slices to that cap -- the number is not enforced
    here as well, so the two cannot drift.

    *charter* must be the RAW charter the tester sent -- the schema-stripped
    body ``charter.parse`` returns under ``raw`` -- and never the normalised
    terms. Normalisation fills every field, so a normalised empty charter
    yields ONE question instead of five. See ``charter.questions_for``.

    THE CHARTER HAS NO FIELD FOR A SECRET, and that is deliberate rather
    than an omission to correct: the schema carried a ``credentials`` list for
    one revision, nothing consumed it, and its only observable effect was
    writing secret-adjacent free text into a report that lands on disk.
    ``never_do`` says so in the packet. A credential VALUE exists only in the
    tester's own chat turn and on the device's stdin -- see this module's
    header and ``tools/mobile/ime.py``.
    """
    try:
        from tools.mobile import charter as charter_mod

        # THE RAW CHARTER, NOT A NORMALISED ONE. ``normalize`` fills every
        # field, so ``questions_for(normalize({}))`` returns ONE question
        # (``goal``) where ``questions_for({})`` returns FIVE -- measured. This
        # line held the normalised form, so a tester with no charter was asked
        # ONE question and depth, scope, destructive and budget were silently
        # defaulted. Same defect as judging the normalised charter in
        # ``defaults_used`` (M2d), at a SECOND CONSUMER: the function was
        # graded, the WIRING was not. Graded at this seam by M1c /
        # ``test_an_empty_intake_asks_all_five_questions_through_the_builder``.
        asks = charter_mod.questions_for(charter)
        packet = _packet_base(run_id)
        packet.update(
            {
                "kind": CHARTER_INTAKE_KIND,
                "package": str(package or "")[:200],
                "questions": [
                    {
                        "field": str(question["field"]),
                        "ask": str(question["ask"]),
                        "options": list(question["options"]),
                        "default": charter_mod.defaults()[question["field"]],
                    }
                    for question in asks
                ],
                "defaults": charter_mod.defaults(),
                "unasked_fields_take_their_default": [
                    field
                    for field in charter_mod.defaults()
                    if field not in charter_mod.ASKABLE
                ],
                "instruction": (
                    "Put these questions to the TESTER in your own words, in "
                    "one message, and ask no others -- the server owns the "
                    "question set so that two runs of the same exploration "
                    "are comparable. Then call `qa_mobile_test` again with "
                    "`charter` set to a JSON object using these exact field "
                    "names. Anything the tester does not answer takes the "
                    "default shown, and the report NAMES every default that "
                    "was used, so leaving a field out is a recorded choice "
                    "rather than a silent one."
                ),
                "never_do": (
                    "Never put a password, an OTP or any personal VALUE "
                    "in this charter, in any field: the charter is written "
                    "into the run's report on disk, and it has no field for "
                    "a secret. When the app asks for one during the run, the "
                    "packet asks the tester for that field by name and the "
                    "value is typed straight to the device -- it never "
                    "passes through you, a packet, the report or the run "
                    "store."
                ),
                "response_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["goal"],
                    "properties": {
                        "goal": {"type": "string"},
                        "depth": {"enum": list(charter_mod.DEPTHS)},
                        "scope": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "include": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "exclude": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        "destructive": {"enum": list(charter_mod.DESTRUCTIVE)},
                        "budget": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "steps": {"type": "integer"},
                                "minutes": {"type": "integer"},
                            },
                        },
                        "stop_on": {"enum": list(charter_mod.STOP_ON)},
                    },
                },
            }
        )
        return packet
    except Exception:  # pragma: no cover - defensive
        logger.warning("build_charter_intake failed", exc_info=True)
        return {}
