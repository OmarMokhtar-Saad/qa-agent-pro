"""Host mode: which jobs and images a prepare reply ships (C3c).

Moved from handlers/generate.py. Pure leaves over the grounded page and the
settings seam; no hooks needed.
"""

from __future__ import annotations

import logging
from typing import Any

import llm
from agents.host_gen_types import _Grounding, _ImageFlags
from config.settings_guard import never_raise
from tools import atomic_checklist, rule_packs, settings_port

logger = logging.getLogger("agents.host_mode")


def _pop_page_screenshot(grounded: _Grounding) -> Any:
    """Pop the DEFERRED Tier-3 screenshot BEFORE _prepare_generation.

    Raw bytes must never reach serialize_prepared / the prep store (JSON). The
    key is absent unless Tier 2 rendered a screenshot that yielded no elements.
    """
    if isinstance(grounded.ui_content, dict):
        return grounded.ui_content.pop("vision_screenshot", None)
    return None


def _screenshot_image(page_screenshot: Any) -> dict:
    return {
        "filename": "rendered_page.png",
        "mime": "image/png",
        "data": page_screenshot,
    }


def host_forward_images(
    grounded: _Grounding, attached_images: list | None, host_img: bool
) -> list:
    """Pop the deferred screenshot, then list the images the host should see.

    Jira ticket images are key-gated inside jira_fetcher._fetch_jira_images;
    chat attachments and the page screenshot need NO key (keyless host mode).
    """
    page_screenshot = _pop_page_screenshot(grounded)
    host_images: list = []
    if host_img:
        host_images = [
            i for i in ((grounded.url_content or {}).get("images") or []) if i
        ]
        host_images += [i for i in (attached_images or []) if i]
        if page_screenshot:
            host_images.append(_screenshot_image(page_screenshot))
    return host_images


# Narrower than the flag, exactly like _ac_job: with nothing to forward there
# is no job to ship. Widened for the host-ATTESTED chat-attachment channel:
# with attached_image_count > 0 the images live in the HOST's context and
# host_images is empty, but IMAGE_JOB must still ship (its image_descriptions[]
# is the only tell that the attested images were read).
#
# Relevance (QA_IMAGE_RELEVANCE_ENABLED), preflight (Batch 4 LAYER 1,
# QA_HOST_IMAGE_PREFLIGHT_ENABLED) and require (Batch 4 LAYER 2,
# QA_HOST_IMAGE_REQUIRE_RELEVANT, default OFF) are each narrower than the job
# itself: with no job shipped there is nothing to ask for. All are decided and
# stamped HERE, at prepare time, so a mid-flow .env flip or launcher update
# cannot change what an in-flight prep expects. Require also needs screens
# actually FORWARDED on the prep (submit side), so ticket-image-only preps are
# never enforced.
def image_job_flags(host_img: bool, host_images: list, attested: int) -> _ImageFlags:
    """Decide the image job and its relevance/preflight/require stamps."""
    _img_job = bool(host_img and attested)
    _img_relevance = bool(_img_job)
    _img_preflight = bool(_img_relevance)
    _img_require = bool(
        _img_relevance
        and getattr(settings_port.get(), "qa_host_image_require_relevant", False)
    )
    return _ImageFlags(
        job=_img_job,
        relevance=_img_relevance,
        preflight=_img_preflight,
        require=_img_require,
    )


# The Phase 3a risk/plan jobs were DELETED on 2026-08-16 (P2-H); risk scoring
# is the deterministic score_and_sort heuristic, no test-plan artifacts exist.
# Residue R4 (ledger id `atomic_checklist.decompose`): the decomposition call
# and its _prepare_generation parameter were deleted (P2-F2, 2026-08-16), so
# this decides ONE thing: whether CHECKLIST_JOB ships to the host. True on
# every install since 2026-08-14 (QA_ATOMIC_CHECKLIST_ENABLED hardcoded ON).
# Reads the SEAM, not a literal, so tests/conftest.py's pin governs it.
def checklist_job_on() -> bool:
    """Whether CHECKLIST_JOB ships to the host on this prepare."""
    return bool(
        atomic_checklist.checklist_enabled() and llm.resolve_generation_mode() == "host"
    )


# Residue R4: the ONE capability narrowing this fold accepts, and the TESTER
# has to be told. agents/test_scenario_agent.py sets rule_packs.checklist_mode
# ONLY when the checklist is already non-empty; with the decomposition
# boomeranged it is empty, so the MANDATED rule-pack lines fall back to PROMPT
# + ADVISORY mode: they still reach the generator, but nothing checks them
# against the suite. Disclosed ONLY when a pack actually mandated a line AND
# the fallback really happened. Never raises: a disclosure that cannot be
# computed must not break a prepare.
def rule_pack_narrowed(prepared: Any, checklist_job: bool) -> bool:
    """Whether a mandated rule pack fell back to prompt + advisory mode."""
    _rp_narrowed = False
    if checklist_job:
        # pragma: no cover - advisory disclosure only
        with never_raise(logger, "rule-pack narrowing check failed", exc_info=True):
            _rp = getattr(prepared, "rule_packs", None)
            _rp_narrowed = bool(
                _rp is not None
                and rule_packs.rule_pack_checklist_items(_rp)
                and not getattr(_rp, "checklist_mode", False)
            )
    return _rp_narrowed


# 2026-08-09 (review M1): the images that will ACTUALLY ride on this reply,
# resolved BEFORE the envelope so the stamp records what SHIPS rather than what
# was read off the tray. Reused verbatim as `ticket_images`: one expression, so
# the simulated budget and the applied budget cannot drift.
def prospective_images(img_job: bool, host_images: list, grounded: _Grounding) -> list:
    """The images that will actually ride on this prepare reply."""
    return (
        list(host_images)
        if img_job
        else list((grounded.url_content or {}).get("images") or [])
    )
