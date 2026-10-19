"""Attribute values of a view-tree dump, whichever quote the dumper wrote.

uiautomator writes ``text="..."``; the fast accessibility reader writes
``text='...'``. A reader that knows only one quote sees nothing at all on the
other's screens, so every attribute read by pattern goes through here.
"""

from __future__ import annotations

import html
import re


def attr_pattern(name: str) -> re.Pattern[str]:
    """The pattern of attribute *name* in either quote style."""
    return re.compile(r"""(?:^|\s)%s=(?:"([^"]*)"|'([^']*)')""" % re.escape(name))


def attr_values(pattern: re.Pattern[str], xml: str) -> list[str]:
    """Every value of *pattern*'s attribute in *xml*, entities decoded."""
    return [html.unescape(dq or sq) for dq, sq in pattern.findall(xml or "")]
