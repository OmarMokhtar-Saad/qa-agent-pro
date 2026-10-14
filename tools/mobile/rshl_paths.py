"""Report shell paths: the report folder layout and the design file's location."""

from __future__ import annotations

from pathlib import Path

from tools.mobile import media, paths

#: The report is a self-contained FOLDER, not a file: ``index.html`` beside the
#: ``media/`` its own markup references RELATIVELY, so the folder can be zipped
#: and opened anywhere. ``REPORT_FILE`` is gone rather than kept as an alias --
#: one name for one artifact, and an alias is how two answers start.
REPORT_DIR = media.REPORT_DIRNAME


INDEX_FILE = "index.html"


MEDIA_DIR = media.MEDIA_DIRNAME


def report_path(run_id: str) -> Path:
    """``runs/<run_id>/report/index.html``. Pure; creates nothing."""
    return paths.run_dir(str(run_id)) / REPORT_DIR / INDEX_FILE


#: The design file. Read once, at import: a shell that cannot be read is a
#: broken install, and it should fail loudly here rather than on the first run.
SHELL_PATH = Path(__file__).with_name("report_shell.html")
