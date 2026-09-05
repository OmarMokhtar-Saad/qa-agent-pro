"""The bounded action vocabulary a model may return, and how it is resolved.

Why a vocabulary at all: the tester's own chat model plans a case as ONE script
from the first screen, and the server replays it. A model that could emit
arbitrary shell would be a remote-code-execution surface reachable from a Jira
ticket. So the script is a closed set of twelve operations validated by pydantic
with ``extra="forbid"`` -- an unknown key is a refusal, not a warning -- and
every one of them lands on an ``adb`` helper that validates its own arguments
again.

Target resolution order is ``rid`` > exact text/desc > ``contains`` > ``id``,
and that order is a fix rather than a preference. It used to put ``id`` FIRST,
while ids were row numbers in the current dump and the executor re-dumps after
every mutating op -- so a ``type`` that made a chat app grow a send control
shifted every later row by one and the next tap landed on the neighbouring
widget with ``how="id"`` and ``candidates=1``: a confident, unique, wrong
answer. ``perception.element_id`` now derives an id from the element's own
content, which makes the shift unrepresentable. The ORDERING is not a second
guard on top of that, and calling it one would overstate it: ``how`` has no
production reader, and a target carrying only a ``rid`` resolves to the same
element under either order. What the order buys is that the packet, the prompt
and the resolver all recommend the selectors the APP chose -- ``rid`` and the
on-screen text -- so a model that follows the guidance is not relying on an id
at all. The safety comes from content-derived ids and from the refusals below.

A MISS IS NOT A FAILURE: it is the boomerang point. The model planned from
screen N and the app is on screen N+1; the honest answer is to hand the new
screen back and ask for the rest of the script, not to fail the tester's case.

**A target's selectors are cross-checked against EACH OTHER, all of them.** A
plan whose selectors name different elements, or one of whose selectors matches
nothing while another matches, is a plan built on an older screen, and this
module refuses to pick one of them. The first version of that check covered
``id`` only, so ``{"rid": ..., "text": ...}`` naming two different elements
tapped one of them and reported a unique hit -- the same defect as the row
numbers, one selector pair over. The check is now driven by
:func:`candidates_for` and pinned by a property test over
``Target.model_fields``, so it cannot miss the next pair either.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)

MAX_ACTIONS = 40
MAX_WAIT_MS = 10000

#: The wall clock ONE submit may spend replaying, in ms. A client kills a tool
#: call at around 60 s: measured on 2026-09-04, where `qa_submit_mobile_step`
#: returned "ok in 60040 ms" to a client that had already given up, and the
#: model started a fresh run rather than resuming. 40 s leaves the reply, the
#: checkpoint write and the packet render inside the window.
#:
#: It lives HERE, next to the vocabulary, and not in ``executor``, because the
#: packet quotes it to the model and ``agents/mobile_run`` may not import the
#: executor. ``executor.SUBMIT_BUDGET_S`` is derived from this one value.
SUBMIT_BUDGET_MS = 40000

#: The SUM of every wait in one script. A single wait is already capped at
#: :data:`MAX_WAIT_MS`, but nothing capped the total, and two 5 s waits plus two
#: types and two asserts took a submit past the client's kill. Refusing costs
#: the tester nothing: a refused script is not an escape, so the model resends a
#: shorter one against the same screen.
MAX_TOTAL_WAIT_MS = 25000

#: What a ``wait`` carrying only ``until_text`` counts as, for the total above:
#: the bound the executor actually applies to it. Mirrored rather than imported
#: -- ``actions`` must not import ``executor`` -- and pinned equal to
#: ``executor.DEFAULT_WAIT_UNTIL_TEXT_S`` by a test, so the two cannot drift.
UNTIL_TEXT_DEFAULT_MS = 20000
MAX_TEXT_CHARS = 4000
MAX_ERROR_CHARS = 600
SECRET_MASK = "***"

#: Words that make a field a CREDENTIAL by name, whatever the model marked.
#:
#: The list is enumerated rather than described because a vague rule is not an
#: implementable one. It is checked as WHOLE WORDS -- camel-case split first --
#: against the action's ``field`` and its target's ``rid``, ``text`` and ``id``,
#: so ``com.x:id/pass`` matches ``pass`` and ``Compass`` matches nothing.
#:
#: Deliberately broad in the SAFE direction: a promo ``code`` field is masked in
#: the report, which costs a tester one detail, and a leaked one-time code costs
#: more. It exists because a model that forgets ``secret: true`` must not be
#: able to put a credential into a checkpoint, an audit line or a page.
CREDENTIAL_TERMS: frozenset = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "passcode",
        "pin",
        "otp",
        "code",
        "token",
        "secret",
        "credential",
        "credentials",
        "cvv",
        "ssn",
        "apikey",
        "auth",
        "login",
        # Added 2026-09-04 from a review that executed the code: `passphrase`
        # is ONE token and so was never covered by `pass`.
        "passphrase",
        "phrase",
        "seed",
        "mnemonic",
        "security",
        "recovery",
    }
)

SCROLL_DIRECTIONS = ("up", "down", "left", "right")
ASSERT_KINDS = (
    "text_present",
    "text_absent",
    "element",
    "new_text",
    "screen_changed",
)
VERDICTS = ("pass", "fail", "blocked")
URL_SCHEMES = ("https", "http", "market")

# Which operations change the device, and therefore force a re-dump. ``wait`` is
# in here because a wait exists precisely to let the screen change.
#
# ``press`` is in here for the same reason it exists: the keyboard's action key
# SUBMITS the focused field, so the screen after it is a different screen.
# ``executor.ACTUATING_OPS`` is derived from this set minus ``wait``, so a press
# also counts as having touched the device for the escape budget -- correct,
# because it taps to focus and then sends a key.
MUTATING_OPS = frozenset(
    {
        "tap",
        "type",
        "clear",
        "back",
        "home",
        "scroll",
        "launch",
        "open_url",
        "wait",
        "press",
    }
)

#: The keys ``press`` may send, and the ONLY ones -- name -> Android keycode.
#:
#: Deliberately NOT ``adb.KEYEVENT_RE``. That regex is the right bound for
#: ``adb.keyevent``, whose callers are this package's own code, but it admits
#: every ``KEYCODE_*`` name and every bare numeric code 0-999 -- ``KEYCODE_POWER``,
#: the volume and call keys, three-digit codes nobody enumerated. A model-facing
#: vocabulary cannot inherit that surface: the model chooses the value, and
#: "anything the device understands" is not a bound. So ``press`` carries its
#: own allowlist and ``adb`` validates again behind it.
#:
#: One key on purpose. ``enter`` IS the IME action on a single-line field.
#: Each addition is one line here plus one behavioural test.
PRESS_KEYS: dict[str, str] = {"enter": "KEYCODE_ENTER"}


class Target(BaseModel):
    """Which element an action acts on. At least one selector is required.

    ``id`` IS NOT STABLE, and that is not a detail. ``perception`` numbers
    elements positionally on every dump, and the executor re-dumps after every
    screen-changing action -- so an ``id`` planned from screen N means something
    else on screen N+1. Both blocked runs of 2026-09-04 died on exactly that:
    ``[clear e14, type e14, wait, tap e17]`` reached ``missing_element``,
    because typing into the composer changed the element list.

    ``role`` and ``label`` are computed by this server from the element's own
    content, so they survive a renumber. For any action that follows another
    action, they are the right selector.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=16)
    text: str = Field(default="", max_length=200)
    rid: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=32)
    label: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def at_least_one_selector(self) -> "Target":
        if not (
            self.id.strip()
            or self.text.strip()
            or self.rid.strip()
            or self.role.strip()
            or self.label.strip()
        ):
            raise ValueError("a target needs one of id, rid, role, label or text")
        return self


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TapAction(_Base):
    op: Literal["tap"]
    target: Target


