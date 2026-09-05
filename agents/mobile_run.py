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
from tools.mobile import perception
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
    "dialog, the case has not failed: send `back` to dismiss a dialog, or "
    "`launch` to bring the app under test back to the front, and then carry "
    "on with the case. "
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
    "This is one turn of a bounded exploratory session. Return a short script "
    "(a handful of actions) that makes progress toward the goal from the screen "
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
    """The pruned screen as a wrapped prompt block. Never the raw XML."""
    return perception.to_prompt_block(screen)


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


def build_case_job(
    view: object, screen: object, *, run_id: str, tc_id: str, escapes: int = 0
) -> dict:
    """The first planning packet for one case. Never raises; ``{}`` on failure."""
    try:
        packet = _packet_base(run_id, tc_id)
        packet.update(
            {
                "kind": "case",
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


def _guard_ask(guard_term: str) -> str:
    """What the model is told about a guard stop, per REASON.

    Two stops reach here and only one of them is about a control. A screen the
    packet cannot carry in full has no matched control to name, and the
    "re-submit the same script if the tester agrees" remedy is deterministically
    wrong for it: the same press re-truncates the same screen and stops again,
    so the only door that wording leaves open is turning the guard off for the
    whole script -- including a real Confirm button. The remedy that works is a
    TAP, which is judged by the control's own label and not by a screen scan.

    Imported lazily: ``executor`` owns the term, this module owns the wording,
    and a module-level import would put an agent's import at the top of a file
    the executor's own callers import.
    """
    from tools.mobile import executor as executor_mod

    if guard_term == executor_mod.SCREEN_NOT_FULLY_SEEN:
        return (
            "The replay stopped before a press: this screen has more elements "
            "than one packet carries, so what that press would submit cannot "
            "be judged. Nothing was touched. Re-sending the same press stops "
            "here again -- plan a TAP on the control this case means instead, "
            "which is judged by its own label. Ask the tester only if you "
            "cannot tell which control that is."
        )
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
                    "Keep each turn small: the budget is turns, not actions. "
                    "Record anything a tester would want to know in `finding`, "
                    "one sentence, even when the turn went fine."
                ),
            }
        )
        return packet
    except Exception:  # pragma: no cover - defensive
        logger.warning("build_explore_turn failed", exc_info=True)
        return {}
