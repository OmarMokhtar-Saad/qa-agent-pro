"""I/O-side ports of the host submit pipeline (C3a step 0).

Members are typed attributes, not methods, so a port is satisfied by a module,
a namespace or a fake, and the wrappers that keep their old many-parameter
signature stay representable. Each Protocol holds at most 10 members.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

Fn = Callable[..., Any]
AFn = Callable[..., Awaitable[Any]]


class IoPort(Protocol):
    """Stores, parsers and the finalize entry the handler keeps patchable."""

    save_suite: AFn
    save_checklist: AFn
    load_suite: AFn
    earlier_suites: AFn
    family_of: AFn
    set_export_path: AFn
    finalize_generation: AFn
    deserialize_prepared: Fn
    parse_host_suite: Fn
    code_fingerprint: Fn
    settings: Any
    try_retain: AFn
    sidecar_merge: Fn


class ReviewPort(Protocol):
    """Acceptance-criteria, grounding, checklist and ambiguity review builders."""

    build_host_ac_section: Fn
    extract_host_acs: Fn
    extract_host_checklist: Fn
    build_host_checklist_section: Fn
    build_grounding_section: Fn
    route_ungrounded_cases: Fn
    extract_ambiguity_result: Fn
    build_ambiguity_result_section: Fn


class MediaDupPort(Protocol):
    """Image-description and duplicate-review builders."""

    extract_host_image_descriptions: Fn
    build_host_image_section: Fn
    image_relevance_counts: Fn
    screen_duplicate_groups: Fn
    dup_shortlist_cases_json: Fn
    apply_duplicate_groups: Fn
    host_dedup_dup_agreements: Fn
    build_dup_contradiction_headline: Fn
    build_dup_contradiction_pairs: Fn
    build_dup_shortlist_counted: Fn


class ImageNotePort(Protocol):
    """Image-gap notes of generate_prepare that tests patch."""

    text_only_image_skip_note: Fn
    attested_image_gap_note: Fn


class DupSectionPort(Protocol):
    """Duplicate-section builder of host_dedup that tests patch on the handler."""

    build_duplicate_section: Fn
