"""Rule blocks and composition of the shared category system prompt."""

from __future__ import annotations

from agents.scenario_prompt_text import (
    _CATEGORY_HEADER,
    _CATEGORY_JSON_TAIL,
    _CATEGORY_RULES,
)
from tools.models import TestSuite
from tools.untrusted import _GUARD

# Used ONLY on the cached-prefix path, where the FOCUS/Requirements header has
# moved to the trailing user block and the rules would otherwise open as a bare
# bullet list with no lead-in.
# F5 (2026-08-29). On the TICKET-5692 run, two of eight categories came back in a
# {step, action, payload} shape instead of the schema's {step_number, action,
# expected_result, test_data}. Those 85 steps carried NO expected_result at all,
# `additionalProperties: false` rejected them, and the client's regeneration of
# exactly those two categories introduced 65 steps whose expected_result merely
# restated the action. The prose rules above were already correct and already
# demanded a verifiable expected_result -- what was missing was one concrete
# instance of the SHAPE, which is what a generating model actually copies.
#
# Placed between the rules and the "output ONLY JSON" tail: right after the
# rules that describe the fields, and right before the instruction to emit
# only JSON. It is NOT the last thing in the prompt -- _TEST_DATA_INSTRUCTION,
# rtm_hint and _GUARD still follow at 987-989.
#
# BRACES ARE SINGLE, deliberately. Only _CATEGORY_JSON_TAIL is passed through
# .format() in _category_shared_system; this constant is plain concatenation, so
# doubling would ship a doubled opening brace to the model -- an example of the
# WRONG shape, in the one fix whose entire purpose is shape fidelity. A test
# asserts that no doubled brace reaches the rendered prompt.
_STEP_SHAPE_EXAMPLE = """\
Every entry in "steps" MUST use exactly these four keys. One fully-worked step:

  {
    "step_number": 2,
    "action": "On the Payment summary screen, enter card number 4111 1111 1111 1111 and tap 'Pay'.",
    "test_data": "card_number: 4111 1111 1111 1111, expiry: 12/29, cvv: 123",
    "expected_result": "The Payment result screen opens showing 'Payment successful' and the booking reference in the format BK-000000."
  }

Note what makes that expected_result acceptable: it names something the tester
can SEE and that would look different if the feature were broken. An
expected_result that repeats the action is NOT acceptable and will be rejected --
never write "The step completes successfully: <the action again>", "works as
expected", or "no error occurs". If a step genuinely has no observable outcome of
its own, merge it into the next step rather than inventing an assertion.

"""


_CATEGORY_RULES_LEAD = "Requirements that apply to EVERY test case you generate:\n"


# Appended to EVERY category prompt. Unconditional since 2026-08-12
# (QA_TEST_DATA_STRATEGY was deleted). The base template constant itself is
# unchanged, so the cached-prefix recomposition still matches it byte for byte.
_TEST_DATA_INSTRUCTION = """

TEST DATA STRATEGY (populate the case-level "test_data" array ONLY when the case
manipulates data — registration, login, forms, search, uploads, API request
bodies; otherwise leave it as an empty array []):
For each distinct data field the test needs, add ONE object with:
- "field": the field name (e.g. "username", "email", "national_id").
- "strategy": exactly one of:
    * "unique_per_run" — must be NEW/unique every execution (new username, email,
      national id) to avoid "already exists" collisions.
    * "seed_account" — a pre-existing fixed account/record the environment is
      seeded with (a known login, an existing order id).
    * "chained" — a value produced by an EARLIER test case in THIS category (e.g.
      login reuses the account a registration case created); set "chained_from"
      to that case's tc_id.
    * "static" — a fixed constant valid for every run (a country code, a fixed
      valid password).
- "example_value": a SAFE, CLEARLY-FAKE example — NEVER a real or real-looking
  person's data. Use obvious placeholders with a run token, e.g.
  "testuser_<timestamp>", "qa+<timestamp>@example.com", "Pass@123",
  "000-00-0000". NEVER invent a plausible real SSN, national id, credit-card
  number, phone number, or full name. Keep it short (no long literals — see the
  length rule above).
- "chained_from": the tc_id of the producing case when strategy is "chained";
  otherwise null.
- "notes": a short (<=100 char) hint on how to obtain/rotate the value.
"""


# The rules the host-mode category jobs inject into a FRESH prompt
# (agents/host_*.py). Until 2026-08-16 this was a SPLIT: a
# _QUALITY_RETRY_PREAMBLE that ACCUSED the model of prior bad output, correct
# only on a genuine re-ask, plus the body below. P2-E2 deleted the server-side
# retry/repair ladder that was the preamble's only consumer, so only the body
# remains and there is nothing left to split it from.
_QUALITY_RULES_BODY = """
Every step's "action" text MUST embed the literal value/payload used (e.g. "Enter ' OR '1'='1
into the 'Username' field", not "enter a SQL injection string"; "Enter 256 characters into the
'Bio' field", not "enter a very long string"). Every test_data value MUST be a concrete example
tied to a named field — never "anything", "any value", "some value", "valid data", "N/A", or "TBD".
The FIRST step MUST make the starting location findable — the exact URL when known, an explicit
click-path from the home page (e.g. "From the home page, click 'Login' in the top-right
navigation"), or simply NAMING the screen the tester starts on (e.g. "From the Account settings
screen, tap the Notifications toggle ON"). Naming the screen is enough. NEVER open a case inside
a field or on a toggle with no screen named ("Enter 'abc' in the amount field and tap Save"), and
NEVER write a bare "Navigate to the Login page" or "go to the registration form".
Every expected_result MUST state the concrete observable outcome — the exact on-screen message
(quoted when known), field/button state, or resulting page/URL. NEVER write "appropriate error
message", "proper validation", "behaves correctly", or "as expected" without saying exactly what
the tester will see.
"""


# Upfront form: same rules, same leading blank line the old constant contributed
# after the category task template, minus the accusation.
_QUALITY_RULES_UPFRONT = "\n" + _QUALITY_RULES_BODY


def _category_response_model() -> type[TestSuite]:
    """The response model every category call uses.

    Shared by _generate_for_category and the cache warm-up so the JSON schema
    baked into `system` by llm._json_system is byte-identical in both — a
    mismatch would warm an entry nothing ever reads.
    """
    return TestSuite


# The category-INDEPENDENT system prompt used when prompt caching is ON.
#
# Byte-identical for all 8 categories, for the remediation pass and for the
# quality retry — which is exactly what makes the cached prefix reusable. The
# per-category FOCUS / preferred-type / case-count instruction lives in the
# trailing UNCACHED user block instead (see _CATEGORY_TASK_TEMPLATE).
#
# _GUARD still terminates the system prompt, and the trailing suffix carries
# ONLY trusted, code-authored text — every wrap_untrusted block stays in the
# cached user prefix, so containment is unchanged.
def _category_shared_system(rtm_hint: str) -> str:
    """The category-INDEPENDENT system prompt used when prompt caching is ON."""
    return (
        _CATEGORY_HEADER
        + _CATEGORY_RULES_LEAD
        + _CATEGORY_RULES
        + _STEP_SHAPE_EXAMPLE
        + _CATEGORY_JSON_TAIL.format()
        + _TEST_DATA_INSTRUCTION
        + rtm_hint
        + _GUARD
    )
