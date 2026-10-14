"""The preflight block: failures first, each with its fix.

Ninth layer of the ``render_*`` split of ``render.py``.

``preflight_block`` -- the preflight checks, failures first, each with its fix.

*rendered* is ``preflight.render``'s output, passed in rather than imported so
this module stays free of internal imports and the checks have exactly one
renderer.
"""

from __future__ import annotations


def _must_fix(body: dict) -> list:
    failing = list(body.get("failing") or [])
    # Count only what BLOCKS. `blocking` / `advisory` come from preflight.check;
    # a caller that supplies neither (older shape) keeps the old count.
    blocking = body.get("blocking")
    return list(blocking) if isinstance(blocking, list) and blocking else failing


def _advisory(body: dict) -> list:
    advisory = body.get("advisory")
    return list(advisory) if isinstance(advisory, list) else []


def _head(body: dict, must_fix: list, note: str) -> str:
    return (
        "## Preflight — all clear" + note + "\n\n"
        if body.get("ok")
        else "## Preflight — "
        + str(len(must_fix))
        + " check(s) must be fixed first"
        + note
        + "\n\n"
    )


def _tail(body: dict, advisory: list) -> str:
    return (
        ""
        if body.get("ok")
        else "\n\nFix the ❌ items above and call `qa_mobile_test` again."
        + (" The ⚠️ advisory items do not stop a run." if advisory else "")
    )


def preflight_block(content: object, rendered: str = "") -> str:
    """The preflight checks, failures first, each with its fix."""
    body = content if isinstance(content, dict) else {}
    must_fix = _must_fix(body)
    advisory = _advisory(body)
    note = (
        " (" + str(len(advisory)) + " advisory note(s), none of them stops a run)"
        if advisory
        else ""
    )
    head = _head(body, must_fix, note)
    return head + (str(rendered) or "(no checks were produced)") + _tail(body, advisory)