class TypeAction(_Base):
    """Type into a field.

    A SECRET value is never carried here. ``secret=true`` requires ``field`` and
    forbids ``text``: the literal lives only in the tester chat turn that
    supplied it, and the executor looks it up by field name at replay time. A
    script that puts a credential in ``text`` is REFUSED BY NAME, because a
    model that can write one into a plan can also write it into a report.
    """

    op: Literal["type"]
    target: Target
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    secret: bool = False
    field: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def secret_comes_from_the_tester(self) -> "TypeAction":
        if self.secret:
            # The literal is refused FIRST, and unconditionally. Checking for a
            # missing `field` first meant a script carrying a credential AND no
            # field got the weaker "needs field" message, burying the finding
            # that matters -- that a secret was written into a plan at all.
            if self.text:
                raise ValueError(
                    "a secret value must never appear in a script; use field "
                    "and let the tester supply it"
                )
            if not self.field.strip():
                raise ValueError(
                    "a secret type action needs field (the name the tester was "
                    "asked for) and must leave text empty"
                )
        elif not self.text.strip():
            raise ValueError("type needs text")
        return self


class ClearAction(_Base):
    op: Literal["clear"]
    target: Target


class BackAction(_Base):
    op: Literal["back"]


class HomeAction(_Base):
    op: Literal["home"]


# ``PressAction``'s docstring ships to the model inside ``response_schema()``
# on every turn, so it carries the CONTRACT only. The reasoning lives here.
#
# ``target`` is the FIELD, and it is required so the key lands where the model
# meant: the executor taps it to focus it first, the same two steps ``type``
# and ``clear`` take, and that focus tap is judged by the destructive guard
# exactly like any tap -- named element AND the control under the finger.
#
# What the key then does is the APP's decision, not the field's. ``enter``
# delivers the form's IME action (send / go / done), and no dump names the
# control that action fires -- uiautomator emits no ``imeOptions``. So the
# guard judges a press against every element the packet carries (and refuses
# on a packet that does not carry the whole screen): on a screen holding an
# irreversible control the press is handed to the tester, and the model is told
# to tap the control it means instead, which IS guarded by name. A press was
# shipped once with "requiring the field is what makes the guard see it";
# measured, `press enter {"rid": amount}` sent KEYCODE_ENTER on a form whose
# button `tap` refused. The field's label was never the node the key actuated.


