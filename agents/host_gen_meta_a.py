"""Host mode: prep-meta keys 1-19 of the prepare pipeline (C3c).

Moved from handlers/generate.py. Every key is STAMPED at prepare time, never
read live at submit: the launcher applies updates at the next idle minute and a
host-mode flow is idle exactly between prepare and submit, so a restart or an
.env flip lands MID-FLOW and an in-flight prep must not change. The boot version
and the code fingerprint arrive through hooks so patches on them still bite.
"""

from __future__ import annotations

import logging

from agents.host_gen_hooks import GenerateHooks
from agents.host_gen_types import _PrepRun

logger = logging.getLogger("agents.host_mode")


# Source text and url, advisory, round, writer version and fingerprint.
#
# image_reference_advisory (A6): already computed in tools/jira_mcp.py, stamped
# so it survives to finalize and to the prepare reply.
# app_version (ops-6): a restart mid-flow means a different code version
# deserializes the envelope than wrote it; stamp the writer.
# code_fingerprint (F4): the version string is not enough (a checkout reports
# the same 0.1.0 across code changes and suites.db is shared per machine), so
# a content hash of the prep-shaping modules; disclosure only, "" if none.
def _source_keys(run: _PrepRun, hooks: GenerateHooks) -> dict:
    """Source text and url, advisory, round, writer version and fingerprint."""
    return {
        "source_text": run.text,
        "source_url": run.text if run.grounded.url_content else "",
        "image_reference_advisory": str(
            (run.grounded.url_content or {}).get("image_reference_advisory") or ""
        ),
        "round": 0,
        "app_version": hooks.common.boot_version(),
        "code_fingerprint": hooks.run.code_fingerprint(),
    }


# Which step-zero jobs this prep handed the host, and the text-only skips.
#
# Stamped so submit knows whether to expect `acceptance_criteria` and
# `image_descriptions`, and so a skipped beat 2 (source_plan='jira' while the
# ticket referenced screens) is disclosed, never silent.
def _job_keys(run: _PrepRun) -> dict:
    """Which step-zero jobs this prep handed the host, and the text-only skips."""
    return {
        "host_ambiguity_review": bool(run.host_amb),
        "host_ac_job": bool(run.ac_job),
        "host_image_job": bool(run.img.job),
        "text_only_image_skip_count": run.skip.total,
        "text_only_image_skip_names": run.skip.names,
    }


# Attested, shipped and read image counts.
#
# attached_image_count: host-ATTESTED chat attachments; the submit reply
# compares it with the returned image_descriptions and says so when nothing
# came back.
# captured_image_count: the SHIPPED count (review M1, 2026-08-09), not the
# pre-budget tray count, because _select_prepare_images caps the reply at
# jira_max_images; the forwarded-screens guard sums it with the attested count
# and carry-forward reports it as the prior prep's promise.
# captured_image_read: the pre-budget count, forensics only.
def _image_count_keys(run: _PrepRun) -> dict:
    """Attested, shipped and read image counts."""
    return {
        "attached_image_count": run.intake.attested,
        "captured_image_count": run.captured_shipped,
        "captured_image_read": run.intake.captured,
    }


# Capture ids, labels and carry-forward provenance.
#
# capture_ids: IDS ONLY, never bytes (the JSON prep store must not hold them),
# so a RE-PREPARE of the same source inside the duplicate-prep window can
# revive the exact screens and the refusal can NAME what would vanish. They
# are host-supplied strings, so the count and each id's LENGTH are capped.
# carried_forward_*: which prep the screens came from, how many were revived,
# and whether the tester ACKed generating without them.
def _capture_keys(run: _PrepRun) -> dict:
    """Capture ids, labels and carry-forward provenance."""
    return {
        "capture_ids": [str(c)[:64] for c in (run.capture_ids or [])][:24],
        "captured_image_labels": [str(x) for x in (run.cap_labels or [])][:8],
        "carried_forward_capture_count": len(run.carry.carried_ids),
        "carried_forward_from_prep": run.carry.carried_from,
        "image_carry_ack": bool(run.image_carry_ack),
    }


def meta_head(run: _PrepRun, hooks: GenerateHooks) -> dict:
    """Prep-meta keys 1-19, source_text through image_carry_ack, in order."""
    return {
        **_source_keys(run, hooks),
        **_job_keys(run),
        **_image_count_keys(run),
        **_capture_keys(run),
    }
