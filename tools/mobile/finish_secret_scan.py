"""Finish-step scan of a run's text artifacts for the secrets it injected (B21).

Text files only (never PNG or video), so binary bytes cannot false-match. The
run's own source records (``manifest.json``, ``cases/``) are skipped: only
artifacts that must never hold a secret (reports, logs, evidence) are read. The
needles are the run's intake registry (``act_scrub``, where C12 puts the goal's
credentials) plus anything the records still name. The answer is ``clean``
or ``file:line`` pointers; the secret value is never echoed. Values under six
characters cannot be told from timestamps and counts, so they are not scanned
and the line says how many were left out.
"""

from __future__ import annotations

from pathlib import Path

from tools.mobile import act_scrub, paths, run_cases, run_consts, run_manifest
from tools.mobile.rtxt_secret import _SECRET_MIN, _run_secrets, short_secret_count

TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".html", ".log", ".txt", ".md", ".xml"})
MAX_BYTES = 2_000_000
MAX_HITS = 5


def _lines(path: Path) -> list[str]:
    if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > MAX_BYTES:
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _is_record(path: Path, root: Path) -> bool:
    """True for the run's own stores, which hold the goal's credential by design."""
    rel = path.relative_to(root)
    return rel.as_posix() == run_consts.MANIFEST_FILE or (
        rel.parts[0] == run_consts.CASES_DIR
    )


def _hits_in(path: Path, root: Path, secrets: tuple[str, ...]) -> list[str]:
    found = []
    for number, line in enumerate(_lines(path), 1):
        if any(secret in line for secret in secrets):
            found.append(f"{path.relative_to(root)}:{number}")
    return found


def _run_records(run_id: str) -> list[object]:
    manifest = (run_manifest.read_manifest(run_id) or {}).get("content")
    cases = (run_cases.list_cases(run_id) or {}).get("content")
    return [manifest, cases]


# C12 masks goal credentials at intake, so the run's own records no longer name
# them; the intake registry (act_scrub, per run) is the needle source then, and
# the records still add anything typed as secret later.
def _needles(run_id: str) -> tuple:
    found = set(_run_secrets(*_run_records(run_id)))
    found.update(v for v in act_scrub.owned(run_id) if len(v) >= _SECRET_MIN)
    return tuple(sorted(found, key=lambda value: (-len(value), value)))


def _short_count(run_id: str) -> int:
    short = {v for v in act_scrub.owned(run_id) if len(v) < _SECRET_MIN}
    return max(len(short), short_secret_count(*_run_records(run_id)))


def _scan(root: Path, secrets: tuple[str, ...]) -> str:
    hits: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if not _is_record(path, root):
            hits.extend(_hits_in(path, root, secrets))
    return ", ".join(hits[:MAX_HITS]) or "clean"


def scan_run(run_id: str) -> str:
    """``clean`` or the first ``file:line`` pointers holding an injected secret."""
    try:
        root = paths.run_dir(run_id)
        secrets = _needles(run_id)
        if not secrets or not root.is_dir():
            return "clean"
        return _scan(root, secrets)
    except (OSError, ValueError):
        return "not scanned"


def _short_note(run_id: str) -> str:
    try:
        count = _short_count(run_id)
    except (OSError, ValueError):
        return ""
    return f"; {count} value(s) under 6 chars NOT checked" if count else ""


# The report can be rendered more than once (next called again in STATE_REPORT);
# the registry is dropped after the first render, so the first verdict is kept
# per run and reused instead of re-scanning with no needles ("clean").
_SCANNED_MAX = 64
_scanned: dict[str, str] = {}


def _remember(run_id: str, line: str) -> str:
    if len(_scanned) >= _SCANNED_MAX:
        _scanned.pop(next(iter(_scanned)))
    _scanned[run_id] = line
    return line


def clear() -> None:
    """Forget every remembered scan line (tests)."""
    _scanned.clear()


def scan_line(run_id: str) -> str:
    """The one packet line: what was scanned and the result (first verdict per run)."""
    if run_id in _scanned:
        return _scanned[run_id]
    return _remember(
        run_id,
        "\n\nSecret scan (report, logs and evidence text; PNG/video and the run's "
        f"own manifest/case records skipped): {scan_run(run_id)}{_short_note(run_id)}",
    )
