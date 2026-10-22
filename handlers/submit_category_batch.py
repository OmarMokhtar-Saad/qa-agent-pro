"""Batch form of qa_submit_category: many categories in one call.

``categories`` is a list of ``{"category_name": ..., "suite_json": ...}``
entries (``name`` / ``test_cases`` accepted as aliases). Each entry goes through
the single-category handler unchanged, in order, so every refusal, shrink guard
and normalisation still applies per category. One failing entry (a refusal or an
exception) never blocks the others; the replies are joined under a per-category header.
"""

from __future__ import annotations

import json
import logging
from typing import NamedTuple

from agents.host_submission import safe_prep_id
from handlers import _common
from handlers.submit_category_record import handle_submit_category

logger = logging.getLogger("handlers.submit_category_batch")

_BAD_LIST = "`categories` must be a non-empty list of {category_name, suite_json}."


class Opts(NamedTuple):
    progress: _common.ProgressCb = None
    replace_smaller: bool = False


def _entry_fields(item: object) -> tuple[str, object] | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("category_name") or item.get("name") or "").strip()
    body = item.get("suite_json")
    if body in (None, "") and "test_cases" in item:
        body = {"test_cases": item["test_cases"]}
    return name, "" if body is None else body


def _as_list(categories: object) -> list | str:
    if isinstance(categories, str):
        try:
            categories = json.loads(categories)
        except ValueError:
            return _BAD_LIST
    if not isinstance(categories, list) or not categories:
        return _BAD_LIST
    return categories


async def _one(prep_id: str, item: object, opts: Opts) -> str:
    fields = _entry_fields(item)
    if fields is None:
        return "entry is not an object with `category_name` and `suite_json`."
    name, body = fields
    try:
        return await handle_submit_category(
            prep_id,
            name,
            body,
            progress=opts.progress,
            replace_smaller=opts.replace_smaller,
        )
    except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the rest
        logger.exception("batch category %r failed", name)
        return f"❌ This category was NOT staged: {type(exc).__name__}: {exc}"


def _label(item: object) -> str:
    if not isinstance(item, dict):
        return "?"
    return str(item.get("category_name") or item.get("name") or "?")


async def handle_submit_categories(
    prep_id: str, categories: object, opts: Opts = Opts()
) -> str:
    """Stage every listed category; return one reply per entry, in order."""
    prep_id = safe_prep_id(prep_id)
    items = _as_list(categories)
    if isinstance(items, str):
        return items
    parts = []
    for n, item in enumerate(items, 1):
        reply = await _one(prep_id, item, opts)
        parts.append(f"### Category {n}/{len(items)}: {_label(item)}\n{reply}")
    return "\n\n".join(parts)


async def _refuse_mixed() -> str:
    return (
        "❌ Nothing was staged: send EITHER `categories` (a list) OR "
        "`category_name` + `suite_json`, not both."
    )


def route(categories: object, single: tuple, opts: Opts):
    """Batch when `categories` is given, else the single-category call."""
    prep_id = single[0]
    if categories and (single[1] or single[2]):
        return _refuse_mixed()
    if categories:
        return handle_submit_categories(prep_id, categories, opts)
    return handle_submit_category(
        *single, progress=opts.progress, replace_smaller=opts.replace_smaller
    )
