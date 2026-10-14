"""The report shell: its file paths, the shell text read once at import and the CSS/JS slicing helpers."""

from __future__ import annotations

from tools.mobile.rshl_paths import (  # noqa: F401
    INDEX_FILE,
    MEDIA_DIR,
    REPORT_DIR,
    SHELL_PATH,
    report_path,
)
from tools.mobile.rshl_read import (  # noqa: F401
    _between,
    _compact_css,
    _compact_js,
    _read_shell,
)
from tools.mobile.rshl_style import (  # noqa: F401
    _SCRIPT_CLASSES,
    SHELL,
    SHELL_SCRIPT,
    SHELL_STYLE,
    _css_rules,
    _fill,
    _page_style,
)
