"""The operator's configured mobile cache directory (split out of ``paths``)."""

from __future__ import annotations

from config.settings_guard import never_raise
from tools import settings_port
from tools.process_env import get_env_str


def _configured() -> str:
    """The operator's configured cache dir, or ``""``.

    ``settings`` is the primary source (it is what ``.env`` feeds). The direct
    ``os.environ`` read is a belt for exactly one case: the DETACHED provisioner
    is a fresh interpreter, and if its ``Settings`` construction degrades for an
    unrelated field the whole config resets to class defaults -- at which point
    the cache dir would silently move under the running server's feet.
    """
    raw = ""
    with never_raise() as g:
        raw = str(getattr(settings_port.get(), "qa_mobile_cache_dir", "") or "").strip()
    if g.failed:
        raw = ""
    if not raw:
        raw = get_env_str("QA_MOBILE_CACHE_DIR")
    return raw
