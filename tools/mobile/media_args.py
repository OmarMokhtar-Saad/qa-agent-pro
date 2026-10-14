"""Parameter objects of the media lane (kept apart so every leaf shares them)."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class ClipRef(NamedTuple):
    """Which case and which device one clip belongs to."""

    run_id: str
    tc_id: str
    serial: str


class _PullJob(NamedTuple):
    """Everything ``_finish_step_pull`` needs: device, handle, target, record."""

    serial: str
    held: dict
    target: Path
    clipped: dict


class _FrameJob(NamedTuple):
    """Everything ``_finish_step_frame`` needs: device, folder, screen, record."""

    serial: str
    directory: Path
    screen_id: str
    framed: dict


class _Step(NamedTuple):
    """One submitted step as ``finish_step`` sees it."""

    ref: ClipRef
    held: dict
    directory: Path
    screen_id: str
