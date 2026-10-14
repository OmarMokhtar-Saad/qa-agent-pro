"""The oversized-review-fields refusal for the Path-A sidecar overlay.

The F7 fix stopped applying the cap to the SERVER's own merge of already-
measured category rows, which is right: eight accepted categories could
never finalize, and the reply blamed the tester for JSON they never sent.
But the Path-A sidecar branch overlays FIVE fields taken from the CURRENT
call's `suite_json` -- duplicate_groups, acceptance_criteria,
ambiguity_result, checklist_items, image_descriptions -- into that merge
before parsing it, and `parse_host_suite`'s cap was the only thing measuring
them. With the cap off for the whole call, a 12 MB `acceptance_criteria` was
accepted at the default 4 MB limit (measured in review).

So the bound is applied HERE, to what the TESTER sent, before the overlay --
which is what F7 said it should bound all along. Never raises: a
non-serialisable object is not oversized, and `parse_host_suite` refuses it
by name a few lines later.
"""

from __future__ import annotations

import json

from tools import settings_port


def _oversized_text(size: int, cap: int) -> str:
    """The refusal naming the measured size and the byte limit."""
    return (
        f"⚠️ The review fields in this submission are {size} bytes, over "
        f"the {cap}-byte limit. Your per-category test cases are still staged and "
        "are NOT affected -- resend this submit with a smaller "
        "`duplicate_groups` / `acceptance_criteria` / `ambiguity_result` / "
        "`checklist_items` / `image_descriptions`, or with none of them, and the "
        "suite will finalize from the staged rows."
    )


def oversized_submission_note(obj: object) -> str:
    """``""`` if ``obj`` is within ``QA_PREP_MAX_BYTES``, else a refusal naming it."""
    cap = int(getattr(settings_port.get(), "qa_prep_max_bytes", 0) or 0)
    if not cap or obj is None:
        return ""
    try:
        size = len(json.dumps(obj, ensure_ascii=False).encode("utf-8", "ignore"))
    except (TypeError, ValueError, RecursionError):
        return ""
    if size <= cap:
        return ""
    return _oversized_text(size, cap)