class PressAction(_Base):
    """Press a keyboard key against the named FIELD: tap it to focus, then
    send ``key``. ``enter`` is the keyboard's action key; what it submits is
    the app's decision, so a press is judged against every control on the
    screen and handed to the tester when any looks irreversible."""

    op: Literal["press"]
    key: Literal["enter"]
    target: Target


class ScrollAction(_Base):
    op: Literal["scroll"]
    dir: Literal["up", "down", "left", "right"]
    target: Optional[Target] = None


class WaitAction(_Base):
    op: Literal["wait"]
    ms: int = Field(default=0, ge=0, le=MAX_WAIT_MS)
    until_text: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def something_to_wait_for(self) -> "WaitAction":
        if not self.ms and not self.until_text.strip():
            raise ValueError("wait needs ms or until_text")
        return self


class LaunchAction(_Base):
    op: Literal["launch"]


class OpenUrlAction(_Base):
    op: Literal["open_url"]
    url: str = Field(max_length=2000)

    @model_validator(mode="after")
    def allowed_scheme(self) -> "OpenUrlAction":
        scheme = self.url.split(":", 1)[0].strip().lower()
        if scheme not in URL_SCHEMES:
            raise ValueError("url scheme must be one of " + ", ".join(URL_SCHEMES))
        return self


class AssertAction(_Base):
    """Check something about the screen. Five kinds, and one of them is weak.

    ``new_text`` is the kind to reach for when a case says "the app replies":
    it passes only when text is on the screen that was NOT on the previous one,
    which is what an answer arriving looks like. ``contains`` narrows it to a
    reply mentioning a particular string.

    ``screen_changed`` is kept and is WEAK on purpose: it compares screen IDs,
    so ANY navigation satisfies it and it is never evidence that an answer
    arrived. The 2026-09-04 live run asserted it after each send and read the
    result as "the assistant replied"; it was not.
    """

    op: Literal["assert"]
    kind: Literal[
        "text_present", "text_absent", "element", "new_text", "screen_changed"
    ]
    text: str = Field(default="", max_length=200)
    contains: str = Field(default="", max_length=200)
    target: Optional[Target] = None

    @model_validator(mode="after")
    def kind_has_its_operand(self) -> "AssertAction":
        if self.kind in ("text_present", "text_absent") and not self.text.strip():
            raise ValueError(self.kind + " needs text")
        if self.kind == "element" and self.target is None:
            raise ValueError("assert element needs a target")
        return self


# Field names that would carry a VALUE rather than a request for one. Checked
# against ``AskTesterAction``'s own field set at validation time -- see
# ``carries_no_value_field``.
_VALUE_FIELD_NAMES = frozenset(
    {"text", "value", "password", "secret_value", "input", "tester_input"}
)


class AskTesterAction(_Base):
    """Ask the tester for one field's value. The value is NEVER stored.

    This action is a REQUEST, and it must stay one. ``extra="forbid"`` already
    stops a model sending an unexpected key, but it says nothing about a future
    EDIT to this class adding a value-bearing field -- which is exactly the
    mistake ``TypeAction`` was written to prevent, and the reason it enforces
    forbid-text/require-field rather than trusting convention.

    So the invariant is enforced rather than conventional:
    ``carries_no_value_field`` inspects this class's OWN field set on every
    validation, and adding ``text`` here would make every ``ask_tester`` script
    fail loudly and immediately instead of quietly opening a channel for a
    credential to travel through a packet.
    """

    op: Literal["ask_tester"]
    prompt: str = Field(min_length=3, max_length=300)
    field: str = Field(min_length=1, max_length=80)
    secret: bool = True

    @model_validator(mode="after")
    def carries_no_value_field(self) -> "AskTesterAction":
        leaked = sorted(_VALUE_FIELD_NAMES & set(type(self).model_fields))
        if leaked:
            raise ValueError(
                "ask_tester asks for a value and must never carry one; this "
                "class has grown the field(s) " + ", ".join(leaked)
            )
        return self


class DoneAction(_Base):
    op: Literal["done"]
    verdict: Literal["pass", "fail", "blocked"]
    reason: str = Field(min_length=3, max_length=600)


Action = Annotated[
    Union[
        TapAction,
        TypeAction,
        ClearAction,
        BackAction,
        HomeAction,
        PressAction,
        ScrollAction,
        WaitAction,
        LaunchAction,
        OpenUrlAction,
        AssertAction,
        AskTesterAction,
        DoneAction,
    ],
    Field(discriminator="op"),
]


