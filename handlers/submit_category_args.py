"""The entry gates of a category submission: ids sanitised, canonical, echo-safe."""

from __future__ import annotations

from agents.host_category_jobs import normalize_category
from handlers import _reply
from handlers.submit_category_types import _CategoryArgs


def _canonical_name(category_name: str) -> str:
    """The canonical UNIQUE key for a category name, or the name itself."""
    # Collapse aliases onto the canonical UNIQUE key so parallel workers that
    # submit "Positive" vs "Positive / Happy Path" do not create two rows.
    return normalize_category(category_name) or category_name


def _too_long_reply(category_name: str) -> str:
    """The refusal for a `category_name` that is not a category name."""
    # F20: a 300 KB `category_name` is not a category, and every reply on
    # this path -- the refusals AND the success summary -- echoed it whole.
    # One gate here bounds them all, rather than clipping a dozen strings.
    return (
        "⚠️ That `category_name` is "
        + str(len(category_name))
        + " characters long, which is not a category name. Use one of "
        "the names from `orchestration.expected_categories` / "
        "`categories[].name` in the prepare payload. Received: "
        + _reply._clip_echo(category_name, 60)
    )


# Same invariant as `_unrecognized_names_line`, for the OTHER host-supplied
# value in the category handler: `display` goes through the bounded, fence-safe
# helper so a name carrying a fence cannot close its own span inside bold
# server-authored prose the host model reads as trusted.
#
# Computed AFTER the alias collapse, not on entry. The first cut of this fix
# snapshotted the value on entry and every reply then showed the tester's alias
# instead of the canonical category --
# `test_submit_category_records_and_reports` caught it, reading "**Positive**"
# where the contract says "**Positive / Happy Path**". The length gate already
# bounds the size; what this adds is fence safety, and it has to see the same
# value the replies mean.
def _category_submit_args(prep_id: str, category_name: str) -> str | _CategoryArgs:
    """Validate the (already safe_prep_id-gated) ids, or return the refusal reply."""
    category_name = (category_name or "").strip()
    if not prep_id:
        return (
            "⚠️ Missing prep_id. Prepare a generation first with "
            "`qa_prepare_test_cases`."
        )
    if not category_name:
        return (
            "⚠️ Missing category name. Pass the category you are submitting cases for."
        )
    category_name = _canonical_name(category_name)
    if len(category_name) > 200:
        return _too_long_reply(category_name)
    display = _reply._clip_echo(category_name, 60)
    return _CategoryArgs(prep_id, category_name, display)
