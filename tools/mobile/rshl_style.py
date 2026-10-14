"""Report shell style: the shell text, its CSS and script, and the page-style filter."""

from __future__ import annotations

import re

from tools.mobile.rshl_read import _between, _read_shell

_SLOT = re.compile(r"\{\{([A-Z_]+)\}\}")


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


def _usable(rule: str, live: set) -> bool:
    """Whether *rule* can match: some selector names only classes the page or its script carries."""
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


def _wrap(group: str | None, rules: list) -> str:
    """One group's rules as CSS text: wrapped in its at-rule, or bare."""
    body = "\n".join(rules)
    return (group + "{" + body + "}") if group else body


def _group_rules(live: set) -> list:
    """The shell's usable rules, grouped by at-rule, in the shell's order."""
    out: list = []
    group: str | None = None
    rules: list = []
    for at, rule in _css_rules(SHELL_STYLE) + [(None, "")]:
        if at != group:
            if rules:
                out.append(_wrap(group, rules))
            group, rules = at, []
        if at is not None and _usable(rule, live):
            rules.append(rule)
    return out


# The stylesheet styles every kind of run -- the scripted cards, their
# filters and wireframes -- and a page carried all of it: about 36 KB of an
# exploratory page styled nothing on it. A rule stays when one of its
# selectors names only classes the markup carries or the script creates; a selector
# with no class, and every other at-rule, always stays. Nothing is added or
# rewritten, so the page's style is still the shell's text.
def _page_style(markup: str) -> str:
    """The shell's rules, in the shell's order, that can match ``markup``."""
    live = {tok for value in _CLASS_ATTR.findall(markup) for tok in value.split()}
    return "\n" + "\n".join(_group_rules(live)) + "\n"


def _fill(template: str, slots: dict) -> str:
    """Every ``{{SLOT}}`` in ONE pass, so a filled value is never itself expanded."""

    def one(match):
        return slots[match.group(1)]

    return _SLOT.sub(one, template)
