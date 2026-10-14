"""The command line of the report self-check (stdout IS the product)."""

from __future__ import annotations

import sys

from tools.mobile.rchk_consts import USAGE
from tools.mobile.rchk_run import check


def _parse_args(argv: list | None) -> tuple:
    """``(run_id, html_path)`` from *argv* (``sys.argv[1:]`` when ``None``)."""
    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    run_id = ""
    html_path = ""
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--html" and index + 1 < len(args):
            html_path = args[index + 1]
            index += 2
            continue
        if not run_id and not item.startswith("-"):
            run_id = item
        index += 1
    return run_id, html_path


def _print_result(body: dict) -> None:
    for entry in body.get("pins") or []:
        sys.stdout.write(
            ("PASS " if entry.get("ok") else "FAIL ")
            + str(entry.get("name"))
            + " - "
            + str(entry.get("detail"))
            + "\n"
        )
    sys.stdout.write(
        ("OK " if body.get("ok") else "FAILED ")
        + str(body.get("path"))
        + " ("
        + str(body.get("cards"))
        + " cards, "
        + str(body.get("bytes"))
        + " bytes)\n"
    )


def main(argv: list | None = None) -> int:
    """``0`` when every pin passed, ``1`` on any failure, ``2`` on bad usage."""
    run_id, html_path = _parse_args(argv)
    if not run_id:
        sys.stdout.write(USAGE)
        return 2
    result = check(run_id, html_path)
    if result.get("error"):
        sys.stdout.write("REFUSED " + str(result["error"])[:400] + "\n")
        return 1
    body = result.get("content") or {}
    _print_result(body)
    return 0 if body.get("ok") else 1
