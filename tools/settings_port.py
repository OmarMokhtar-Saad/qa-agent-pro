"""Settings port: the one door to the process settings object.

Call through the module (``settings_port.get().jira_base_url``), never bind the
object at import time. ``get()`` returns the current object at call time;
``use(obj)`` swaps what ``get()`` returns (a fresh ``Settings``, a stand-in);
``override(**fields)`` sets fields on the current object and restores them.

``override`` semantics: an unknown field raises ``ValueError`` before anything
is changed; on exit the PREVIOUS values are restored in reverse order (LIFO)
together with ``model_fields_set``, also when the body raised. Do not mix
``override`` and ``monkeypatch.setattr(settings, ...)`` on one field in one
test: teardown order would decide the result.

The state is process-global, not task-local. That is right for pytest (xdist
workers are processes) and forbidden from worker threads.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from config.settings import Settings, settings


class _State:
    """The current settings object and the number of active swaps."""

    current: Settings = settings
    depth: int = 0


_STATE = _State()


def get() -> Settings:
    """The current settings object (resolved at call time)."""
    return _STATE.current


def pristine() -> bool:
    """True when no ``use``/``override`` is active (leak check for tests)."""
    return _STATE.depth == 0 and _STATE.current is settings


@contextmanager
def use(obj: Settings) -> Iterator[Settings]:
    """Make ``get()`` return ``obj`` inside the block, then put it back."""
    previous = _STATE.current
    _STATE.current = obj
    _STATE.depth += 1
    try:
        yield obj
    finally:
        _STATE.current = previous
        _STATE.depth -= 1


def _check_fields(cfg: Settings, names: list[str]) -> None:
    unknown = [n for n in names if n not in type(cfg).model_fields]
    if unknown:
        raise ValueError(f'"{type(cfg).__name__}" object has no field "{unknown[0]}"')


def _restore(cfg: Settings, saved: dict, fields_set: set) -> None:
    for name in reversed(list(saved)):
        setattr(cfg, name, saved[name])
    cfg.__pydantic_fields_set__.clear()
    cfg.__pydantic_fields_set__.update(fields_set)


@contextmanager
def override(**fields: object) -> Iterator[Settings]:
    """Set ``fields`` on the current object for the block (LIFO restore)."""
    cfg = _STATE.current
    _check_fields(cfg, list(fields))
    saved = {n: object.__getattribute__(cfg, n) for n in fields}
    fields_set = set(cfg.model_fields_set)
    _STATE.depth += 1
    try:
        for name, value in fields.items():
            setattr(cfg, name, value)
        yield cfg
    finally:
        _restore(cfg, saved, fields_set)
        _STATE.depth -= 1
