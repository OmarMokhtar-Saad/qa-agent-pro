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

import csv
import io
import logging
import pathlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from tools.models import Priority, TestCase, TestStep, TestType

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500
MAX_CELL_CHARS = 8000
MAX_STEPS = 40

# xlsx hardening
MAX_ZIP_MEMBERS = 200
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024

_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_STEP_SPLIT_RE = re.compile(r"(?m)^\s*(\d{1,3})\s*[.)\-:]\s+")
_BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩]")
_CELL_REF_RE = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")

SHEET_NAME = "Test Cases"

# Every alias is compared case-insensitively with runs of non-alphanumerics
# collapsed, so "TC ID", "tc_id" and "TC-Id" are one column.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "tc_id": ("tcid", "id", "caseid", "testcaseid", "tc"),
    "title": ("title", "name", "summary", "testcase", "scenario"),
    "module": ("module", "feature", "area", "component"),
    "priority": ("priority", "severity"),
    "type": ("testtype", "type", "category"),
    "preconditions": ("preconditions", "precondition", "setup", "pre"),
    "steps": ("stepsactions", "steps", "step", "actions", "action", "teststeps"),
    "expected": (
        "expectedresults",
        "expectedresult",
        "expected",
        "result",
        "results",
        "expectedoutcome",
    ),
}

#: For a step the tester simply did not write an outcome for. Distinct from
#: TRAILING_EXPECTED_NOTE, which POINTS at the final step: on the partial-list
#: path the final step carries a note too, so re-using it sent the reader to a
#: step that answers nothing.
UNCOVERED_STEP_NOTE = "No expected result was supplied for this step."

TRAILING_EXPECTED_NOTE = (
    "See the expected result recorded on the final step of this case."
)

PRIORITY_LOOKUP = {p.value.lower(): p for p in Priority}
TYPE_LOOKUP = {t.value.lower(): t for t in TestType}


# Tester-facing name and source column for each model field a row can violate.
# Read off ``tools/models.py`` rather than guessed: BOTH ``TestStep.action`` and
# ``TestStep.expected_result`` carry ``min_length=5``, ``TestCase.title`` is
# 10..250 and ``TestCase.module`` 2..100. That contract is shared with the whole
# generation pipeline and is NOT relaxed here -- a row that cannot satisfy it is
# refused by name, never padded.
_FIELD_LABELS: dict[str, tuple[str, str]] = {
    "expected_result": ("expected result", "Expected Results"),
    "action": ("step action", "Steps / Actions"),
    "step_number": ("step number", "Steps / Actions"),
    "title": ("title", "Title"),
    "module": ("module", "Module"),
    "tc_id": ("case id", "TC ID"),
    "priority": ("priority", "Priority"),
    "type": ("test type", "Test Type"),
}


def _explain_validation(exc: Exception) -> str:
    """A pydantic ValidationError in words a manual tester can act on.

    Before this existed a rejected row read ``1 validation error for TestStep``
    -- technically true and useless, because it named neither the column, nor
    the step, nor the rule. That broke this module's own promise that a bad row
    is reported WITH ITS REASON: a tester whose table said ``Expected: OK`` was
    told only that something, somewhere, was invalid.

    The rule itself is not relaxed and the value is not padded. An expected
    result really does need five characters, and inventing one to get past the
    validator would manufacture an assertion the tester never wrote.
    """
    reporter = getattr(exc, "errors", None)
    fallback = str(exc).splitlines()[0][:200]
    if not callable(reporter):
        return fallback
    try:
        found = list(reporter())
    except Exception:  # pragma: no cover - defensive
        return fallback
    if not found:
        return fallback
    problem = found[0]
    location = list(problem.get("loc") or ())
    field = next((str(p) for p in reversed(location) if isinstance(p, str)), "")
    step_index = next((int(p) for p in location if isinstance(p, int)), -1)
    label, column = _FIELD_LABELS.get(field, (field or "value", ""))
    where = "step " + str(step_index + 1) + "'s " if step_index >= 0 else ""
    context = problem.get("ctx") or {}
    kind = str(problem.get("type") or "")
    if kind == "string_too_short":
        detail = (
            where
            + label
            + " needs at least "
            + str(context.get("min_length"))
            + " characters"
        )
    elif kind == "string_too_long":
        detail = (
            where
            + label
            + " is longer than "
            + str(context.get("max_length"))
            + " characters"
        )
    elif kind == "string_pattern_mismatch":
        detail = where + label + " is not in the expected format"
    elif kind == "missing":
        detail = where + label + " is missing"
    elif kind == "too_short":
        detail = where + label + " has no entries"
    else:
        detail = where + label + ": " + str(problem.get("msg") or "")[:120]
    if column:
        detail += " (column: " + column + ")"
    return detail


