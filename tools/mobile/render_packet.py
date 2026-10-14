"""The packet block, the static/goal elision and the bounded-call reply.

Seventh layer of the ``render_*`` split of ``render.py``.

``without_static`` -- *packet* with :data:`STATIC_FIELDS` replaced by one note.
Never raises. A COPY: the caller's packet is what the run store and the report
read, and a field removed in place would go missing from both.

The note is added only when something was actually removed, so a packet that
never carried the block -- a tester request carries none of these fields -- is
returned unchanged rather than gaining a note about an omission that did not
happen.

``packet_block`` -- the packet as a fenced JSON block plus the no-echo
instruction. The packet is embedded exactly as ``tools/mobile/run_packets``
built it. Nothing is added to it here -- in particular no screen, no dump and no
path -- so the compactness property is a property of that builder and this
renderer together, and a test can assert it over the rendered reply.

``busy_block`` -- the bounded-call reply: this call stopped ITSELF, and nothing
was lost. A client kills a tool call at around 50 seconds, and a killed call
looks exactly like a broken server -- so the lane answers before the client's
timer does. The wording has one job beyond politeness: to say that no work was
half-done, because a tester who thinks a case ran will not re-run it.

*packet* is the reserved-tail capture ``session._final_packet`` took before this
call gave up -- best-effort, and ``None`` when the device did not answer in
time. When present it is embedded so the model can plan its next call from THIS
reply instead of spending a whole extra round trip just to see the screen it
already asked about.
"""

from __future__ import annotations

import json

from config.settings_guard import never_raise

#: The one place the packet tells the model not to hand the screen back. It is a
#: real cost: a 150-element screen echoed into the next turn doubles the tokens
#: the tester pays for and adds nothing the server does not already hold.
NO_ECHO = (
    "Do NOT echo the screen back, do not summarise it, and do not re-fetch a "
    "packet you already hold. Answer with the JSON the schema asks for and "
    "nothing else."
)


#: The packet fields that are byte-identical on EVERY packet of a run, in every
#: lane. Graded against the lane x ordinal matrix that
#: docs/RETIRED_CAPABILITIES.md section 5 makes binding: case, case-again,
#: escape, explore, explore-again. Worth about 1,835 tokens a turn.
#:
#: THREE FIELDS THAT ARE NOT HERE, and must never be added:
#:
#: * ``response_schema`` -- it has TWO forms. The explore lane's carries
#:   ``goal_reached`` / ``request_extension`` / ``extension_reason`` /
#:   ``finding`` under ``additionalProperties: false``, so a model handed the
#:   scripted lane's copy cannot end its own session. That is the defect the
#:   whole first attempt was reverted for; ``test_mobile_schema_always`` is the
#:   pin.
#: * ``instruction`` and ``worker_instructions`` -- three forms each, and they
#:   are what tell the model how big a script to send. About 250 tokens to keep
#:   and a behaviour regression to drop.
STATIC_FIELDS: tuple[str, ...] = (
    "system_prompt",
    "vocabulary",
    "untrusted_data_notice",
)

#: What stands in their place. It names the RECOVERY, because a model that has
#: lost the block and is told only "it was sent earlier" has been handed a dead
#: end -- see ``run_store.clear_briefing``.
STATIC_OMITTED_NOTE = (
    "The system prompt, the action vocabulary and the untrusted-data notice "
    "went out in full with the FIRST packet of this run and have not changed "
    "since. Read them there rather than asking for them again. If they are no "
    "longer in front of you, send your best attempt anyway: a script this "
    "server cannot parse is answered with the whole block again, so this is "
    "never a dead end. The response schema below is always current -- plan "
    "against it and not against a remembered one."
)


def without_static(packet: object) -> dict:
    """*packet* with :data:`STATIC_FIELDS` replaced by one note. Never raises."""
    body = packet if isinstance(packet, dict) else {}
    if not any(k in body for k in STATIC_FIELDS):
        return dict(body)
    out = {k: v for k, v in body.items() if k not in STATIC_FIELDS}
    out["static_block"] = STATIC_OMITTED_NOTE
    return out


#: Fix round 3, item 1. The Air run resent the ORIGINAL goal ("Open Firebase
#: App Tester...") on every packet, and the model kept re-planning from its
#: first step. The goal goes to a chat ONCE per run, on the same memo as the
#: static block, and ``sub_goal`` rides every packet instead.
#:
#: Its OWN list and its own note, never a :data:`STATIC_FIELDS` entry: a case
#: packet carries no goal, so the lane x ordinal matrix in
#: ``test_mobile_static_block_once`` would reject it there.
GOAL_FIELDS: tuple[str, ...] = ("goal",)

GOAL_OMITTED_NOTE = (
    "The run's full goal went out with the FIRST packet of this run in this "
    "chat and has not changed. `sub_goal` is the step you are on now: plan "
    "the next step from it and the screen. If the goal is no longer in front "
    "of you, send your best attempt anyway -- a script this server cannot "
    "parse is answered with the goal and the whole block again."
)


def without_goal(packet: object) -> dict:
    """*packet* with :data:`GOAL_FIELDS` replaced by one note. Never raises.

    A COPY, for the reason :func:`without_static` gives. Only an EXPLORE
    packet is touched; any other kind comes back unchanged.
    """
    body = packet if isinstance(packet, dict) else {}
    if str(body.get("kind") or "") != "explore":
        return dict(body)
    if not any(k in body for k in GOAL_FIELDS):
        return dict(body)
    out = {k: v for k, v in body.items() if k not in GOAL_FIELDS}
    out["goal_note"] = GOAL_OMITTED_NOTE
    return out


def packet_block(packet: object, *, session_token: str = "") -> str:
    """The packet as a fenced JSON block plus the no-echo instruction."""
    body = packet if isinstance(packet, dict) else {}
    payload = dict(body)
    if session_token:
        payload["session_token"] = str(session_token)
    with never_raise() as g:
        # COMPACT, not indented. The packet is machine input: `json.loads` on
        # the other side is indifferent to whitespace, and indent=2 charged
        # every turn of every run for it. `sort_keys` stays -- a stable field
        # order is what lets two packets be diffed by eye and by test.
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    if g.failed:
        text = "{}"
    return "```json\n" + text + "\n```\n\n" + NO_ECHO


def busy_block(run_id: str, tc_id: str = "", *, packet: object = None) -> str:
    """The bounded-call reply: this call stopped ITSELF, nothing was lost."""
    run = str(run_id)[:64]
    packet_text = packet_block(packet) if isinstance(packet, dict) and packet else ""
    return (
        "## Still working on run `"
        + run
        + "`\n\nThis call stopped short of its own time budget so your editor "
        "would not time it out. There is no step in this reply and nothing was "
        "half-done"
        + (" \u2014 `" + str(tc_id)[:16] + "` has not started yet" if tc_id else "")
        + ".\n\n"
        + packet_text
        + "- `qa_mobile_status` shows where the run stands.\n"
        '- Call `qa_mobile_test` again with `run_id="'
        + run
        + '"` to carry on from the same place.'
    )