def total_wait_ms(actions: object) -> int:
    """Device time every ``wait`` in *actions* may spend, in ms.

    A ``wait`` carrying only ``until_text`` polls until the text appears, so its
    worst case is :data:`UNTIL_TEXT_DEFAULT_MS` rather than zero. Counting it as
    zero is how a two-wait script still blew the submit budget.
    """
    total = 0
    for action in list(actions or []):
        if str(getattr(action, "op", "") or "") != "wait":
            continue
        ms = int(getattr(action, "ms", 0) or 0)
        if not ms and str(getattr(action, "until_text", "") or "").strip():
            ms = UNTIL_TEXT_DEFAULT_MS
        total += ms
    return total


class Script(BaseModel):
    """One case's bounded plan."""

    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS)

    @model_validator(mode="after")
    def waits_fit_one_submit(self) -> "Script":
        total = total_wait_ms(self.actions)
        if total > MAX_TOTAL_WAIT_MS:
            raise ValueError(
                "the waits in this script total "
                + str(total)
                + " ms, over the "
                + str(MAX_TOTAL_WAIT_MS)
                + " ms one submit allows; split the case into shorter scripts "
                "-- the server hands the screen back between them"
            )
        return self


OPS = (
    "tap",
    "type",
    "clear",
    "back",
    "home",
    "press",
    "scroll",
    "wait",
    "launch",
    "open_url",
    "assert",
    "ask_tester",
    "done",
)


#: The turn fields the EXPLORE packet advertises alongside ``actions``, WITH
#: their schema -- this table is the single source, and ``mobile_run`` builds
#: the packet's ``response_schema`` properties from it.
#:
#: They are not part of :class:`Script` (``extra="forbid"``, on purpose, so an
#: invented op cannot ride in on a typo), which is exactly why the reply has to
#: be split before it reaches ``parse_script`` rather than after.
#:
#: Single-sourced because the alternative was measured: v1.79.0 advertised
#: ``finding`` in one place and refused it in another for a whole release. A
#: field added here is advertised AND split; a field added to the packet alone
#: fails the schema test rather than reaching a tester as a refusal.
TURN_FIELD_SCHEMA: dict = {
    "finding": {"type": "string", "maxLength": 600},
    "goal_reached": {"type": "boolean"},
    "request_extension": {"type": "boolean"},
    "extension_reason": {"type": "string", "maxLength": 400},
}

#: Just the names, for callers that only need to know what rides beside the
#: actions.
TURN_FIELDS: tuple[str, ...] = tuple(TURN_FIELD_SCHEMA)


def decode_reply(raw: object) -> dict:
    """A model's reply, decoded ONCE into a dict at the transport boundary.

    The MCP tool declares ``script: str``, so on every real client path this
    function receives TEXT, and a caller that tested ``isinstance(raw, dict)``
    saw ``{}`` -- which is how the explore lane advertised ``finding`` in its
    ``response_schema``, asked for one every turn in ``worker_instructions``,
    and then refused it at the transport (v1.79.0, found on a live run; every
    test passed a dict, so nothing caught it).

    Accepts the same three shapes ``parse_script`` promises -- ``{"actions":
    [...]}``, a bare list, or a JSON string of either -- and always returns a
    dict with an ``actions`` key. Undecodable input is passed THROUGH under
    ``actions`` rather than rejected here, so the caller still gets
    ``parse_script``'s own wording rather than a second, competing error.
    """
    try:
        payload = raw
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {"actions": raw}
            try:
                payload = json.loads(text)
            except ValueError:
                return {"actions": raw}
        if isinstance(payload, list):
            return {"actions": payload}
        if isinstance(payload, dict):
            out = dict(payload)
            out.setdefault("actions", [])
            return out
        return {"actions": raw}
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.actions.decode_reply failed")
        return {"actions": raw}


