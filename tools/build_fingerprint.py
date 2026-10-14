"""Content fingerprint of the modules that decide what a prep CONTAINS.

F4 (2026-08-15). Host-mode generation is split across two stateless MCP calls,
and the prepared context persists between them in ``data/suites.db`` -- a file
EVERY server process on this machine shares. A prep built by pre-fix code is
therefore reused, unchanged, by a fixed server: the tester restarts the MCP
server, submits against a ``prep_id`` staged minutes earlier, and gets pre-fix
output with nothing saying so. Observed on the TICKET-5645 validation run, where a
prep built before the _strip_html fix (F1) kept feeding placeholder-stripped
ticket text to a server that had been fixed.

``meta.app_version`` cannot see this. It is the installed VERSION string, and a
developer checkout reports the same ``0.1.0`` across every code change, so
``version_skew_note`` -- the existing disclosure this one deliberately mirrors
and reuses -- stays silent through exactly the edits most likely to matter. A
content hash moves whenever the code does.

SCOPE, deliberately narrow: the modules that build, ground, serialize and
validate the prepared context. An exporter or mobile module cannot make a staged
prep stale, so tracking them would only fire the warning on harmless edits.

Design constraints, in order:
  * NEVER raises. A fingerprint is disclosure; a missing one is silence, which
    is exactly today's behaviour, so every failure path returns "".
  * Computed ONCE per process and cached: re-hashing ~1 MB per submit is waste.
  * CONTENT, not mtime or size. A git checkout on a second install has its own
    mtimes, and this must compare two INSTALLS as readily as two builds of one.
  * Stable across platforms: files are read as BYTES and newlines normalised, so
    a checkout with CRLF endings does not read as a different build.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from config.settings_guard import never_raise

logger = logging.getLogger(__name__)

# The install root: this file is <root>/tools/build_fingerprint.py.
_ROOT = Path(__file__).resolve().parent.parent

# Sorted, so the literal's order never matters. A GLOB entry (handlers/**/*.py)
# stands for every file it matches, so new modules there need no edit here.
_TRACKED: tuple[str, ...] = (
    "agents/host_prep_serde*.py",
    "agents/host_submission*.py",
    "agents/host_grounding*.py",
    "agents/host_jobs*.py",
    "agents/host_ac*.py",
    "agents/host_checklist*.py",
    "agents/host_image*.py",
    "agents/host_orchestration*.py",
    "agents/host_category_*.py",
    "agents/host_echo.py",
    "agents/host_prep_merge.py",
    "agents/host_prep_notes.py",
    "agents/host_prep_open.py",
    "agents/host_prep_remap.py",
    "agents/host_prep_relevance_gate.py",
    "agents/host_prep_relevance_verdict.py",
    "agents/host_prep_reply_text.py",
    "agents/host_prep_rtm.py",
    "agents/host_prep_sidecar.py",
    "agents/host_prep_sidecar_keys.py",
    "agents/host_prep_skew.py",
    "agents/host_prep_status_text.py",
    "agents/host_prep_volume.py",
    "agents/host_prep_volume_note.py",
    "agents/host_prep_volume_options.py",
    "agents/host_prep_volume_refusal.py",
    "agents/host_gen_args.py",
    "agents/host_gen_corpus_a.py",
    "agents/host_gen_corpus_b.py",
    "agents/host_gen_hooks.py",
    "agents/host_gen_image_audit.py",
    "agents/host_gen_image_hint.py",
    "agents/host_gen_image_notes.py",
    "agents/host_gen_issue_notes.py",
    "agents/host_gen_jobs.py",
    "agents/host_gen_meta_a.py",
    "agents/host_gen_meta_b.py",
    "agents/host_gen_notes.py",
    "agents/host_gen_pipeline_a.py",
    "agents/host_gen_pipeline_b.py",
    "agents/host_gen_pipeline_c.py",
    "agents/host_gen_preflight.py",
    "agents/host_gen_snapshot_a.py",
    "agents/host_gen_snapshot_b.py",
    "agents/host_gen_types.py",
    "agents/host_submit_ambiguity.py",
    "agents/host_submit_audit.py",
    "agents/host_submit_dup.py",
    "agents/host_submit_dup_notes.py",
    "agents/host_submit_export.py",
    "agents/host_submit_finalize.py",
    "agents/host_submit_gates.py",
    "agents/host_submit_images.py",
    "agents/host_submit_intake.py",
    "agents/host_submit_load.py",
    "agents/host_submit_persist.py",
    "agents/host_submit_persist_notes.py",
    "agents/host_submit_pipeline.py",
    "agents/host_submit_pipeline_post.py",
    "agents/host_submit_pipeline_run.py",
    "agents/host_submit_ports_io.py",
    "agents/host_submit_ports_prep.py",
    "agents/host_submit_ports_text.py",
    "agents/host_submit_push.py",
    "agents/host_submit_push_text.py",
    "agents/host_submit_quality.py",
    "agents/host_submit_read.py",
    "agents/host_submit_reply.py",
    "agents/host_submit_reviews_ac.py",
    "agents/host_submit_reviews_checklist.py",
    "agents/host_submit_reviews_grounding.py",
    "agents/host_submit_sidecar.py",
    "agents/host_submit_types.py",
    "agents/test_scenario_agent.py",
    "agents/scenario_*.py",
    "tools/atomic_checklist*.py",
    "tools/jira_mcp*.py",
    "handlers/**/*.py",
    "tools/prep_store*.py",
    "tools/rtm.py",
    "tools/rtm_*.py",
)

_CACHED: str | None = None


def _expand(rel: str) -> list[str]:
    """``rel`` itself, or every file it matches (posix, sorted) when it is a glob."""
    if "*" in rel:
        return sorted(p.relative_to(_ROOT).as_posix() for p in _ROOT.glob(rel))
    return [rel]


def _hash_file(path: Path) -> str:
    """sha256 of one file's newline-normalised bytes, or "" when unreadable.

    A file that is missing (a distribution build ships a whitelist subset, so
    several of the tracked modules legitimately are not there) contributes ""
    rather than aborting the whole fingerprint -- the same set of files is
    absent on every call within that install, so the value stays stable and
    comparable against another prep from the SAME install.
    """
    try:
        data = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


# A short, stable hash of the prep-shaping modules. "" when unavailable.
#
# Cached for the life of the process, which is also the correct semantics:
# Python does not hot-reload, so the code THIS process is running cannot
# change under it. A running server that is updated on disk keeps reporting
# the build it actually executes, which is the honest answer -- the on-disk
# drift is a separate signal that ``_update_pending_note`` already carries.
def code_fingerprint() -> str:
    """A short, stable hash of the prep-shaping modules; "" if unavailable."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    # defensive; disclosure must not raise
    with never_raise(logger, "code fingerprint unavailable", exc_info=True) as g:
        digest = hashlib.sha256()
        for rel in sorted({f for entry in _TRACKED for f in _expand(entry)}):
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_hash_file(_ROOT / rel).encode("ascii"))
            digest.update(b"\0")
        _CACHED = digest.hexdigest()[:12]
    if g.failed:
        _CACHED = ""
    return _CACHED or ""
