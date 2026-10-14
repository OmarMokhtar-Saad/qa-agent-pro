"""prep_status leaf: the unfinished-preps, volume-floor and image-relevance gates."""

from __future__ import annotations

import sys

from agents.host_prep_open import unfinished_preps_note
from agents.host_prep_relevance_gate import RelevanceCheck, image_relevance_gate
from agents.host_prep_volume import RoutePhrases, VolumeCheck, VolumeQuery
from agents.host_prep_volume_note import volume_floor_note
from handlers import export
from handlers.seam_alias import alias


# The note lives in agents.host_prep_open; tests and callers patch this name,
# so the wrapper keeps the old signature.
async def prep_unfinished_note(exclude_prep_id: str = "") -> str:
    """Markdown disclosure of in-flight preps. Never raises."""
    return await unfinished_preps_note(exclude_prep_id)


# The volume gate lives in agents.host_prep_volume*; tests and submit_suite patch
# this name, so the wrapper takes the query object and resolves the finalize
# route phrases (export._finalize_route_phrase) before delegating.
def prep_volume_floor_note(query: VolumeQuery) -> "tuple[str, str]":
    """Verdict on the generation VOLUME of a finalizing suite."""
    phrases = RoutePhrases(
        export._finalize_route_phrase(short=True), export._finalize_route_phrase()
    )
    return volume_floor_note(VolumeCheck(*query, phrases))


# The gate itself lives in agents.host_prep_relevance_gate; submit_suite calls
# (and tests patch) this name, so the wrapper takes the check object.
def prep_image_relevance_gate(check: RelevanceCheck) -> "tuple[str, str]":
    """Verdict on the IMAGE RELEVANCE of a finalizing submission."""
    return image_relevance_gate(check)


_image_relevance_gate = alias(sys.modules[__name__], "prep_image_relevance_gate")
_unfinished_preps_note = alias(sys.modules[__name__], "prep_unfinished_note")
_volume_floor_note = alias(sys.modules[__name__], "prep_volume_floor_note")