def parse_script(raw: object) -> dict:
    """A model's reply -> a validated :class:`Script`. Never raises.

    Accepts the packet shape (``{"actions": [...]}``), a bare list, or a JSON
    string of either -- a chat model reliably produces all three and refusing
    two of them would boomerang a correct plan.
    """
    try:
        payload = raw
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {"error": "The script was empty.", "content": None}
            try:
                payload = json.loads(text)
            except ValueError:
                return {
                    "error": "The script was not valid JSON and was not replayed.",
                    "content": None,
                }
        if isinstance(payload, list):
            payload = {"actions": payload}
        if not isinstance(payload, dict):
            return {
                "error": "The script must be a JSON object with an `actions` list.",
                "content": None,
            }
        script = Script.model_validate(payload)
        return {"error": None, "content": script}
    except ValidationError as exc:
        problems = []
        for problem in exc.errors()[:5]:
            where = ".".join(str(part) for part in problem.get("loc") or ())
            problems.append((where or "actions") + ": " + str(problem.get("msg") or ""))
        return {
            "error": (
                "This script was refused and nothing was replayed. "
                + "; ".join(problems)
            )[:MAX_ERROR_CHARS],
            "content": None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.actions.parse_script failed")
        return {"error": str(exc), "content": None}


#: The ops whose action can hold a value a tester typed. Everything else is
#: read-only as far as a credential is concerned, so the mask has no business
#: touching it.
VALUE_BEARING_OPS: frozenset = frozenset({"type", "ask_tester"})


def is_credential_action(action: object) -> bool:
    """True when this action's own names say it carries a credential.

    Independent of the ``secret`` marker on purpose: the marker is the model's
    claim, and this is the server's own reading of the field it typed into.

    The tokeniser is ``perception``'s, so this package asks the question ONE
    way -- the destructive lexicon, the role vocabulary and this mask all split
    camel case and match whole words with the same code. ``perception`` imports
    nothing from here, so the direction is safe.
    """
    from tools.mobile import perception

    if isinstance(action, BaseModel):
        try:
            payload = action.model_dump(mode="json")
        except Exception:  # pragma: no cover - defensive
            return True
    elif isinstance(action, dict):
        payload = action
    else:
        return False
    # ONLY AN OP THAT CARRIES A VALUE. An `assert` cannot hold a typed literal,
    # so flagging one by its target's label masked its expected text for
    # nothing -- and `until_text`, on an action the same rule flagged, stayed
    # clear anyway. Inconsistent rather than unsafe, and the scope is the fix.
    if str(payload.get("op") or "") not in VALUE_BEARING_OPS:
        return False
    target = payload.get("target")
    target = target if isinstance(target, dict) else {}
    surface = [
        payload.get("field"),
        target.get("rid"),
        target.get("text"),
        target.get("id"),
        target.get("label"),
    ]
    # THE WHOLE SURFACE, and this reverses a narrowing that lasted one round.
    #
    # It was narrowed to `field` alone because applying it to a target's label
    # masked the tester's own Arabic QUESTION in the report that exists to show
    # it. That is a real cost and it is still real. But the narrowing reopened
    # the leak for every credential field whose dump does not carry
    # `password="true"` -- a PIN, an OTP, a custom view -- and a reviewer
    # confirmed a password rendered in clear in report.html under an Arabic
    # label. Between hiding a question and showing a credential, the credential
    # wins: masking costs a tester one line of evidence, and it is avoidable by
    # targeting with `rid`, a short id or a `role`, which the packet asks for
    # anyway.
    #
    # The element-level control added alongside it stays -- a plain `type` into
    # a password input is still refused -- because it catches the case where
    # the NAME says nothing at all. Neither is sufficient alone.
    if perception.has_unreadable_text(*surface):
        return True
    # FAIL CLOSED ON A NAME THIS SERVER CANNOT READ. `words` tokenises ASCII,
    # so a wholly non-Latin field name yields NO tokens and could never match
    # this list -- by construction, for every language. A reviewer confirmed a
    # value under an Arabic field name reaching report.html in clear, on the
    # lane whose validated app is an Arabic one.
    #
    # The invariant, stated because it recurs: a lexicon of names is not a
    # security boundary. "Matched nothing in my list" and "is not in an
    # alphabet I can read" are different answers, and only the first clears a
    # value for printing.
    return bool(set(perception.words(*surface)) & CREDENTIAL_TERMS)


def action_text(action: object) -> str:
    """The on-screen label an action is aiming at, for the destructive guard."""
    target = getattr(action, "target", None)
    parts = []
    if target is not None:
        parts.append(str(getattr(target, "text", "") or ""))
        parts.append(str(getattr(target, "rid", "") or ""))
    return " ".join(part for part in parts if part).strip()


def redact_action(action: object) -> dict:
    """An action as a dict, with any secret value replaced by ``***``.

    Defence in depth: ``run_store._write_json`` redacts on the way to disk, but
    the trace is also returned to the CALLER, rendered into the report and
    handed to ``audit_log``, none of which goes through the store.
    """
    try:
        if isinstance(action, BaseModel):
            payload = action.model_dump(mode="json")
        elif isinstance(action, dict):
            payload = dict(action)
        else:
            return {"op": str(action)[:40]}
        # TWO reasons to mask, and the second is the one that matters now that
        # the report renders a typed literal: the model's own `secret` marker,
        # and this server's reading of what the field is CALLED. A model that
        # forgets the marker used to put the value straight through.
        if payload.get("secret") or is_credential_action(payload):
            if "text" in payload:
                payload["text"] = SECRET_MASK
            if "value" in payload:
                payload["value"] = SECRET_MASK
        return payload
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.actions.redact_action failed")
        return {"op": "unknown"}


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip().lower()


#: The selector fields a ``Target`` may carry, in the PREFERENCE order the
#: resolver reports. THEIR order, kept verbatim: ``id`` > ``rid`` > ``role`` >
#: ``label`` > ``text``. Preference decides which element is acted on when
#: several agree, and which selector is named in ``how``; it never decides
#: whether the others are consulted -- see :func:`resolve_target`.
#:
#: A property test enumerates ``Target.model_fields`` against this tuple, so a
#: selector added to the vocabulary without a resolver fails immediately rather
#: than silently skipping the agreement check. That is what forced this tuple to
#: grow from three to five when the two branches met.
SELECTORS: tuple[str, ...] = ("id", "rid", "role", "label", "text")

#: The selectors this SERVER mints, as opposed to the ones the app or the plan
#: chose. ``perception.element_id`` derives an ``id`` from the element's own
#: content, so an id that no longer resolves is OUR bookkeeping going stale --
#: after a ``type`` the keyboard opens and every id moves at once -- and
#: ``case_runner``'s uncharged-stop budget declines to charge the case for it.
#: A ``rid``, a ``role``, a ``label`` or a text that matches nothing is the
#: PLANNER naming something absent, and that is charged like any other
#: boomerang. The exemption is narrow on purpose: an uncharged stop for any
#: miss would be a free retry.
#:
#: ``role`` and ``label`` are computed by this server too, but from content that
#: is ALREADY in the element -- they do not go stale on a re-layout the way a
#: bounds-derived id does, which is the whole reason they exist. They are the
#: planner's to get right, so they are not in here.
OURS: frozenset[str] = frozenset({"id"})


def _selector_values(target: object) -> dict:
    """The selectors this target actually carries, normalised."""
    out: dict = {}
    for name in SELECTORS:
        raw = (
            target.get(name) if isinstance(target, dict) else getattr(target, name, "")
        )
        value = _norm(raw)
        if value:
            out[name] = value
    return out


def _rid_candidates(elements: list, want: str) -> list:
    exact = [e for e in elements if _norm(e.get("rid")) == want]
    if exact:
        return exact
    # A rid given without its package prefix is a documented convenience.
    return [e for e in elements if _norm(e.get("rid")).endswith("/" + want)]


def _labelled_candidates(elements: list, key: str, want: str, contains: str) -> tuple:
    """Exact before contains, for one string selector. Their tiering."""
    exact = [e for e in elements if _norm(e.get(key)) == want]
    if exact:
        return key, exact
    return contains, [e for e in elements if want in _norm(e.get(key))]


def _text_candidates(elements: list, want: str) -> tuple:
    exact = [
        e
        for e in elements
        if _norm(e.get("text")) == want or _norm(e.get("desc")) == want
    ]
    if exact:
        return "text", exact
    return "contains", [
        e
        for e in elements
        if want in _norm(e.get("text")) or want in _norm(e.get("desc"))
    ]


def candidates_for(target: object, pruned: object) -> dict:
    """``{selector: (how, [every element that selector ALONE matches])}``.

    Public because :func:`resolve_target` is implemented on top of it and a
    property test enumerates ``Target.model_fields`` against it. That is the
    mechanical half of the agreement rule: the rule iterates whatever this
    returns, so it cannot forget a selector the way an id-only check did -- and
    when ``role`` and ``label`` arrived from another branch, the test named them
    rather than letting them through unchecked.
    """
    screen = pruned if isinstance(pruned, dict) else {}
    if isinstance(screen.get("content"), dict):
        screen = screen["content"]
    # A new PUBLIC entry point, and the property test calls it directly, so it
    # takes whatever a caller sends: 16 of 112 junk screens raised TypeError
    # here before this line.
    raw = screen.get("elements")
    elements = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    out: dict = {}
    for name, want in _selector_values(target).items():
        if name == "rid":
            out[name] = ("rid", _rid_candidates(elements, want))
        elif name == "role":
            out[name] = ("role", [e for e in elements if _norm(e.get("role")) == want])
        elif name == "label":
            out[name] = _labelled_candidates(elements, "label", want, "label_contains")
        elif name == "text":
            out[name] = _text_candidates(elements, want)
        else:
            out[name] = ("id", [e for e in elements if _norm(e.get("id")) == want])
    return out


def resolve_target(target: object, pruned: object) -> dict:
    """Find *target* on the pruned screen. A miss is content, not an error.

    ``{"error": None, "content": {"element", "how", "candidates", "stale",
    "conflict", "supplied", "stale_selectors"}}``. ``how`` is ``""`` on a miss
    so a caller can branch on a constant rather than on the absence of a key.

    **An element is returned only when it is among the matches of EVERY
    selector the target supplied.** Preference order (:data:`SELECTORS`)
    decides which element is acted on among those that agree, and which
    selector ``how`` names; it never decides whether the others are consulted.
    Outcomes:

    * every supplied selector matched and they agree -> a HIT. The narrowest
      selector picks the element, and a CLICKABLE candidate is preferred, so a
      role resolves the tappable wrapper rather than the child that carries the
      word.
    * a ``role`` that still matches more than one TAPPABLE control after every
      other selector has narrowed it -> a miss carrying that count. Ambiguity is
      not a coin toss: picking the first in dump order is how "Send voice
      message" was tapped instead of Send. Checked AFTER the intersection so a
      target carrying its own tie-break -- ``{"role": "input", "label":
      "Message"}`` -- is still answered.
    * a supplied selector matched NOTHING while another matched something ->
      ``stale``: the plan was built on a screen that no longer exists.
    * two supplied selectors both matched, but no element satisfies both ->
      ``conflict``.
    * nothing matched at all -> ``stale`` when one of :data:`OURS` was supplied
      and missed, otherwise a plain miss. ``case_runner`` charges those two
      differently.
    """
    try:
        found = candidates_for(target, pruned)
        if not found:
            return {"error": None, "content": _miss()}
        supplied = tuple(name for name in SELECTORS if name in found)
        matched = {name: group for name, (_how, group) in found.items() if group}
        missed = tuple(name for name in supplied if name not in matched)
        if not matched:
            ours_missed = tuple(name for name in missed if name in OURS)
            return {
                "error": None,
                "content": _miss(
                    stale=bool(ours_missed),
                    supplied=supplied,
                    stale_selectors=missed,
                ),
            }
        if missed:
            return {
                "error": None,
                "content": _miss(stale=True, supplied=supplied, stale_selectors=missed),
            }

        # Elements satisfying EVERY supplied selector. Compared by the ASSIGNED
        # id (see `_identity`): the content seed omits `clickable`, so a
        # clickable wrapper and its non-clickable child are one bucket to it,
        # and a target could be answered by an element only ONE of its
        # selectors matched.
        agreed_ids: set | None = None
        for group in matched.values():
            group_ids = {_identity(element) for element in group}
            agreed_ids = group_ids if agreed_ids is None else (agreed_ids & group_ids)
        if not agreed_ids:
            return {
                "error": None,
                "content": _miss(conflict=True, supplied=supplied),
            }

        order = {name: index for index, name in enumerate(SELECTORS)}
        narrowest = min(matched, key=lambda name: (len(matched[name]), order[name]))
        agreed = [e for e in matched[narrowest] if _identity(e) in agreed_ids]
        # THEIR clickable preference: the tap target is the wrapper, and the
        # element carrying the word is often its non-clickable child.
        tappable = [e for e in agreed if e.get("clickable")]

        # THEIR ambiguity rule, applied to what SURVIVED the cross-check rather
        # than to the role's own matches -- which is their own "ambiguity falls
        # through" intent, generalised: any other selector may narrow it, and
        # only a role still ambiguous at the end is a miss.
        if "role" in matched and len(tappable) > 1:
            return {
                "error": None,
                "content": _miss(supplied=supplied, candidates=len(tappable)),
            }

        chosen = (tappable or agreed)[0]
        preferred = min(matched, key=lambda name: order[name])
        return {
            "error": None,
            "content": _hit(chosen, found[preferred][0], len(matched[preferred])),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.actions.resolve_target failed")
        return {"error": str(exc), "content": None}


def _identity(element: object) -> str:
    """One element's identity WITHIN one dump, for the cross-selector check.

    A CONTENT comparison rather than ``is``: a screen that has round-tripped
    through the run store's JSON is the same screen, and object identity would
    call two views of one element a conflict.

    **It is the ASSIGNED id, not the content seed, and the difference is a
    confirmed defect.** ``perception.element_seed`` is five observables --
    ``cls``, ``text``, ``desc``, ``rid``, ``bounds`` -- and it omits
    ``clickable``, ``role`` and ``label``. Two elements CAN share all five: the
    clickable wrapper of a chat Send control and the non-clickable child it
    wraps do, which is the very shape ``perception.label_of``'s docstring
    describes on the 2026-09-04 screen. ``label_of`` borrows a label only for a
    TAPPABLE element, so those seed twins get DIFFERENT roles -- and a seed
    intersection then treats them as one element. Measured on such a screen
    before this change: ``{"id": <child>, "role": "send"}`` returned the child,
    ``clickable=False`` and ``role=""``, with ``how="id"`` and
    ``conflict=False`` -- an element the ``role`` selector does not match at
    all. Six of 34 hits over the full selector enumeration were that shape.

    ``_assign_ids`` runs inside ``prune``, BEFORE anything resolves, and gives
    a colliding element an ordinal suffix (``eX``, ``eX-2``), so the id is
    unique per dump BY CONSTRUCTION. Intersecting on it makes "a hit means
    every supplied selector agreed" true rather than merely documented; the
    twin pair above now answers ``conflict``, which is what two selectors
    naming different elements has always meant.

    Falls back to the seed when an element carries no id, so a hand-built
    screen that never went through ``prune`` still compares by content rather
    than collapsing every element into one empty bucket.

    Imported inside the function because ``actions`` must stay importable on
    its own. ``perception`` does not import ``actions``, so there is no cycle.
    """
    from tools.mobile import perception

    body = element if isinstance(element, dict) else {}
    assigned = str(body.get("id") or "").strip()
    if assigned:
        return "id:" + assigned
    return "seed:" + perception.element_seed(element)


def _hit(element: dict, how: str, candidates: int) -> dict:
    """A resolution. ``stale`` and ``conflict`` are ALWAYS False here.

    A hit means every selector the target supplied agreed. They were briefly
    settable on a hit, which was dead information AND meant an action could be
    performed on an element only one selector named.
    """
    return {
        "element": element,
        "how": how,
        "candidates": int(candidates),
        "stale": False,
        "conflict": False,
        "supplied": (),
        "stale_selectors": (),
    }


def _miss(
    *,
    stale: bool = False,
    conflict: bool = False,
    supplied: tuple = (),
    stale_selectors: tuple = (),
    candidates: int = 0,
) -> dict:
    """No element, and WHY -- so the boomerang can say something useful.

    ``candidates`` is non-zero only for an ambiguous ``role``: it is how many
    TAPPABLE controls carried it, and ``executor.missing_element_detail`` turns
    that into "name one of them" rather than "nothing matched".
    """
    return {
        "element": None,
        "how": "",
        "candidates": int(candidates),
        "stale": bool(stale),
        "conflict": bool(conflict),
        "supplied": tuple(supplied),
        "stale_selectors": tuple(stale_selectors),
    }


def describe_vocabulary() -> dict:
    """The machine-readable spec handed to the model. Data only, never raises."""
    return {
        "max_actions": MAX_ACTIONS,
        "ops": list(OPS),
        "target": (
            "one of {id, rid, role, label, text}, and they are CROSS-CHECKED: "
            "send two that name different elements, or one that matches "
            "nothing beside one that matches, and the action is handed back "
            "rather than guessed. Prefer `role` or `label` for any action that "
            "follows another -- the server computes them from the element's "
            "own content and they survive a re-layout. `rid` is next. The "
            "short id is THIS SCREEN ONLY: it describes the element as it was "
            "on the screen you were given, bounds included, so it stops "
            "matching when that element moves. It can never match a DIFFERENT "
            "element, but being handed back still costs you a turn."
        ),
        "notes": [
            "tap/type/clear/scroll/press act on a target; back/home/launch take none.",
            "press(key, target) taps the field to focus it and then sends that "
            "key; key is one of "
            + ", ".join(sorted(PRESS_KEYS))
            + ". `enter` is the keyboard's ACTION key and WHAT IT SUBMITS IS "
            "DECIDED BY THE APP, not by the field you name -- so a press is "
            "judged against every element on the screen, ordinary text and "
            "widget names included (a message reading 'did you transfer it?' "
            "counts, and so does a control whose class or resource id contains "
            "one of these words), and "
            "on a screen holding anything irreversible (confirm, pay, delete, "
            "...) it is handed to the tester. On such a screen tap the button "
            "you mean instead; that is judged by its own label.",
            "type carries secret=true ONLY for a value the tester supplied; never "
            "invent a credential and never put one in a plan.",
            "wait needs ms (<= "
            + str(MAX_WAIT_MS)
            + ") or until_text; assert kinds are "
            + ", ".join(ASSERT_KINDS)
            + ".",
            "To check that the app REPLIED, use assert new_text (optionally with "
            "contains): it passes only when text appeared that was not on the "
            "previous screen. screen_changed is WEAK -- any navigation "
            "satisfies it -- so it is never evidence that an answer arrived.",
            "One submit replays for at most "
            + str(SUBMIT_BUDGET_MS)
            + " ms of device time and the waits in one script may total at most "
            + str(MAX_TOTAL_WAIT_MS)
            + " ms. Over the wait total the script is REFUSED; over the submit "
            "budget the replay stops before the next action and hands you the "
            "screen, so plan short scripts rather than one long one.",
            "ask_tester(prompt, field) stops the replay and asks the tester for "
            "that one field. The value is typed and never stored.",
            "end with done(verdict, reason); verdict is one of "
            + ", ".join(VERDICTS)
            + ".",
            "Never send two selectors that point at different elements -- an "
            "`id` and a `text`, a `rid` and a `text`, any pair: the action is "
            "handed back rather than guessed, and so is a target one of whose "
            "selectors matches nothing while another matches. Never reuse an "
            "`id` for an action you plan AFTER a "
            "`type` in the same script: typing opens the keyboard and re-lays "
            "out the screen, so every id from the previous screen goes stale "
            "at once. `rid` and the on-screen text survive that; ids do not.",
            "Plan from the screen you were given. If an element you need is not "
            "on it, stop the script there -- the server hands you the next "
            "screen and you continue. Do not guess coordinates.",
        ],
    }


def response_schema() -> dict:
    """JSON schema for a script, for the packet's ``response_schema`` field."""
    try:
        return Script.model_json_schema()
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.actions.response_schema failed")
        return {"type": "object"}
