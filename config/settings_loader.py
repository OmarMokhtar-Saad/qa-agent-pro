"""Degrade-field-by-field loader for ``Settings`` (QW-14 / I-045 / B-017).

House rule: the settings modules import nothing internal except each other.
``Settings`` is passed in by the ``config.settings`` facade (resolved at call
time there) so this module never imports it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from config.settings_coerce import logger
from config.settings_guard import never_raise

# Bound on the degrade-and-retry loop below. Pydantic reports every field error
# in one pass, so a real environment converges in a single round; the bound only
# exists so a pathological case cannot spin.
_MAX_DEGRADE_ROUNDS = 12


@dataclass
class DegradeState:
    """Fields pinned to their defaults so far, and the init kwargs that do it."""

    overrides: dict[str, object] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)


def offending_fields(exc: ValidationError, cls: Any) -> list[str]:
    """Names of the Settings fields pydantic rejected. Never raises."""
    names: list[str] = []
    # pragma: no cover - defensive
    with never_raise(logger, "could not read validation errors", exc_info=True):
        for err in exc.errors():
            loc = err.get("loc") or ()
            if loc and isinstance(loc[0], str) and loc[0] in cls.model_fields:
                names.append(loc[0])
    return names


def pin_offending_fields(cls: Any, exc: ValidationError, state: DegradeState) -> bool:
    """Pin the not-yet-pinned offending fields to their defaults.

    Returns False when no new offending field could be identified (the
    caller stops retrying), True when at least one field was pinned.
    """
    fresh = [n for n in offending_fields(exc, cls) if n not in state.overrides]
    if not fresh:
        logger.warning(
            "Settings rejected the environment and the offending "
            "field(s) could not be identified (%s).",
            exc,
        )
        return False
    for name in fresh:
        state.overrides[name] = cls.model_fields[name].get_default(
            call_default_factory=True
        )
        state.degraded.append(name)
    return True


def warn_degraded(degraded: list[str]) -> None:
    """Log which fields were reset to their defaults, if any."""
    if degraded:
        logger.warning(
            "Settings: %d field(s) had an unusable value and were reset to "
            "their defaults (%s). Every other field kept its configured "
            "value.",
            len(degraded),
            ", ".join(sorted(degraded)),
        )


def _attempt(cls: Any, state: DegradeState) -> tuple[Any, bool]:
    """One construction try: (loaded or None, whether another round may help)."""
    invalid = None
    # pragma: no cover - defensive backstop
    with never_raise(
        logger, "Settings failed to load (%s).", level=logging.WARNING, with_exc=True
    ):
        try:
            return cls(**state.overrides), False
        except ValidationError as exc:
            invalid = exc
    if invalid is not None:
        return None, pin_offending_fields(cls, invalid, state)
    return None, False


def _fallback(cls: Any, state: DegradeState) -> Any:
    """Last resort: every field back to its class default."""
    logger.warning(
        "Settings could not parse the environment even after resetting %s — "
        "falling back to built-in defaults for ALL fields.",
        ", ".join(sorted(state.degraded)) or "nothing",
    )
    return cls.model_construct()


def load(cls: Any) -> Any:
    """Build ``cls``, pinning each offending field to its default and retrying.

    Init kwargs outrank the environment, so a bad value costs one field;
    ``model_construct()`` is the last resort (I-045 / B-017).
    """
    state = DegradeState()
    for _ in range(_MAX_DEGRADE_ROUNDS):
        loaded, again = _attempt(cls, state)
        if loaded is not None:
            warn_degraded(state.degraded)
            return loaded
        if not again:
            break
    return _fallback(cls, state)
