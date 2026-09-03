"""The bounded action vocabulary a model may return, and how it is resolved.

Why a vocabulary at all: the tester's own chat model plans a case as ONE script
from the first screen, and the server replays it. A model that could emit
arbitrary shell would be a remote-code-execution surface reachable from a Jira
ticket. So the script is a closed set of twelve operations validated by pydantic
with ``extra="forbid"`` -- an unknown key is a refusal, not a warning -- and
every one of them lands on an ``adb`` helper that validates its own arguments
again.

Target resolution order is ``id`` > ``rid`` > exact text/desc > contains, and a
MISS IS NOT A FAILURE: it is the boomerang point. The model planned from screen
N and the app is on screen N+1; the honest answer is to hand the new screen back
and ask for the rest of the script, not to fail the tester's case.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)

MAX_ACTIONS = 40
MAX_WAIT_MS = 10000
MAX_TEXT_CHARS = 4000
MAX_ERROR_CHARS = 600
SECRET_MASK = "***"

SCROLL_DIRECTIONS = ("up", "down", "left", "right")
ASSERT_KINDS = ("text_present", "text_absent", "element", "screen_changed")
VERDICTS = ("pass", "fail", "blocked")
URL_SCHEMES = ("https", "http", "market")

# Which operations change the device, and therefore force a re-dump. ``wait`` is
# in here because a wait exists precisely to let the screen change.
MUTATING_OPS = frozenset(
    {"tap", "type", "clear", "back", "home", "scroll", "launch", "open_url", "wait"}
)


class Target(BaseModel):
    """Which element an action acts on. At least one selector is required."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=16)
    text: str = Field(default="", max_length=200)
    rid: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def at_least_one_selector(self) -> "Target":
        if not (self.id.strip() or self.text.strip() or self.rid.strip()):
            raise ValueError("a target needs one of id, text or rid")
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
    op: Literal["assert"]
    kind: Literal["text_present", "text_absent", "element", "screen_changed"]
    text: str = Field(default="", max_length=200)
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


class Script(BaseModel):
    """One case's bounded plan."""

    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS)


OPS = (
    "tap",
    "type",
    "clear",
    "back",
    "home",
    "scroll",
    "wait",
    "launch",
    "open_url",
    "assert",
    "ask_tester",
    "done",
)


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
        if payload.get("secret"):
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


def resolve_target(target: object, pruned: object) -> dict:
    """Find *target* on the pruned screen. A miss is content, not an error.

    ``{"error": None, "content": {"element": <element|None>, "how": str,
    "candidates": int}}``. ``how`` is ``""`` on a miss so a caller can branch on
    a constant rather than on the absence of a key.
    """
    try:
        screen = pruned if isinstance(pruned, dict) else {}
        if isinstance(screen.get("content"), dict):
            screen = screen["content"]
        elements = [e for e in (screen.get("elements") or []) if isinstance(e, dict)]

        if isinstance(target, dict):
            want_id = _norm(target.get("id"))
            want_text = _norm(target.get("text"))
            want_rid = _norm(target.get("rid"))
        else:
            want_id = _norm(getattr(target, "id", ""))
            want_text = _norm(getattr(target, "text", ""))
            want_rid = _norm(getattr(target, "rid", ""))

        if want_id:
            for element in elements:
                if _norm(element.get("id")) == want_id:
                    return _hit(element, "id", 1)
        if want_rid:
            matches = [e for e in elements if _norm(e.get("rid")) == want_rid]
            if not matches:
                matches = [
                    e for e in elements if _norm(e.get("rid")).endswith("/" + want_rid)
                ]
            if matches:
                return _hit(matches[0], "rid", len(matches))
        if want_text:
            exact = [
                e
                for e in elements
                if _norm(e.get("text")) == want_text
                or _norm(e.get("desc")) == want_text
            ]
            if exact:
                return _hit(exact[0], "text", len(exact))
            partial = [
                e
                for e in elements
                if want_text in _norm(e.get("text"))
                or want_text in _norm(e.get("desc"))
            ]
            if partial:
                return _hit(partial[0], "contains", len(partial))
        return {
            "error": None,
            "content": {"element": None, "how": "", "candidates": 0},
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.actions.resolve_target failed")
        return {"error": str(exc), "content": None}


def _hit(element: dict, how: str, candidates: int) -> dict:
    return {
        "error": None,
        "content": {"element": element, "how": how, "candidates": int(candidates)},
    }


def describe_vocabulary() -> dict:
    """The machine-readable spec handed to the model. Data only, never raises."""
    return {
        "max_actions": MAX_ACTIONS,
        "ops": list(OPS),
        "target": "one of {id, text, rid}; prefer the short id from the screen block",
        "notes": [
            "tap/type/clear/scroll act on a target; back/home/launch take none.",
            "type carries secret=true ONLY for a value the tester supplied; never "
            "invent a credential and never put one in a plan.",
            "wait needs ms (<= "
            + str(MAX_WAIT_MS)
            + ") or until_text; assert kinds are "
            + ", ".join(ASSERT_KINDS)
            + ".",
            "ask_tester(prompt, field) stops the replay and asks the tester for "
            "that one field. The value is typed and never stored.",
            "end with done(verdict, reason); verdict is one of "
            + ", ".join(VERDICTS)
            + ".",
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
