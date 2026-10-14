"""Host mode: image-description job, relevance and preflight (facade).

The implementation lives in agents/host_image_*.py; every name callers or tests reach
through here is re-exported from its home. Cut verbatim from agents/host_mode.py (P4.3).
"""

from __future__ import annotations

from agents.host_image_counts import (  # noqa: F401
    image_relevance_counts,
    off_topic_images,
)
from agents.host_image_extract import extract_host_image_descriptions  # noqa: F401
from agents.host_image_job import (  # noqa: F401
    _IMAGE_JOB_INSTRUCTIONS,
    _IMAGE_JOB_MARKER,
    IMAGE_JOB,
)
from agents.host_image_preflight import (  # noqa: F401
    _IMAGE_PREFLIGHT_SPEC,
    _IMAGE_PREVENTION_CLAUSE,
    _IMAGE_PREVENTION_MARKER,
    IMAGE_PREFLIGHT_JOB,
)
from agents.host_image_relevance import (  # noqa: F401
    _IMAGE_JOB_RELEVANCE_SPEC,
    _IMAGE_RELEVANCE_CLAUSE,
    IMAGE_RELEVANCE_JOB,
)
from agents.host_image_result import (  # noqa: F401
    _IMG_MAX_DESC_CHARS,
    _IMG_MAX_ITEMS,
    _IMG_MAX_REASON_CHARS,
    _IMG_RELEVANCE_VALUES,
    HostImageResult,
)
from agents.host_image_section import build_host_image_section  # noqa: F401
