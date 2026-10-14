"""Constants of the self-check: schema version, fixture seed, timeout, floors, finding kinds."""

#: Bumped when the report shape changes incompatibly.
SCHEMA = 1

#: This tool's own registered name. It is skipped (and the skip is COUNTED),
#: because invoking it from inside itself recurses. A test asserts the
#: registered name equals this, so a rename cannot silently turn the skip into
#: a no-op that lets the recursion back in.
SELF_TOOL_NAME = "qa_selfcheck"

#: The one fixture value handed to a required parameter. Deliberately shaped so
#: it matches NONE of the patterns below (no ``qa_`` prefix, no ``.py``, no
#: SHOUTING_CASE) and is not a valid value for any enum-ish parameter in the
#: tree -- an echo of it must never be able to look like a finding, and a push
#: target must never be able to look valid. Measured: an earlier seed of
#: ``qa-selfcheck-probe`` produced three phantom findings by being echoed back.
SEED = "selfcheck-fixture"

#: Per-invocation ceiling. Measured 2026-09-04 over all 27 tools of a full
#: edition: the slowest was 4.3s (``adb`` device listing).
INVOKE_TIMEOUT_S = 45.0

#: Bounds that keep the absence assertions from being ambient-satisfiable. An
#: absence check passes trivially the moment its detector stops matching, so a
#: pass reports HOW MUCH it scanned and :func:`floor_violations` refuses to call
#: a thin pass clean.
#:
#: They are all EDITION-RELATIVE, and that is a correction of a measured
#: mistake: absolute floors calibrated on a full edition (24 surfaces) reported
#: the public test-cases-only build (15 surfaces, 12 tools) as inconclusive,
#: which would have made the tool useless for exactly the tester it is for.
#: Measured 2026-09-04: full edition 34 surfaces / 33,177 chars / 25 distinct
#: tool names / 3 flag tokens; dist edition 15 / 15,652 / 11 / 2.
#:
#: The primary guard is not a floor at all but the COMPLETENESS equality in
#: :func:`unaccounted`: every registered tool is either read or a counted skip.
#: A floor can be tuned; an equality cannot be quietly satisfied.
#:
#: There is deliberately NO bound on module or model tokens: today's replies
#: contain ZERO of either (measured, both editions), so any floor would be a
#: lie. Those two checks are proven ONLY by their positive controls in
#: ``tests/test_selfcheck.py``, and the report prints their scanned count so the
#: zero is visible rather than implied.
FLOOR_CHARS_PER_SURFACE = 300
FLOOR_TOOL_MENTION_DIVISOR = 3
FLOOR_TOOL_MENTIONS_MIN = 3
FLOOR_FLAG_TOKENS = 1

#: The finding kinds, in report order. Named here so a caller (and a test) can
#: assert on the SET rather than on strings scattered through the scanner.
KINDS: tuple = (
    "dead_flag",
    "dead_module",
    "absent_llm_symbol",
    "model_reference",
    "server_model_claim",
    "unregistered_tool",
    "invocation_error",
)
