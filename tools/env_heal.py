"""Repair known-stale values in an install's .env (see qa_setup_check).

``updater.migrate_env`` appends missing keys but never rewrites an existing line,
which is the right default for operator config and the reason a stale value can
outlive several releases. This module handles the narrow remainder: a key whose
value is still EXACTLY one the project itself shipped as a default, and which a
later release superseded.

Never raises. Nothing here touches a value the operator chose, or a key they
commented out.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

# key -> (superseded values that MAY be rewritten, new value, one-line reason)
#
# Every entry must name values THIS PROJECT once shipped. Anything else is an
# operator decision and is out of scope by construction.
HEAL_RULES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "QA_MODULE_PREFIX_NORMALIZE_ENABLED": (
        ("false", "0", "no", "off"),
        "true",
        "merges a qualifier-prefixed module label onto its bare variant -- the "
        "2026-08-04 SHYJ-5645 run shipped one feature split 12/85 across "
        "'Cancel order' and 'Sehhaty Store - Cancel order', which breaks "
        "module-based filtering in TestRail/Xray pushes",
    ),
    "JIRA_MAX_PARENT_CHARS": (
        ("1500",),
        "2500",
        "the sibling-story budget is additive, so 1500 squeezed out the parent's "
        "own description",
    ),
    "JIRA_FETCH_COMMENTS": (
        ("false", "0", "no", "off"),
        "true",
        "comments now ride along in the same Jira call -- zero extra round trips",
    ),
    "JIRA_FETCH_IMAGES": (
        ("false", "0", "no", "off"),
        "true",
        "names the screenshots a ticket references instead of silently ignoring them",
    ),
    "JIRA_FETCH_SIBLING_STORIES": (
        ("false", "0", "no", "off"),
        "true",
        "sibling user stories carry requirements this ticket inherits",
    ),
}

_MAX_ENV_BYTES = 1_000_000


def _split(line: str) -> tuple[str, str]:
    """(KEY, value) for an ACTIVE assignment line, ('', '') otherwise."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return "", ""
    key, _, value = stripped.partition("=")
    return key.strip(), value.strip()


def heal_env(install_dir: Path) -> dict:
    """Rewrite superseded default values in ``install_dir/.env``.

    Returns ``{"changed": [(key, old, new, reason), ...], "backup": str,
    "error": None}``. ``changed`` is empty when there is nothing to do, which is
    the normal case. Never raises.
    """
    result: dict = {"changed": [], "backup": "", "error": None}
    try:
        if not bool(getattr(settings, "qa_env_selfheal_enabled", False)):
            return result
        env = Path(install_dir) / ".env"
        if not env.is_file():
            return result
        raw = env.read_text(encoding="utf-8")
        if len(raw.encode("utf-8", "ignore")) > _MAX_ENV_BYTES:
            result["error"] = "the .env is unexpectedly large -- not touching it"
            return result

        lines = raw.splitlines()
        changed: list[tuple[str, str, str, str]] = []
        for idx, line in enumerate(lines):
            key, value = _split(line)
            if not key or key not in HEAL_RULES:
                continue
            superseded, new_value, reason = HEAL_RULES[key]
            if value.lower() not in superseded or value == new_value:
                continue
            # Preserve the original indentation/spacing style of the line.
            lines[idx] = line.replace(f"={value}", f"={new_value}", 1)
            changed.append((key, value, new_value, reason))

        if not changed:
            return result

        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = env.with_name(f".env.bak-{stamp}")
        backup.write_text(raw, encoding="utf-8")
        trailing = "\n" if raw.endswith("\n") else ""
        env.write_text("\n".join(lines) + trailing, encoding="utf-8")
        result["changed"] = changed
        result["backup"] = str(backup)
        logger.info(
            "env self-heal rewrote %d superseded value(s); backup at %s",
            len(changed),
            backup,
        )
        return result
    except Exception as exc:
        logger.exception("env self-heal failed - leaving the .env untouched")
        result["error"] = str(exc)
        return result
