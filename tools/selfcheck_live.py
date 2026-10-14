"""Live env-name allow-list of the self-check and the guard that keeps it honest."""

from __future__ import annotations

import re
from typing import Any

#: Env vars that are legitimately named in tester-facing text and are NOT
#: ``Settings`` fields. This is the canonical copy --
#: ``tests/test_no_deleted_flag_in_output.py`` imports it rather than keeping a
#: second list, because two copies of one allow-list is the rot site a
#: self-audit is most likely to grow.
#:
#: Every name here must be justified by :func:`classify_live_env`, which is what
#: stops the list becoming a place to silence a finding.
LIVE_ENV: frozenset = frozenset(
    {
        "QA_MCP_ENABLED",
        "QA_EXPORT_DIR",
        "QA_DIST_MODE",
        "QA_UPDATE_REPO",
        "QA_POSTHOG_KEY",
        "QA_FORCE",
        "QA_DRIFT_RESTART_ENABLED",
        "QA_DRIFT_CHECK_SECONDS",
        "QA_INSTALL_DIR",
        "QA_STAGING_DIR",
        "QA_PY",
        "QA_UPDATE_INTERVAL_MINUTES",
        "QA_LOG_RETENTION_ENABLED",
        "QA_LOG_RETENTION_DAYS",
        "QA_LOG_RETENTION_MAX_FILES",
        "API_AUTH_TOKEN",
        "GITHUB_TOKEN",
    }
)

#: The residue: names in :data:`LIVE_ENV` that are neither a ``Settings`` field
#: nor read through ``os.environ`` in this tree, each with the reason it is
#: still legitimate. Count-pinned by the tests in BOTH directions, exactly like
#: the static scan's ``RETAINED`` table. This is the one circular corner of the
#: allow-list -- the string that names ``API_AUTH_TOKEN`` is what keeps
#: ``API_AUTH_TOKEN`` exempt -- so it is kept small, named and enumerated rather
#: than hidden inside the set above.
ENV_RESIDUAL: dict = {
    "API_AUTH_TOKEN": (
        "the TESTER exports this to run the generated Java suite; this server "
        "never receives, stores or writes it, and saying so is the point of "
        "the strings that name it"
    ),
    "QA_FORCE": "read by the installer/launcher shell templates, not by Python",
    "QA_INSTALL_DIR": "installer/launcher shell template variable",
    "QA_PY": "installer/launcher shell template variable",
}


def live_names(settings_cls: Any) -> frozenset:
    """Every env name a reply may legitimately tell a tester to set."""
    fields = getattr(settings_cls, "model_fields", {}) or {}
    return frozenset({str(name).upper() for name in fields}) | LIVE_ENV


def _env_reads(source_text: str) -> str:
    return "\n".join(
        re.findall(r"(?:environ|getenv|_setting|_env)[^\n]{0,120}", source_text)
    )


def _classify_one(name: str, fields: set, reads: str) -> str:
    if name in fields:
        return "field"
    if re.search(rf"\b{re.escape(name)}\b", reads):
        return "environ"
    if name in ENV_RESIDUAL:
        return "residual"
    return "unjustified"


# ``{name: "field" | "environ" | "residual" | "unjustified"}``.
#
# The anti-rot guard for :data:`LIVE_ENV` itself. ``source_text`` is the
# shipped Python source, supplied by the caller (this module reads no tree of
# its own): a name counts as ``environ`` when it appears within 120 characters
# of an environment read. Anything left must be listed in
# :data:`ENV_RESIDUAL` with a reason, or it is ``unjustified`` and the tests
# fail.
def classify_live_env(settings_cls: Any, source_text: str) -> dict:
    """``{name: "field" | "environ" | "residual" | "unjustified"}``."""
    fields = {
        str(name).upper() for name in (getattr(settings_cls, "model_fields", {}) or {})
    }
    reads = _env_reads(source_text)
    return {name: _classify_one(name, fields, reads) for name in sorted(LIVE_ENV)}
