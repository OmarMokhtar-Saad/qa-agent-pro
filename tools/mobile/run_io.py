"""Atomic file helpers and id validators for the per-run store.

Every write is tmp-file + ``os.replace``, so a run killed mid-write leaves the
previous good file rather than half a JSON document. A resumable run whose
checkpoint can be truncated is not resumable.

:func:`_write_bytes` is the binary sibling of :func:`write_json`: atomic for
the same reason every other write here is, so a run killed mid-write never
leaves a truncated PNG that a browser renders as a broken image beside a caption
promising a screenshot.

:func:`_write_text` keeps the same tmp + ``os.replace`` + 0600 discipline, with
NO redaction, and stated: :func:`run_redact.redact` is dict-key based and cannot
see free text. The caller (``tools/mobile_evidence/capture.py``) scrubs every
line through the value, key and pair nets BEFORE calling this.

:func:`looks_like_a_run_id` is for deciding whether a string may be shown to a
tester as something they can pass back as ``run_id``. :func:`valid_run_id`
remains the right check for "may this reach the filesystem", and a real run id
satisfies both.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from tools.mobile import paths, run_consts, run_redact

logger = logging.getLogger("tools.mobile.run_store")


def _now(value: float | None) -> float:
    return float(value) if value is not None else time.time()


def valid_run_id(value: object) -> bool:
    return bool(isinstance(value, str) and run_consts._RUN_ID_RE.match(value))


# For deciding whether a string may be shown to a tester as something they can
# pass back as ``run_id``. :func:`valid_run_id` remains the right check for
# "may this reach the filesystem", and a real run id satisfies both.
def looks_like_a_run_id(value: object) -> bool:
    """True when *value* has the shape of a run id this lane MINTS."""
    return bool(isinstance(value, str) and run_consts._RUN_ID_SHAPE_RE.match(value))


def valid_tc_id(value: object) -> bool:
    return bool(isinstance(value, str) and run_consts._TC_ID_RE.match(value))


def _prepare(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.with_name(target.name + ".tmp")


def _commit(tmp: Path, target: Path) -> None:
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def write_json(target: Path, payload: object) -> None:
    tmp = _prepare(target)
    text = json.dumps(run_redact.redact(payload), indent=2, sort_keys=True, default=str)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _commit(tmp, target)


# The binary sibling of :func:`write_json`: tmp file, fsync, ``os.replace``.
#
# Atomic for the same reason every other write here is: a run killed mid-write
# must leave the previous good file rather than a truncated PNG that a browser
# renders as a broken image beside a caption promising a screenshot.
def _write_bytes(target: Path, data: bytes) -> None:
    tmp = _prepare(target)
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _commit(tmp, target)


# The same tmp + ``os.replace`` + 0600 discipline as :func:`write_json`.
#
# NO redaction here, and stated: :func:`redact` is dict-key based and cannot
# see free text. The caller (``tools/mobile_evidence/capture.py``) scrubs every
# line through the value, key and pair nets BEFORE calling this.
def _write_text(target: Path, text: str) -> None:
    tmp = _prepare(target)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(text or ""))
        handle.flush()
        os.fsync(handle.fileno())
    _commit(tmp, target)


def read_json(target: Path) -> Any:
    try:
        if not target.is_file():
            return None
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("mobile.run_store: unreadable %s: %s", target, exc)
        return None


def run_path(run_id: str) -> Path:
    return paths.run_dir(run_id)


# Private names kept for callers outside this wave and for tests that still
# patch them; both delegate at call time so a patch of ``write_json`` /
# ``read_json`` bites on every path.
def _write_json(target: Path, payload: object) -> None:
    write_json(target, payload)


def _read_json(target: Path) -> Any:
    return read_json(target)
