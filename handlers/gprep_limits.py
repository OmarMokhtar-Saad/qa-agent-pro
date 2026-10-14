"""Size budgets for the host-mode PREPARE result (text block and image bytes)."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Host-mode PREPARE result -> MCP content assembly (ops-3d-2, items 5 & 6)
#
# Item 5 (tool-result SIZE): a fastmcp tool may return a LIST of content blocks.
# The grounded payload is emitted as ONE text block when it fits the per-block
# byte budget (byte-identical to render_prepare_payload -- the legacy/string-only
# form), and SPLIT across labeled, self-contained blocks only when it does not.
# ops-3c made the payload chunk-friendly (flat top-level keys, self-contained
# category entries), so a field is NEVER cut mid-value and TEXT IS NEVER
# TRUNCATED. The only thing ever dropped for size is an IMAGE, and every drop is
# named (item 6).
#
# Thresholds (stated, with rationale):
#  * _MAX_TEXT_BLOCK_BYTES = 256 KiB -- the soft per-text-block budget. A typical
#    grounded payload (capped ticket/AC/parent/spec/openapi text + 8 category
#    instructions + the TestSuite schema) fits well under this, so the common
#    case stays a single legacy-identical block; only an unusually large context
#    triggers the split. It is a SPLIT trigger, not a truncation cap -- an
#    oversized single field is still emitted whole in its own block.
#  * _MAX_IMAGE_RESULT_BYTES = 4 MiB -- total RAW image bytes forwarded per
#    result. base64 inflates ~33%, so 4 MiB raw is ~5.3 MiB on the wire; this
#    keeps the whole tool result within sane host limits while allowing a few
#    screenshots. Images are ALSO bounded upstream by settings.jira_max_images
#    (count) and settings.jira_max_image_bytes (per image at download).
# --------------------------------------------------------------------------- #

_MAX_TEXT_BLOCK_BYTES = 262_144
_MAX_IMAGE_RESULT_BYTES = 4_194_304


# ATTACHED keys. This is a WHITELIST, so anything not named here is silently
# dropped on the oversized-payload path -- which for a BLOCKING step-zero job
# (the ambiguity preflight) would mean the safety check vanishes on exactly the
# biggest tickets. None-valued keys are filtered out, so a payload without them
# renders byte-identically to before.
_SPLIT_META_KEYS = (
    "version",
    "task",
    "prep_id",
    "untrusted_data_notice",
    "instructions",
    "image_context",
    "jobs_to_run",
    "ambiguity_job",
    "acceptance_criteria_job",
    "orchestration",
    "jobs",
)
