"""Version stamp of a regenerated suite: earlier versions, overlap, export path."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from agents.host_submit_ports_prep import SubmitPorts
from config.settings_guard import never_raise
from tools import suite_family

logger = logging.getLogger("agents.host_mode")


async def _overlap_text(suite: Any, prev: Any, ports: SubmitPorts) -> str:
    loaded = await ports.io.load_suite(prev.suite_id)
    old = getattr(loaded.get("content"), "test_cases", None) or []
    new = list(getattr(suite, "test_cases", None) or [])
    hits = suite_family.count_overlap(
        [c.title for c in new],
        [c.title for c in old],
        suite_family.OVERLAP_THRESHOLD,
    )
    return suite_family.overlap_line(hits, len(new), prev.version)


class FamilyIn(NamedTuple):
    """What the version stamp needs: the prep, the suite and its stored id."""

    prep: Any
    suite: Any
    saved_id: str


async def stamp_versions(args: FamilyIn, ports: SubmitPorts) -> None:
    """Set ``suite._family_note`` / ``_family_version`` when earlier versions exist."""
    suite, saved_id = args.suite, args.saved_id
    with never_raise(logger, "suite version stamp failed", exc_info=True):
        source = args.prep.source_url or args.prep.source_text
        earlier = await ports.io.earlier_suites(source, saved_id)
        if not earlier:
            return
        fam = await ports.io.family_of(saved_id) or {}
        version = int(fam.get("version") or len(earlier) + 1)
        head = suite_family.versions_line(earlier, version)
        overlap = await _overlap_text(suite, earlier[-1], ports)
        suite._family_version = version
        suite._family_note = f"{head} {overlap}."


async def record_export(saved_id: str, paths: list, ports: SubmitPorts) -> None:
    """Remember the workbook path so a later prepare can point at it."""
    with never_raise(logger, "export path record failed", exc_info=True):
        if paths and saved_id:
            await ports.io.set_export_path(saved_id, str(paths[0]))
