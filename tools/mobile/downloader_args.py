"""Argument objects of the downloader: one request, and one fetch in flight."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadRequest:
    """The arguments of one ``download`` call."""

    url: str
    dest: str | Path
    sha256: str
    payload_bytes: int = 0
    progress_path: str | Path | None = None
    phase: str = "download"


@dataclass(frozen=True)
class FetchJob:
    """What one fetch needs: the URL, the ``.part`` file, and the progress sink.

    *base* holds the progress fields that do not change mid-download.
    """

    url: str
    part: Path
    progress_path: str | Path | None
    base: dict
