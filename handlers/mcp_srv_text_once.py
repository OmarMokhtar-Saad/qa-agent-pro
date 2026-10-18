"""Send a tool's text to the client once, not twice.

FastMCP wraps a ``-> str`` tool's return into ``structuredContent`` as
``{"result": <text>}`` and advertises an ``outputSchema`` for it, next to the
same text in ``content``. A client that renders both shows the reply twice
(the doctor report did). Every tool here returns prose, so no tool needs the
schema: default it off for ALL registrations, keeping any explicit choice.
"""

from __future__ import annotations

import functools


def single_text_output(mcp) -> None:
    """Make ``mcp.tool`` default to ``output_schema=None`` (text only)."""
    original = mcp.tool

    @functools.wraps(original)
    def tool(*args, **kwargs):
        kwargs.setdefault("output_schema", None)
        return original(*args, **kwargs)

    mcp.tool = tool
