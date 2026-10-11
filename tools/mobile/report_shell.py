"""The report shell: its file paths, the shell text read once at import and the CSS/JS slicing helpers."""

from __future__ import annotations

import re
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


_SLOT = re.compile(r"\{\{([A-Z_]+)\}\}")


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


def _read_shell() -> str:
    """The shell as shipped: the file minus its commentary and indentation.

    The file is written to be read, and every page used to carry that reading
    -- about 30 KB of design notes per report. Comments are dropped here, ONCE,
    so :data:`SHELL`, :data:`SHELL_STYLE` and :data:`SHELL_SCRIPT` all come
    from the same compacted text and the self-check's identity pins still
    compare like with like. Only whole-line ``//`` comments leave the script
    (a ``//`` inside a line may be a string), and newlines stay so automatic
    semicolon insertion reads the code exactly as written. The one HTML
    comment kept is the source stamp, which carries a slot.
    """
    text = SHELL_PATH.read_text(encoding="utf-8")
    text = re.sub(
        r"<!--(?!\s*Rendered from: \{\{SOURCE_STAMP\}\}).*?-->\n?", "", text, flags=re.S
    )
    head, rest = text.split("<style>", 1)
    css, rest = rest.split("</style>", 1)
    body, rest = rest.split("\n<script>", 1)
    js, tail = rest.split("</script>", 1)
    return (
        head
        + "<style>"
        + _compact_css(css)
        + "</style>"
        + body
        + "\n<script>"
        + _compact_js(js)
        + "</script>"
        + tail
    )


def _between(text: str, open_tag: str, close_tag: str) -> str:
    start = text.index(open_tag) + len(open_tag)
    return text[start : text.index(close_tag, start)]


#: The design, verbatim. Neither constant is ever interpolated into.
SHELL = _read_shell()


SHELL_STYLE = _between(SHELL, "<style>", "</style>")


SHELL_SCRIPT = _between(SHELL, "\n<script>", "</script>")

_CSS_CLASS = re.compile(r"\.(-?[A-Za-z_][\w-]*)")


_CSS_NOT = re.compile(r":not\([^()]*\)")


_CLASS_ATTR = re.compile(r'class="([^"]*)"')


#: Every class the script can PUT on the page: an element's ``className``, a
#: ``classList.add``/``toggle``, and a ``class="..."`` in markup it writes. A class it
#: only queries (``$$('li.seq')``, ``contains('is-' + k)``) needs a rule only when the
#: markup carries it, and then ``live`` has it -- counting every word the script held
#: kept ~3 KB of sequence and exchange rules on pages that draw neither.
_SCRIPT_CLASSES = frozenset(
    token
    for value in re.findall(
        r"className\s*=\s*'([^']*)'"
        r"|classList\.(?:add|toggle)\('([^']*)'"
        r'|class="([^"]*)"',
        SHELL_SCRIPT,
    )
    for part in value
    for token in part.split()
)


def _block_end(css: str, start: int) -> int:
    """Index just past the ``}`` closing the block whose ``{`` is at ``start``."""
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if not depth:
                return i + 1
    return len(css)


def _css_rules(css: str, at: str = "") -> list:
    """``css`` as ``(at_rule, rule)`` pairs in source order; ``@media`` opened."""
    out: list = []
    i = 0
    while True:
        j = css.find("{", i)
        if j < 0:
            return out
        prelude = css[i:j].strip()
        end = _block_end(css, j)
        if prelude.startswith("@media"):
            out += _css_rules(css[j + 1 : end - 1], prelude)
        else:
            out.append((at, prelude + css[j:end].strip()))
        i = end


def _page_style(markup: str) -> str:
    """The shell's rules, in the shell's order, that can match ``markup``.

    The stylesheet styles every kind of run -- the scripted cards, their
    filters and wireframes -- and a page carried all of it: about 36 KB of an
    exploratory page styled nothing on it. A rule stays when one of its
    selectors names only classes the markup carries or the script creates; a selector
    with no class, and every other at-rule, always stays. Nothing is added or
    rewritten, so the page's style is still the shell's text.
    """
    live = {tok for value in _CLASS_ATTR.findall(markup) for tok in value.split()}

    def usable(rule: str) -> bool:
        if rule.startswith("@"):
            return True
        selector = _CSS_NOT.sub("", rule.split("{", 1)[0])
        return any(
            all(
                name in live or name in _SCRIPT_CLASSES
                for name in _CSS_CLASS.findall(branch)
            )
            for branch in selector.split(",")
        )

    out: list = []
    group, rules = None, []
    for at, rule in _css_rules(SHELL_STYLE) + [(None, "")]:
        if at != group:
            if rules:
                out.append(
                    (group + "{" + "\n".join(rules) + "}")
                    if group
                    else "\n".join(rules)
                )
            group, rules = at, []
        if at is not None and usable(rule):
            rules.append(rule)
    return "\n" + "\n".join(out) + "\n"


def _fill(template: str, slots: dict) -> str:
    """Every ``{{SLOT}}`` in ONE pass, so a filled value is never itself expanded."""

    def one(match):
        return slots[match.group(1)]

    return _SLOT.sub(one, template)