def _maybe_path(text: str) -> "pathlib.Path | None":
    """*text* as an existing file, or None. NEVER raises.

    ``Path(text).is_file()`` raises ``OSError`` for input that is not
    path-shaped -- ENAMETOOLONG on a long component, ENOTDIR, embedded NUL --
    and a ``len(text) < 4096`` guard does NOT prevent it, because the limit is
    per COMPONENT (255 bytes on macOS), not per path. A 469-character pasted
    table raised ENAMETOOLONG, the outer handler reported "Could not read that
    source", and ``load()`` therefore refused EVERY markdown paste while
    ``from_markdown`` handled the same text fine. A probe for "is this a file?"
    must answer False, not detonate.
    """
    candidate = str(text or "")
    if not candidate or "\n" in candidate or "\r" in candidate or "\x00" in candidate:
        return None
    if len(candidate) > 4096:
        return None
    try:
        resolved = pathlib.Path(candidate).expanduser()
        return resolved if resolved.is_file() else None
    except (OSError, ValueError):
        return None


def _clean_document(text: object) -> str:
    """Normalise a WHOLE pasted document. Never truncates.

    ``_clean`` is a per-CELL helper and caps at ``MAX_CELL_CHARS``. Running it
    over an entire paste silently discarded everything past 8,000 characters:
    a 299-row table arrived as 100 rows and the other 199 were never mentioned
    -- silent row loss, which is the one thing this module exists to prevent.
    The document-level cap is ``MAX_BYTES`` and it REFUSES rather than trims.
    """
    body = text if isinstance(text, str) else ("" if text is None else str(text))
    return _BIDI_RE.sub("", body).replace("\r\n", "\n").replace("\r", "\n").strip()


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean(value: object) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = _BIDI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS]
    return text.strip()


def _map_headers(header_row: list[str]) -> dict[str, int]:
    """Column name -> index. Case-insensitive, first match wins."""
    mapping: dict[str, int] = {}
    for index, raw in enumerate(header_row):
        key = _key(raw)
        if not key:
            continue
        for field, aliases in COLUMN_ALIASES.items():
            if field in mapping:
                continue
            if key in aliases:
                mapping[field] = index
                break
    return mapping


def _split_numbered(text: str) -> list[str]:
    """``"1. a\\n2. b"`` -> ``["a", "b"]``; unnumbered lines stay one per line."""
    body = _clean(text)
    if not body:
        return []
    parts = _STEP_SPLIT_RE.split(body)
    if len(parts) > 1:
        out = []
        # split() yields [pre, num, text, num, text, ...]
        for index in range(1, len(parts) - 1, 2):
            piece = _clean(parts[index + 1])
            if piece:
                out.append(piece)
        if out:
            return out[:MAX_STEPS]
    plain = (_clean(piece) for piece in body.split("\n"))
    return [line for line in plain if line][:MAX_STEPS]


