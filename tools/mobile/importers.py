"""The tester's OWN cases: a pasted markdown table, a ``.csv`` or an ``.xlsx``.

Three rules, all of them about honesty:

* **A bad row is REPORTED BY ROW NUMBER, never dropped.** A tester who pastes
  forty cases and gets thirty-one back must be told which nine and why. Silent
  loss is the failure this module exists to prevent.
* **Nothing is invented.** A row with no steps, no title, or no expected result
  anywhere is rejected -- ``TestStep.expected_result`` has a ``min_length`` and
  filling it with "N/A" to get past the validator would be manufacturing a test.
  The ONE accommodation, stated in ``_expected_for``, is a single trailing
  expected result for a multi-step row, which is how testers actually write.
* **The xlsx path is hardened like the dump path.** A ``.xlsx`` is a zip of XML
  supplied by whoever sent the tester the file: member count, declared and
  actual uncompressed size, and the DOCTYPE/ENTITY refusal all apply before a
  parser sees a byte.

An ``.xlsx`` this repo WROTE is readable here (sheet ``Test Cases``, the header
row ``tools/xlsx_generator._HEADERS``, steps as ``1. …\\n2. …``), and so is a
plain sheet with a header row of its own; bidi marks the exporter inserts around
Arabic runs are stripped on the way back in, because they are presentation.
"""

from __future__ import annotations

import logging

from tools.mobile.imp_case import (  # noqa: F401
    _outcome,
    _rejected,
    _resolve_tc_id,
    _row_to_case,
)
from tools.mobile.imp_cells import (  # noqa: F401
    _cell_value,
    _column_index,
    _row_index,
    _strip_ns,
)

# MAX_ROWS is a plain copy for readers; its one live home is imp_consts (the
# readers there look it up qualified, so a patch must target imp_consts).
from tools.mobile.imp_consts import (  # noqa: F401
    _BIDI_RE,
    _CELL_REF_RE,
    _DECL_RE,
    _STEP_SPLIT_RE,
    COLUMN_ALIASES,
    MAX_BYTES,
    MAX_CELL_CHARS,
    MAX_ROWS,
    MAX_STEPS,
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_MEMBERS,
    PRIORITY_LOOKUP,
    SHEET_NAME,
    TRAILING_EXPECTED_NOTE,
    TYPE_LOOKUP,
    UNCOVERED_STEP_NOTE,
)
from tools.mobile.imp_csv import from_csv  # noqa: F401
from tools.mobile.imp_expect import _expected_for  # noqa: F401
from tools.mobile.imp_explain import (  # noqa: F401
    _FIELD_LABELS,
    _explain_validation,
    _problem_detail,
)
from tools.mobile.imp_headers import _map_headers, _split_numbered  # noqa: F401
from tools.mobile.imp_load import load, render_rejections  # noqa: F401
from tools.mobile.imp_md import from_markdown  # noqa: F401
from tools.mobile.imp_member import _member_text  # noqa: F401
from tools.mobile.imp_read import _read_text, _too_big  # noqa: F401
from tools.mobile.imp_sheet import (  # noqa: F401
    _shared_strings,
    _sheet_rows,
    _sheet_xml,
)
from tools.mobile.imp_source import (  # noqa: F401
    _SOURCE_SUFFIXES,
    _maybe_path,
    _source_roots,
    vet_source_path,
)
from tools.mobile.imp_table import (  # noqa: F401
    _assemble,
    _convert_rows,
    _no_cases_message,
)
from tools.mobile.imp_text import _clean, _clean_document, _key  # noqa: F401
from tools.mobile.imp_xlsx import _archive_rows, from_xlsx  # noqa: F401

logger = logging.getLogger(__name__)
