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

import logging

from tools.mobile.act_cands import (  # noqa: F401
    TAP_TEXT_AMBIGUOUS,
    candidates_for,
    tap_text_candidates,
)
from tools.mobile.act_cred import (  # noqa: F401
    CREDENTIAL_TERMS,
    SECRET_MASK,
    _surface_reads_as_credential,
    _value_surface,
    is_credential_action,
)
from tools.mobile.act_hit import _hit, _identity, _miss  # noqa: F401
from tools.mobile.act_notes import (  # noqa: F401
    _action_notes,
    _budget_notes,
    _wait_notes,
)
from tools.mobile.act_parse import (  # noqa: F401
    _refused,
    _validation_refusal,
    combine_queued_actions,
    parse_script,
    too_many_actions,
)
from tools.mobile.act_redact import action_text, redact_action  # noqa: F401
from tools.mobile.act_reply import (  # noqa: F401
    decode_reply,
    expect_apps_from,
    script_finding,
)
from tools.mobile.act_resolve import (  # noqa: F401
    _pick_agreed,
    _resolve_found,
    resolve_target,
)
from tools.mobile.act_select import (  # noqa: F401
    OURS,
    SELECTORS,
    _labelled_candidates,
    _norm,
    _rid_candidates,
    _selector_values,
    _text_candidates,
)
from tools.mobile.act_vocab import describe_vocabulary, response_schema  # noqa: F401
from tools.mobile.action_consts import (  # noqa: F401
    ASSERT_KINDS,
    MAX_ACTIONS,
    MAX_ERROR_CHARS,
    MAX_EXPECT_APPS,
    MAX_MODEL_ACTIONS,
    MAX_TEXT_CHARS,
    MAX_TOTAL_WAIT_MS,
    MAX_WAIT_MS,
    MUTATING_OPS,
    PRESS_KEYS,
    SCROLL_DIRECTIONS,
    SUBMIT_BUDGET_MS,
    TAP_TEXT_MAX_CANDIDATES,
    TAP_TEXT_MAX_QUERY_CHARS,
    UNTIL_TEXT_DEFAULT_MS,
    URL_SCHEMES,
    VERDICTS,
    WAIT_OPS,
    WAIT_UNTIL_DEFAULT_S,
    WAIT_UNTIL_MAX_S,
    WAIT_UNTIL_OPS,
    is_package_id,
)
from tools.mobile.action_models_assert import (  # noqa: F401
    _VALUE_FIELD_NAMES,
    AskTesterAction,
    AssertAction,
    DoneAction,
)
from tools.mobile.action_models_basic import (  # noqa: F401
    BackAction,
    ClearAction,
    FillAction,
    HomeAction,
    TapAction,
    TapTextAction,
    Target,
    TypeAction,
    _Base,
)
from tools.mobile.action_models_nav import (  # noqa: F401
    ClearAppDataAction,
    LaunchAction,
    OpenUrlAction,
    PressAction,
    ScrollAction,
    WaitAction,
    WaitUntilAction,
)
from tools.mobile.action_schema import (  # noqa: F401
    OPS,
    TURN_FIELD_SCHEMA,
    TURN_FIELDS,
    VALUE_BEARING_OPS,
    Action,
    Script,
)
from tools.mobile.action_waits import (  # noqa: F401
    _bare_until_text_count,
    _explicit_wait_ms,
    bare_wait_ms_each,
    total_wait_ms,
)

logger = logging.getLogger(__name__)