def _expected_for(index: int, expected: list[str], total: int) -> str:
    """Which expected result belongs to step *index*.

    Four shapes, all four stated because the fourth was missing and wrong:
    counts equal, ONE outcome, a partial list, and MORE outcomes than steps
    (folded onto the last step -- never dropped).

    Aligned one-to-one when the counts match. When a row carries FEWER expected
    results than steps -- overwhelmingly the common case, because a tester writes
    "do this, then this, then this, and X should happen" -- the LAST expected
    result lands on the LAST step and the earlier steps carry an explicit,
    honest pointer. It is a placeholder, and it says so; it is not a fabricated
    assertion, and no step is silently given a result it does not have.
    """
    if not expected:
        return ""
    if len(expected) == total:
        return expected[index]
    if len(expected) == 1:
        # The documented accommodation, and the ONLY coherent reading of it:
        # the one stated outcome belongs to the LAST step. The old code reached
        # its ``index < len(expected)`` fallback first, so step 1 AND the last
        # step both claimed this text while the steps between them carried the
        # placeholder -- the same assertion attached twice, which reads to a
        # tester as two separate verifications of one outcome.
        return expected[0] if index == total - 1 else TRAILING_EXPECTED_NOTE
    if len(expected) > total:
        # MORE outcomes than steps. This used to fall through to the partial-list
        # branch below and return expected[0], which was doubly wrong: the
        # surplus outcomes vanished with no entry in `rejected` and no
        # `truncated` flag, and the step asserted the FIRST outcome rather than
        # the one the case was written to verify. Silent loss of
        # tester-authored content is the one thing this module promises not to
        # do (see the module docstring).
        #
        # Folding the tail onto the last step keeps every outcome the tester
        # wrote and keeps them in order. Rejecting the whole row was the other
        # candidate and is worse for a habit this ordinary: a tester who writes
        # one prose step and three numbered outcomes has said something
        # perfectly clear.
        if index == total - 1:
            return "; ".join(expected[index:])
        return expected[index]
    # A partial list (1 < len < total) aligns from the START, which is the only
    # sane reading of "they wrote the first few and stopped"; the uncovered
    # tail carries the pointer rather than a borrowed assertion.
    if index < len(expected):
        return expected[index]
    return UNCOVERED_STEP_NOTE


def _row_to_case(
    row: list[str], mapping: dict[str, int], row_number: int, fallback_index: int
) -> dict:
    """``{"case": TestCase|None, "reject": {...}|None, "renumbered": bool}``."""

    def cell(field: str) -> str:
        index = mapping.get(field)
        if index is None or index >= len(row):
            return ""
        return _clean(row[index])

    title = cell("title")
    steps_text = cell("steps")
    expected_text = cell("expected")

    if not any(cell(name) for name in COLUMN_ALIASES):
        return {"case": None, "reject": None, "renumbered": False}
    if not title:
        return {
            "case": None,
            "reject": {"row": row_number, "why": "no title", "title": ""},
            "renumbered": False,
        }
    steps = _split_numbered(steps_text)
    if not steps:
        return {
            "case": None,
            "reject": {"row": row_number, "why": "no steps", "title": title[:80]},
            "renumbered": False,
        }
    expected = _split_numbered(expected_text)
    if not expected:
        return {
            "case": None,
            "reject": {
                "row": row_number,
                "why": "no expected result",
                "title": title[:80],
            },
            "renumbered": False,
        }

    raw_id = cell("tc_id")
    digits = re.sub(r"\D", "", raw_id)
    renumbered = False
    if digits and 1 <= len(digits) <= 6:
        tc_id = "TC-" + digits.zfill(3)
        renumbered = tc_id != raw_id.strip().upper()
    else:
        tc_id = "TC-" + str(fallback_index).zfill(3)
        renumbered = True

    module = cell("module") or "Imported cases"
    priority = PRIORITY_LOOKUP.get(cell("priority").lower(), Priority.MEDIUM)
    test_type = TYPE_LOOKUP.get(cell("type").lower(), TestType.FUNCTIONAL)
    preconditions = cell("preconditions") or None

    try:
        built = TestCase(
            tc_id=tc_id,
            module=module[:100] if len(module) >= 2 else "Imported cases",
            title=title[:250],
            priority=priority,
            type=test_type,
            preconditions=preconditions,
            steps=[
                TestStep(
                    step_number=number,
                    action=action,
                    expected_result=_expected_for(number - 1, expected, len(steps)),
                )
                for number, action in enumerate(steps, start=1)
            ],
        )
    except Exception as exc:
        return {
            "case": None,
            "reject": {
                "row": row_number,
                "why": _explain_validation(exc),
                "title": title[:80],
            },
            "renumbered": False,
        }
    return {"case": built, "reject": None, "renumbered": renumbered}


