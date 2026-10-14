"""The exploration CHARTER: the terms one explore run is conducted under.

``qa_mobile_test`` hands the tester's model an INTAKE packet before an explore
run starts. The SERVER owns the question set and the typed schema in this
module; the tester's model owns only the wording it puts them in. That division
is the whole point: a model that improvises the questions gives every run a
different charter shape, so nothing is resumable and two runs of "the same"
exploration are not comparable.

Four properties this module holds. Each is pinned in
``tests/mobile/test_mobile_charter.py``, and each pin has a mutant listed in
that file's module docstring -- per the authoring rule, a line here may claim a
property is graded only once its mutant has been RUN.

* **At most :data:`MAX_QUESTIONS` questions are ever asked.** More and the
  tester abandons the flow before the run starts. :data:`QUESTIONS` is a tuple,
  and :func:`questions_for` slices to the cap, so the packet builder cannot
  exceed it however it is called.
* **An unanswered field takes its default AND the report NAMES the default it
  used.** :func:`defaults_used` is the one producer of that list and
  :func:`describe_terms` the one producer of the sentence the report renders.
  "Unanswered" is judged against the RAW input, never against the normalised
  charter: normalising fills every field, so a charter compared against itself
  reports nothing was set -- and a tester who answered ``depth: both`` would be
  told they did not answer. The verdict is therefore computed ONCE, in
  :func:`normalize`, and carried in :data:`DEFAULTS_KEY` so every later reader
  -- the report, a resume in another chat -- reads the same verdict rather than
  re-deriving it from data that can no longer support it.
* **The charter is persisted in the run manifest.** It is stored as a key of
  the explore STATE, which ``run_store`` writes as ``manifest["explore"]``, so
  a resume from another chat explores under the same terms -- which is why it
  is typed here rather than left as prose in a packet.
* **``destructive`` decides whether a guard hit PAUSES the run or REFUSES it,
  and NOTHING else.** :func:`guard_policy` is that whole decision. It does not
  widen the destructive lexicon, ``tools/mobile/replay`` does not import
  this module, and the guard -- including the per-element fidelity sentinel
  added by step 3 -- is UNCHANGED. A future reader will assume otherwise, so it
  is said here: there is no value of ``destructive`` that lets an action
  through the guard. ``allowed`` means the tester has pre-authorised what the
  guard stops on, so the pause is a one-line confirm; it is not a bypass.
  **The REFUSE half IS WIRED, since step 5.** ``session._submit_explore``
  reads :func:`guard_policy` for the run's own charter and sets
  ``executor.Context.guard_refuses``; at the one guard-hit site a refusing
  run gets the refusal detail and a TERMINAL status instead of the tester
  pause, and the stop is recorded on the explore state. What is STILL
  unwired keeps its own disclosure -- ``coverage_plateau``, which nothing
  detects. This is the module's FRONT PAGE, and it is retired in the same
  commit that wired the thing it described: a disclosure that outlives its
  gap is the same defect as one that precedes the fix.

**There is no ``credentials`` field, deliberately, and it is not an oversight
a later reader should quietly correct.** The schema carried one for exactly one
revision. It had NO consumer: nothing read it but the sentence that printed it,
so its entire observable effect was writing secret-adjacent free text into a
report that lands on disk. A field with no consumer and a live leak route is
pure risk, so it was DELETED. A future step that genuinely needs to name a
credential FIELD must build it with an ALLOWLIST of known field names -- never
a filter that tries to tell a one-word NAME from a one-word VALUE, which is not
something a filter can do by construction: ``hunter2``, ``1234``, ``000000``
and ``otp#1234`` are all single whitespace-free tokens carrying no separator.
Whatever it builds must not re-open what ``tools/mobile/ime.py`` guarantees:
the lane types credential VALUES through the IME on stdin with an EMPTY argv
(``type_text`` -> ``adb.shell_in(serial, [], StdinFeed(...))``) precisely so a
value never reaches argv, a packet, a trace or the run store, and
``tools/mobile/run_packets.build_tester_request`` carries a field NAME and a prompt
and has no way to carry a value.

No model call, no device, no file and no network: this module is data and
coercion. Every coercion catches ``OverflowError`` beside ``TypeError`` and
``ValueError``, because ``json.loads`` admits ``Infinity`` and
``int(float("inf"))`` raises the third one.

Not wired, stated rather than implied: ``stop_on="coverage_plateau"`` is
accepted, persisted and REPORTED, and behaves as ``budget``. Nothing detects a
plateau yet, so nothing grades a plateau stop -- follow-up. The report says so
in words rather than letting a tester believe a stop condition applied.
"""

from __future__ import annotations

import json  # noqa: F401
import logging  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import explore_stop  # noqa: F401
from tools.mobile.charter_clauses import (  # noqa: F401
    _budget_clause,
    _charter_clause,
    _defaults_clause,
    _stop_clauses,
)
from tools.mobile.charter_coerce import (  # noqa: F401
    _choice,
    _int,
    _items,
    _one_line,
    _sent,
    _unset,
    defaults_used,
    stop_on,
)
from tools.mobile.charter_consts import (  # noqa: F401
    DEPTH_DIRECTIVES,
    MAX_BUDGET_UNITS,
    MAX_CHARTER_BYTES,
    MAX_OFF_CHARTER_RECORDS,
    MAX_QUESTIONS,
    MAX_SCOPE_ITEM_CHARS,
    MAX_SCOPE_ITEMS,
    MAX_SCOPE_MATCH_CHARS,
    OFF_CHARTER_NOTE,
    SCOPE_ELEMENT_FIELDS,
    SCOPE_IN,
    SCOPE_OUT,
    SCOPE_SCREEN_FIELDS,
    SCOPE_UNCONFIRMED,
    UNCONFIRMED_NOTE,
    logger,
)
from tools.mobile.charter_describe import (  # noqa: F401
    _rejected_clause,
    describe_terms,
    rejection_notice,
)
from tools.mobile.charter_normalize import (  # noqa: F401
    _budget_terms,
    _coerced_terms,
    _schema_only,
    _scope_terms,
    normalize,
)
from tools.mobile.charter_parse import (  # noqa: F401
    _decode_charter,
    _oversize_refusal,
    _unreadable_refusal,
    parse,
)
from tools.mobile.charter_policy import (  # noqa: F401
    budget_cap,
    budget_for,
    depth_directive,
    guard_policy,
    record_off_charter,
)
from tools.mobile.charter_questions import (  # noqa: F401
    ASKABLE,
    QUESTIONS,
    RESET_HINT_WORDS,
    destructive_hint,
    questions_for,
)
from tools.mobile.charter_rejected import (  # noqa: F401
    _refusal_message,
    _rejected,
    _rejected_budget,
    _rejected_enums,
    rejected_fields,
)
from tools.mobile.charter_schema import (  # noqa: F401
    ALIASES,
    DEFAULTS_KEY,
    DEPTHS,
    DESTRUCTIVE,
    PAUSE,
    REFUSE,
    REJECTABLE,
    REJECTED_KEY,
    STOP_ON,
    WIRED_STOPS,
    defaults,
)
from tools.mobile.charter_scope import (  # noqa: F401
    _named,
    _needle,
    _scope_phrase,
    _screen_terms,
    scope_lines,
    scope_sentence,
    scope_verdict,
)
