"""Host mode: category-job constants (logger, thin-source limit, example case, aliases)."""

from __future__ import annotations

import logging

logger = logging.getLogger("agents.host_mode")

# F10 (2026-08-30): a source with nothing in it to ground a test case still
# gets the full "an empty category is always wrong" instruction and a request
# for 8-10 cases per category. The COUNT is deliberately not lowered -- see the
# reverted-band note in agents/test_scenario_agent._case_count_bounds for the
# measurement that rules that out -- so what changes is what the worker is told
# about inventing. Deliberately narrow: two thresholds that a real one-line
# feature description clears comfortably, so a normal run's packet is unchanged.
_THIN_SOURCE_CHARS = 40

# v1.97.0 cursor-hardening (item 4a): one schema-valid EXAMPLE case, included
# verbatim in every job packet a worker model reads, so a worker has ground
# truth for enum spelling (Priority/TestType) and list-field shape (steps,
# test_data) instead of guessing -- the same class of mistake that silently
# dropped `testable_surface` (item 9), just for TestCase's own fields.
# Category-agnostic on purpose: it illustrates SHAPE, not this ticket's
# content, so it is hoisted once into build_category_jobs_batch's `shared`
# block rather than repeated per job.
_EXAMPLE_VALID_CASE = {
    "tc_id": "TC-001",
    "module": "Login",
    "title": "User logs in with valid username and password",
    "priority": "Medium",
    "type": "Functional",
    "preconditions": "A registered user account exists",
    "steps": [
        {
            "step_number": 1,
            "action": "Enter a valid username and password, then submit",
            "test_data": "username: testuser1",
            "expected_result": "Login succeeds and the dashboard is shown",
        }
    ],
    "test_data": [
        {
            "field": "username",
            "strategy": "unique_per_run",
            "example_value": "testuser1",
            "notes": "Use a fresh seeded test account per run",
        }
    ],
}


# F6: the 15 spellings actually observed in the audit trail, mapped onto the 8
# canonical CATEGORIES names. Keys are casefolded and already segment-reduced
# (the part before any "/"), which is why `Positive / Happy Path` -> `positive`
# and `UI/UX Validation` -> `ui` both land here.
_CATEGORY_ALIASES: dict = {
    "positive": "Positive / Happy Path",
    "happy path": "Positive / Happy Path",
    "negative": "Negative / Error Flows",
    "error flows": "Negative / Error Flows",
    "boundary": "Boundary Values",
    "boundary value": "Boundary Values",
    "boundary values": "Boundary Values",
    "edge": "Edge Cases",
    "edge case": "Edge Cases",
    "edge cases": "Edge Cases",
    "state": "State Transitions",
    "state transition": "State Transitions",
    "state transitions": "State Transitions",
    "security": "Security",
    "ui": "UI/UX Validation",
    "ux": "UI/UX Validation",
    "ui ux": "UI/UX Validation",
    "ui/ux": "UI/UX Validation",
    "integration": "Integration",
    "integrations": "Integration",
}