def _assemble(rows: list[list[str]], first_data_row: int) -> dict:
    """Header row + data rows -> the import result."""
    if not rows:
        return {"error": "There were no rows to import.", "content": None}
    mapping = _map_headers(rows[0])
    if "title" not in mapping or "steps" not in mapping:
        return {
            "error": (
                "This table has no recognisable title and steps columns, so "
                "nothing was imported. Expected headers like: "
                + ", ".join(("TC ID", "Title", "Steps", "Expected Results"))
                + "."
            ),
            "content": None,
        }
    cases, rejected, renumbered = [], [], []
    for offset, row in enumerate(rows[1 : MAX_ROWS + 1]):
        outcome = _row_to_case(row, mapping, first_data_row + offset, len(cases) + 1)
        if outcome["case"] is not None:
            cases.append(outcome["case"])
            if outcome["renumbered"]:
                renumbered.append(outcome["case"].tc_id)
        elif outcome["reject"] is not None:
            rejected.append(outcome["reject"])
    if not cases:
        return {
            "error": (
                "No row in this table produced a usable test case. "
                + str(len(rejected))
                + " row(s) were rejected; the first was row "
                + str(rejected[0]["row"])
                + " ("
                + rejected[0]["why"]
                + ")."
                if rejected
                else "No row in this table produced a usable test case."
            ),
            "content": None,
        }
    truncated = len(rows) - 1 > MAX_ROWS
    return {
        "error": None,
        "content": {
            "cases": cases,
            "rejected": rejected,
            "renumbered": renumbered,
            "total_rows": len(rows) - 1,
            "truncated": truncated,
        },
    }


def from_markdown(text: object) -> dict:
    """A pasted markdown (or plain pipe-delimited) table."""
    try:
        # The byte cap is checked on the RAW text, BEFORE any normalisation:
        # a cap applied after a TRUNCATING cleaner is not a cap at all, which
        # is exactly how 199 rows of a 299-row paste went missing in silence.
        raw = text if isinstance(text, str) else ("" if text is None else str(text))
        if len(raw.encode("utf-8", errors="replace")) > MAX_BYTES:
            return {
                "error": (
                    "That paste is larger than the "
                    + str(MAX_BYTES)
                    + " byte cap and was not imported."
                ),
                "content": None,
            }
        # Document-level normalisation, which never trims content.
        body = _clean_document(text)
        if not body:
            return {"error": "Nothing was pasted.", "content": None}
        rows: list[list[str]] = []
        first_data_row = 0
        for number, line in enumerate(body.split("\n"), start=1):
            stripped = line.strip()
            if not stripped or "|" not in stripped:
                continue
            if re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
                continue  # the ---|--- separator
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            # A markdown cell escapes a literal newline as <br>.
            cells = [re.sub(r"(?i)<br\s*/?>", "\n", cell) for cell in cells]
            rows.append(cells)
            if len(rows) == 2:
                first_data_row = number
        if len(rows) < 2:
            return {
                "error": (
                    "That paste does not contain a table with a header row and "
                    "at least one case row, so nothing was imported."
                ),
                "content": None,
            }
        return _assemble(rows, first_data_row or 2)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.from_markdown failed")
        return {"error": str(exc), "content": None}


def from_csv(source: object) -> dict:
    """A ``.csv`` path or its text."""
    try:
        text = _read_text(source)
        if text.get("error"):
            return text
        body = str(text["content"])
        reader = csv.reader(io.StringIO(body))
        rows = [[_clean(cell) for cell in row] for row in reader]
        rows = [row for row in rows if any(row)]
        if len(rows) < 2:
            return {
                "error": (
                    "That CSV has no header row plus at least one case row, so "
                    "nothing was imported."
                ),
                "content": None,
            }
        return _assemble(rows, 2)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.from_csv failed")
        return {"error": str(exc), "content": None}


def _read_text(source: object) -> dict:
    """A path or a string -> text, byte-capped. Never raises."""
    try:
        if isinstance(source, (bytes, bytearray)):
            if len(source) > MAX_BYTES:
                return {"error": _too_big("input"), "content": None}
            return {"error": None, "content": source.decode("utf-8", errors="replace")}
        text = str(source or "")
        candidate = _maybe_path(text)
        if candidate is not None:
            size = candidate.stat().st_size
            if size > MAX_BYTES:
                return {"error": _too_big(str(candidate.name)), "content": None}
            return {
                "error": None,
                "content": candidate.read_text(encoding="utf-8", errors="replace"),
            }
        if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
            return {"error": _too_big("input"), "content": None}
        return {"error": None, "content": text}
    except OSError as exc:
        return {"error": "Could not read that file: " + str(exc), "content": None}


