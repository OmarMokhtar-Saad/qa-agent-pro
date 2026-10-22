"""Install-time release overlay with backup, rollback and prune (stdlib only).

install.sh runs this file from the freshly extracted release, with ``python -I``,
before the install directory is touched. It never touches ``.env``, ``data/``,
``corpus/``, ``backups/`` or ``.venv``; the overwritten and pruned files are
copied to ``backups/install-<stamp>/`` first and restored if the overlay fails.

    install_apply.py check DEST        exit 0 if DEST may be installed into
    install_apply.py apply SRC DEST    overlay SRC onto DEST
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

PROTECTED = frozenset({".env", "data", "corpus", "backups", ".venv", ".git"})
MARKERS = ("start.sh", "start.cmd", "VERSION", "launcher.py", "MANIFEST.sha256")

Journal = List[Tuple[Path, Optional[Path]]]


def is_install(dest: Path) -> bool:
    """True for an empty/missing dir or one that looks like a qa-agent-pro install."""
    if not dest.exists():
        return True
    if not dest.is_dir():
        return False
    if not any(dest.iterdir()):
        return True
    return any((dest / m).exists() for m in MARKERS)


def _protected(rel: Path) -> bool:
    return bool(rel.parts) and rel.parts[0] in PROTECTED


def _unlock_dirs(dest: Path) -> None:
    for root, dirs, _ in os.walk(dest):
        if Path(root).relative_to(dest).parts[:1] == (".venv",):
            continue
        os.chmod(root, os.stat(root).st_mode | 0o200)


def _save(target: Path, backup: Path, rel: Path) -> Optional[Path]:
    if not target.exists():
        return None
    saved = backup / rel
    saved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, saved)
    return saved


def put_file(src_file: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".qa-new")
    shutil.copy2(src_file, tmp)
    if target.exists():
        os.chmod(target, 0o666)  # Windows refuses to replace a read-only file
    os.replace(tmp, target)


def _rollback(journal: Journal) -> None:
    for target, saved in reversed(journal):
        if saved is None:
            target.unlink(missing_ok=True)
        else:
            shutil.copy2(saved, target)


def _release_files(src: Path) -> List[Path]:
    rels = [p.relative_to(src) for p in src.rglob("*") if p.is_file()]
    return [r for r in rels if not _protected(r)]


def removed_paths(src: Path) -> List[Path]:
    """Safe relative entries of the release's REMOVED_PATHS, absent from the release."""
    f = src / "REMOVED_PATHS"
    lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    rels = [Path(x.strip()) for x in lines if x.strip() and not x.startswith("#")]
    safe = [r for r in rels if not r.is_absolute() and ".." not in r.parts]
    return [r for r in safe if not _protected(r) and not (src / r).exists()]


def _prune(src: Path, dest: Path, backup: Path) -> Journal:
    journal: Journal = []
    for rel in removed_paths(src):
        target = dest / rel
        if target.is_file():
            journal.append((target, _save(target, backup, rel)))
            target.unlink()
    return journal


def apply_release(src: Path, dest: Path) -> Path:
    """Overlay ``src`` onto ``dest``; roll everything back if any step fails."""
    backup = dest / "backups" / time.strftime("install-%Y%m%d-%H%M%S")
    journal: Journal = []
    dest.mkdir(parents=True, exist_ok=True)
    _unlock_dirs(dest)
    try:
        for rel in _release_files(src):
            journal.append((dest / rel, _save(dest / rel, backup, rel)))
            put_file(src / rel, dest / rel)
        journal += _prune(src, dest, backup)
    except BaseException:
        _rollback(journal)
        raise
    return backup


def main(argv: List[str]) -> int:
    if len(argv) == 3 and argv[1] == "check":
        return 0 if is_install(Path(argv[2])) else 2
    if len(argv) == 4 and argv[1] == "apply":
        saved = apply_release(Path(argv[2]), Path(argv[3]))
        sys.stdout.write(f"Previous files saved in {saved}\n")
        return 0
    sys.stderr.write(f"{__doc__}\n")
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
