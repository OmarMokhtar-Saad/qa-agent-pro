"""``qa_mobile_knowledge``: read and manage ONE app's saved knowledge (contract 1.10).

Sync, called from ``mcp_handlers.handle_mobile_knowledge`` inside ``to_thread``
with the package already resolved. Returns ``str`` and never raises. Wave 1
implements ``list`` and ``show`` for every table; the other actions reply "not
implemented yet" until W2d fills them. Stored text is only echoed through
``wrap_untrusted``; ``reason``, ``text`` and ``payload`` are refused when they
hold a secret.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
import re  # noqa: F401
import sqlite3  # noqa: F401
from contextlib import closing  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import (  # noqa: F401
    app_knowledge,
    app_store,
    knowledge_limits,
    knowledge_reflect,
    knowledge_schema,
    run_store,
)
from tools.mobile import knowledge_db as kdb  # noqa: F401
from tools.mobile import knowledge_gate as gate  # noqa: F401
from tools.mobile import knowledge_plug_locators as plug_locators  # noqa: F401
from tools.mobile import knowledge_stage_popups as popups_stage  # noqa: F401
from tools.mobile.ktool_act import _act, _intake
from tools.mobile.ktool_args import (  # noqa: F401
    Ask,
    Edit,
    Found,
    Page,
    Parsed,
    Raw,
    Retire,
    Ruling,
    Scope,
)
from tools.mobile.ktool_core import (  # noqa: F401
    _EDITABLE,
    _ROW_CHARS,
    _SQL_ERRORS,
    _WARN,
    ACTIONS,
    TABLES,
    _clone,
    _refuse,
    _row_of,
    _said,
    _target,
    _write,
)
from tools.mobile.ktool_export import _EXPORT_STATUS, _export  # noqa: F401
from tools.mobile.ktool_imp_rules import (  # noqa: F401
    _EVIDENCE,
    _EVIDENCE_NUMERIC,
    _IMPORT_SHARE,
    _IMPORTABLE,
    _JSON_SHAPES,
    _SKIP_IMPORT,
    _import_note,
    _popup_consistent,
    _strict_dumps,
)
from tools.mobile.ktool_import import _import, _import_room, _import_row  # noqa: F401
from tools.mobile.ktool_import_learned import _import_learned  # noqa: F401
from tools.mobile.ktool_read import (  # noqa: F401
    _read_rows,
    _render_row,
    _screen_inputs,
    _show,
    list_table,
    notes_list,
    notes_retire,
)
from tools.mobile.ktool_reflect import (  # noqa: F401
    _package_of,
    _pending,
    _reflect,
    _review,
    _submit,
)
from tools.mobile.ktool_resolve import _apply_proposal, _resolve  # noqa: F401
from tools.mobile.ktool_write import _confirm, _edit, _reject, _rollback  # noqa: F401
from tools.untrusted import single_line, wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)


def handle(ask: Ask) -> str:
    """``ask.data`` carries the action fields: table, reason, text, verdict, payload, page."""
    try:
        got = _intake(ask)
        if isinstance(got, str):
            return got
        verb, package = got.ask.action, got.ask.package
        if verb == "list":
            return list_table(package, got.table, got.extra.get("page") or 0)
        if verb == "show":
            return _show(package, got.table, got.ask.item_id)
        return _act(got.ask, got.extra)
    except Exception:
        logger.exception("qa_mobile_knowledge failed")
        return _WARN + "The knowledge store could not be read."