def _too_big(what: str) -> str:
    return (
        str(what)
        + " is larger than the "
        + str(MAX_BYTES)
        + " byte cap and was not imported."
    )


def _column_index(ref: str) -> int:
    match = _CELL_REF_RE.match(str(ref or "").upper())
    if not match:
        return -1
    letters = match.group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _row_index(ref: str) -> int:
    match = _CELL_REF_RE.match(str(ref or "").upper())
    return int(match.group(2)) if match else -1


def from_xlsx(source: object) -> dict:
    """An ``.xlsx`` read with ``zipfile`` + ``xml.etree``, hardened.

    ``openpyxl`` is not a dependency and ``xlsxwriter`` writes only, so the read
    side is ours. The hardening is not optional: the file arrived from outside.
    """
    try:
        path = Path(str(source or "")).expanduser()
        if not path.is_file():
            return {
                "error": "There is no file at " + str(path) + ".",
                "content": None,
            }
        if path.stat().st_size > MAX_BYTES:
            return {"error": _too_big(path.name), "content": None}
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                return {
                    "error": (
                        "That workbook contains "
                        + str(len(infos))
                        + " internal files, more than the "
                        + str(MAX_ZIP_MEMBERS)
                        + " allowed, and was not opened."
                    ),
                    "content": None,
                }
            declared = sum(int(info.file_size or 0) for info in infos)
            if declared > MAX_UNCOMPRESSED_BYTES:
                return {
                    "error": (
                        "That workbook expands to "
                        + str(declared)
                        + " bytes, more than the "
                        + str(MAX_UNCOMPRESSED_BYTES)
                        + " allowed, and was not opened."
                    ),
                    "content": None,
                }
            shared = _shared_strings(archive)
            if shared.get("error"):
                return shared
            sheet = _sheet_xml(archive)
            if sheet.get("error"):
                return sheet
            rows = _sheet_rows(str(sheet["content"]), list(shared["content"]))
            if rows.get("error"):
                return rows
            table = list(rows["content"])
        table = [row for row in table if any(cell for cell in row)]
        if len(table) < 2:
            return {
                "error": (
                    "That workbook's first sheet has no header row plus at "
                    "least one case row, so nothing was imported."
                ),
                "content": None,
            }
        return _assemble(table, 2)
    except zipfile.BadZipFile:
        return {
            "error": "That file is not a readable .xlsx workbook.",
            "content": None,
        }
    except OSError as exc:
        return {"error": "Could not read that workbook: " + str(exc), "content": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.from_xlsx failed")
        return {"error": str(exc), "content": None}


def _member_text(archive: zipfile.ZipFile, name: str) -> dict:
    try:
        with archive.open(name) as handle:
            raw = handle.read(MAX_UNCOMPRESSED_BYTES + 1)
    except KeyError:
        return {"error": None, "content": ""}
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        return {
            "error": (
                "A part of that workbook ("
                + name
                + ") is larger than the allowed size and was not parsed."
            ),
            "content": None,
        }
    text = raw.decode("utf-8", errors="replace")
    if _DECL_RE.search(text):
        return {
            "error": (
                "That workbook declares an XML DOCTYPE or ENTITY, which a real "
                "spreadsheet never does. It was rejected rather than parsed."
            ),
            "content": None,
        }
    return {"error": None, "content": text}


def _strip_ns(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _shared_strings(archive: zipfile.ZipFile) -> dict:
    read = _member_text(archive, "xl/sharedStrings.xml")
    if read.get("error"):
        return read
    text = str(read["content"] or "")
    if not text.strip():
        return {"error": None, "content": []}
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return {
            "error": "That workbook's string table is not valid XML.",
            "content": None,
        }
    out: list[str] = []
    for item in root:
        if _strip_ns(item.tag) != "si":
            continue
        pieces = [node.text or "" for node in item.iter() if _strip_ns(node.tag) == "t"]
        out.append(_clean("".join(pieces)))
    return {"error": None, "content": out}


def _sheet_xml(archive: zipfile.ZipFile) -> dict:
    """The ``Test Cases`` sheet if the workbook names one, else sheet1."""
    names = {info.filename for info in archive.infolist()}
    target = "xl/worksheets/sheet1.xml"
    book = _member_text(archive, "xl/workbook.xml")
    if book.get("error"):
        return book
    text = str(book["content"] or "")
    if text.strip():
        try:
            root = ElementTree.fromstring(text)
            order = [node for node in root.iter() if _strip_ns(node.tag) == "sheet"]
            for position, node in enumerate(order, start=1):
                if str(node.get("name") or "").strip() == SHEET_NAME:
                    candidate = "xl/worksheets/sheet" + str(position) + ".xml"
                    if candidate in names:
                        target = candidate
                    break
        except ElementTree.ParseError:
            pass  # fall through to sheet1
    if target not in names:
        return {
            "error": "That workbook has no readable first worksheet.",
            "content": None,
        }
    return _member_text(archive, target)


def _sheet_rows(sheet_xml: str, shared: list[str]) -> dict:
    try:
        root = ElementTree.fromstring(sheet_xml)
    except ElementTree.ParseError:
        return {"error": "That worksheet is not valid XML.", "content": None}
    rows: list[list[str]] = []
    for row in root.iter():
        if _strip_ns(row.tag) != "row":
            continue
        cells: dict[int, str] = {}
        for cell in row:
            if _strip_ns(cell.tag) != "c":
                continue
            column = _column_index(cell.get("r") or "")
            if column < 0:
                column = len(cells)
            kind = str(cell.get("t") or "")
            value = ""
            for child in cell:
                name = _strip_ns(child.tag)
                if name == "v":
                    value = child.text or ""
                elif name == "is":
                    value = "".join(
                        node.text or ""
                        for node in child.iter()
                        if _strip_ns(node.tag) == "t"
                    )
            if kind == "s":
                try:
                    value = shared[int(value)]
                except (ValueError, IndexError):
                    value = ""
            cells[column] = _clean(value)
        if not cells:
            rows.append([])
            continue
        width = max(cells) + 1
        rows.append([cells.get(index, "") for index in range(width)])
        if len(rows) > MAX_ROWS + 1:
            break
    return {"error": None, "content": rows}


def load(source: object) -> dict:
    """Dispatch by extension, then by content. ``{"error", "content": {...}}``."""
    try:
        text = str(source or "")
        candidate = _maybe_path(text)
        if candidate is not None:
            suffix = candidate.suffix.lower()
            if suffix == ".xlsx":
                return from_xlsx(candidate)
            if suffix == ".csv":
                return from_csv(candidate)
            if suffix in (".md", ".markdown", ".txt"):
                return from_markdown(
                    candidate.read_text(encoding="utf-8", errors="replace")
                )
            return {
                "error": (
                    "Cannot import "
                    + candidate.name
                    + ": paste a markdown table, or give a .csv or .xlsx path."
                ),
                "content": None,
            }
        if "|" in text:
            return from_markdown(text)
        if "," in text and "\n" in text:
            return from_csv(text)
        return {
            "error": (
                "That does not look like a markdown table, a .csv or an .xlsx "
                "path, so nothing was imported."
            ),
            "content": None,
        }
    except OSError as exc:
        return {"error": "Could not read that source: " + str(exc), "content": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.load failed")
        return {"error": str(exc), "content": None}


def render_rejections(content: object) -> str:
    """The rejected rows as a tester-facing block. ``""`` when there are none."""
    body = content if isinstance(content, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    rejected = list(body.get("rejected") or [])
    if not rejected:
        return ""
    lines = [
        "**"
        + str(len(rejected))
        + " row(s) were NOT imported.** Fix and re-paste these:",
    ]
    for item in rejected[:50]:
        lines.append(
            "- row "
            + str(item.get("row"))
            + ": "
            + str(item.get("why") or "")
            + (" -- " + str(item.get("title")) if str(item.get("title") or "") else "")
        )
    return "\n".join(lines)
