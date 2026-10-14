"""Provision `mitmdump` on demand, the SDK/emulator provisioner's own pattern.

A facade: the code lives in the ``mitm_*`` leaves and every name is
re-exported here so ``tools.mobile_capture.mitm_provision.<name>`` keeps
working.

* ``mitm_pins`` -- the pinned release, the per-platform pin table, paths.
* ``mitm_safe`` -- archive size ceilings, the traversal guard, refusals.
* ``mitm_find`` -- locate before download (PATH, then the cache).
* ``mitm_tar`` / ``mitm_zip`` / ``mitm_extract`` -- whole-archive extraction.
* ``mitm_fetch`` -- ``provision``: fetch through ``tools.mobile.downloader``.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import logging
import shutil  # noqa: F401  (tests read ``mitm_provision.shutil.which``)

from tools.mobile_capture.mitm_extract import _extract_archive  # noqa: F401
from tools.mobile_capture.mitm_fetch import (  # noqa: F401
    REASON_NO_MITM_BINARY,
    REASON_NO_MITM_PIN,
    REASON_NO_MITMDUMP,
    _provision_fetch_and_extract,
    _provision_resolved_reply,
    _provision_unpinned_reply,
    provision,
)
from tools.mobile_capture.mitm_find import (  # noqa: F401
    _resolve_binary,
    find_mitmdump,
    mitm_candidates,
)
from tools.mobile_capture.mitm_pins import (  # noqa: F401
    MITM_BIN,
    MITM_DIR,
    MITM_HOST_SUFFIX,
    MITM_PINS,
    MITM_VERSION,
    _leaf,
    _version_dir,
    mitm_dir,
    pin_for_platform,
    platform_key,
)
from tools.mobile_capture.mitm_safe import (  # noqa: F401
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_TOTAL_BYTES,
    _archive_suffix,
    _TraversalRefused,
    safe_join,
)
from tools.mobile_capture.mitm_tar import _extract_tar  # noqa: F401
from tools.mobile_capture.mitm_zip import (  # noqa: F401
    _extract_zip,
    _extract_zip_validate,
    _extract_zip_write,
)

logger = logging.getLogger("tools.mobile_capture.mitm_provision")
