"""Report shell reading: compact the design file's CSS and script, once."""

from __future__ import annotations

import re

from tools.mobile.rshl_paths import SHELL_PATH


def _compact_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    lines = (line.strip() for line in css.splitlines())
    return "\n" + "\n".join(line for line in lines if line) + "\n"


def _compact_js(js: str) -> str:
    # Block comments go too, as in the CSS. The shell's script holds no string
    # or regex with "/*" in it; test_the_page_script_keeps_no_comment binds that.
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    lines = (line.strip() for line in js.splitlines())
    return (
        "\n"
        + "\n".join(line for line in lines if line and not line.startswith("//"))
        + "\n"
    )


def _strip_comments(text: str) -> str:
    """*text* without its HTML comments; the source stamp, which carries a slot, stays."""
    return re.sub(
        r"<!--(?!\s*Rendered from: \{\{SOURCE_STAMP\}\}).*?-->\n?", "", text, flags=re.S
    )


def _assemble(text: str) -> str:
    """*text* with its stylesheet and its script compacted."""
    head, rest = text.split("<style>", 1)
    css, rest = rest.split("</style>", 1)
    body, rest = rest.split("\n<script>", 1)
    js, tail = rest.split("</script>", 1)
    styled = f"{head}<style>{_compact_css(css)}</style>{body}"
    return f"{styled}\n<script>{_compact_js(js)}</script>{tail}"


# The file is written to be read, and every page used to carry that reading
# -- about 30 KB of design notes per report. Comments are dropped here, ONCE,
# so :data:`SHELL`, :data:`SHELL_STYLE` and :data:`SHELL_SCRIPT` all come
# from the same compacted text and the self-check's identity pins still
# compare like with like. Only whole-line ``//`` comments leave the script
# (a ``//`` inside a line may be a string), and newlines stay so automatic
# semicolon insertion reads the code exactly as written. The one HTML
# comment kept is the source stamp, which carries a slot.
def _read_shell() -> str:
    """The shell as shipped: the file minus its commentary and indentation."""
    return _assemble(_strip_comments(SHELL_PATH.read_text(encoding="utf-8")))


def _between(text: str, open_tag: str, close_tag: str) -> str:
    start = text.index(open_tag) + len(open_tag)
    return text[start : text.index(close_tag, start)]
