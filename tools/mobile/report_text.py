"""Text, escaping and stamp helpers for the run report: the leaf every other report_* module reads.

The code lives in ``rtxt_clean`` (coercion), ``rtxt_secret`` (masking and ``esc``) and
``rtxt_time`` (durations and stamps); this module re-exports every name callers use.
"""

from __future__ import annotations

from tools.mobile.rtxt_clean import (  # noqa: F401
    _SAFE_TOKEN,
    _SLUG,
    MAX_TEXT,
    _neutralize_markers,
    _slug,
    _text,
    _token,
)
from tools.mobile.rtxt_secret import (  # noqa: F401
    _SECRET_MIN,
    _SECRETS,
    _mask_prose,
    _run_secrets,
    esc,
)
from tools.mobile.rtxt_time import (  # noqa: F401
    LAST_CHECKPOINT_LABEL,
    LAST_CHECKPOINT_SUB,
    LAT_BUCKETS,
    _epoch,
    _lat_bucket,
    _ms,
    _percentile,
    _short_stamp,
    _stamp,
    elapsed_note,
    fmt_ms,
    last_checkpoint,
)
