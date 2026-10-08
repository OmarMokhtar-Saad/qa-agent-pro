"""Per-app knowledge: notes a tester saved about ONE Android app.

One SQLite file per package at ``<cache>/apps/<package>/knowledge.db`` (tables
``meta``, ``notes``, ``events``). It is local to this machine and never
uploaded. This module owns validation, secret refusal, the caps, supersede and
the counters. Callers: the ``note`` parameter and ``qa_mobile_notes`` (through
``tools/mcp_handlers.py``) and the executor's guard.

No public function raises: each returns ``{"error": ..., "content": ...}``.
A note is text the host model reads back, so it is only ever echoed through
``wrap_untrusted``; and a note can make a replay WAIT (bounded) or REFUSE an
op, never allow one the destructive guard stops.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tools import api_redact
from tools.mobile import actions, held_inputs, knowledge_limits, knowledge_schema, paths
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

#: Notes that may be ACTIVE for one app. Each is a guard read on every replay.
MAX_ACTIVE_NOTES = 100
#: Notes kept per app, history included: supersede and retire delete nothing,
#: so this stops the file growing; at the cap the oldest retired or superseded
#: rows are dropped to make room (an active note never is).
MAX_NOTE_ROWS = 1000
#: Characters in one note's text. It is echoed to the host model.
MAX_NOTE_CHARS = 600
#: Counter events kept per app. Past it the counters still count.
MAX_EVENT_ROWS = 5000
#: Notes one list reply carries.
MAX_LIST_ROWS = 50
#: Seconds one sqlite call may wait on a locked file.
DB_TIMEOUT_S = 2.0

#: A wait note that names no ``ms`` waits this long at most.
DEFAULT_GUARD_WAIT_MS = 5000
#: A held tester value shorter than this is not searched for in a note.
HELD_VALUE_LENGTH = 3
KINDS = ("wait", "avoid", "precondition", "fact")
#: Kinds the executor reads as guards; the others are context only.
GUARD_KINDS = ("wait", "avoid")

_SCHEMA_VERSION = "1"
_FIELD_CHARS = 200
_ENV_ENTRIES = knowledge_limits.ENV_ENTRIES
_WHEN_KEYS = ("op", "rid", "activity", "env")
_THEN_KEYS = ("until_rid", "until_text", "until_gone", "until_rid_text", "ms")
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
_OP_RE = re.compile(r"^[a-z_]{1,32}$")
_DIGITS_RE = re.compile(r"\d{6,}")
#: A credential word within ``_NEAR_WORDS`` words of a VALUE-SHAPED token, in
#: either direction ("otp 4821", "enter 4821 as the PIN", "pin=1234"). A bare
#: credential word is fine ("login is required", "the code is sent by SMS"):
#: the term list names topics, not secrets, so matching it alone over-refuses.
#: Value-shaped = 4+ digits, or 5+ chars mixing letters and digits. Single
#: digits spaced out ("pin 1 2 3 4") are joined before the check.
_NEAR_WORDS = 3
_SPLIT_RE = re.compile(r"[\s:=]+")
_ALPHA_RE = re.compile(r"[a-z]+")
_DIGITS4_RE = re.compile(r"\d{4}")
_SPACED_DIGITS_RE = re.compile(r"(?<![\d.-])\d(?:[ .-]\d(?![\d.-])){3,}")
#: A secret word ASSIGNED a value: ``password=ab12`` (any 3+ chars after
#: ``=``), or ``password is Hunter`` / ``pin: Tr0ub`` (after ``is``/``was``/
#: ``:``, a token that is not a plain lowercase word). Narrower than
#: CREDENTIAL_TERMS: "login is required" and "the code is sent" stay prose.
#: ``pass`` counts only in the ``=`` form: "Pass: Yes" is a test verdict.
#: KNOWN LIMIT: an all-lowercase word after ``is`` or ``:`` ("password:
#: hunter") reads as prose, and so does an all-lowercase hyphenated
#: passphrase ("passphrase is correct-horse-battery"); the held-value check
#: is the net for both.
_STATED_WORDS = r"password|passwd|passcode|passphrase|pin|otp|secret|token|apikey|cvv"
_ASSIGNED_RE = re.compile(
    r"\b(?i:pass|%s)\s*=\s*[^\s,;]{3,}" % _STATED_WORDS
    + r"|\b(?i:%s)(?:\s+(?i:is|was)\s+|\s*:\s*)" % _STATED_WORDS
    + r"(?![a-z]+(?:-[a-z]+)*(?:[\s,;.!?)]|$))[^\s,;]{3,}"
)
#: Four or more digits right after a secret word, split by spaces or dashes
#: (spaces around the dash allowed): "pin 1 - 2 - 3 - 4", "otp: 48 21",
#: "token 1-2-3-4". After a word that names a code (``_CODE_WORDS``) dots,
#: commas and slashes split a run too, so "pin: 1,2,3,4", "pin 1/2/3/4" and
#: "password 1.2.3.4" are refused while "token 2.0.1.3 build", "secret
#: 10.0.2.2" and "token 1, 2, 3, 4 appear" read as a version, a host or a list.
#: Only after the word, so "version 1.2.3.4" and "wait 2-3 s" stay prose. Any
#: number of is/was/at/to/on may sit between ("pin at 123 456", "pin is at to
#: 1234"), and so may a line break ("pin\nis 1234": the run is matched over
#: the whole note, not per line). The prefix has one way to spend each space,
#: and each connector needs a word, so a long run of spaces or connectors costs
#: linear time. After "pin" only, ``_states_digits`` lets a measurement or a
#: date through. The loosening is deliberate: "token 1,2,3,4" and "secret
#: 1/2/3/4" read as prose, since a token or secret is rarely a digit run.
_CODE_WORDS = r"pin|otp|passcode|cvv|password|passwd|passphrase"
_SPACE_SEP = r"(?:\s*-\s*|\s+)"
_CODE_SEP = r"(?:\s*[-,/.]\s*|\s+)"
_WORD_PREFIX = r"(?:\s+(?i:is|was|at|to|on)\b)*(?:\s*[:=])?\s*"
_STATED_DIGITS_RE = re.compile(
    r"\b(?:((?i:%s))%s(\d(?:%s?\d){3,})|((?i:%s))%s(\d(?:%s?\d){3,}))(?!\d)"
    % (_CODE_WORDS, _WORD_PREFIX, _CODE_SEP, _STATED_WORDS, _WORD_PREFIX, _SPACE_SEP)
)
#: Only "pin" is ever measured or dated (a drag pin, a pinned view or event),
#: so "otp 123 456 pt" and "cvv 12/25" are codes. A measurement is a run whose
#: every group has 3 or more digits, a thousands comma joining its group ("pin
#: 1,000 px") + a layout unit (px, dp, sp, pt; a time unit or "x" does not
#: count, since "pin 123 456 s" is a code) + no more digits, even behind
#: punctuation or "is" ("pin 100 200 px 4321", "pin 100 200 px: 4321" are
#: PINs). A run of 1- or 2-digit groups or a mix ("pin 12 34 px", "pin 123 4
#: px") is still a PIN, and so is a run of 6 or more digits in all ("pin 123
#: 456 px", "pin 100 200 px"): split or not, that is a 6-digit code, the same
#: as ``_DIGITS_RE``. Drag coordinates read as prose without the word "pin".
_PROSE_WORD = "pin"
_UNIT_AFTER_RE = re.compile(r"\s*(?i:px|dp|sp|pt)\b")
_MORE_DIGITS_RE = re.compile(r"[^\w\n]*(?:(?i:is|was|and|then|at|or|to)\b[^\w\n]*)*\d")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_MEASURE_GROUP_DIGITS = 3
_MEASURE_MAX_DIGITS = 6
#: A date is two 1- or 2-digit groups split by one slash, month/day or
#: day/month: "pin 12/31 on the map". No year: a 4-digit year is refused as a
#: value anyway (``_is_value_token``). ASCII digits only: ``int`` would read
#: fullwidth digits too.
_DATE_RE = re.compile(r"([0-9]{1,2})/([0-9]{1,2})")
#: Every line boundary ``str.splitlines`` knows, so a bare CR, U+2028, NEL,
#: VT or FF after "pin" starts a new line just as LF and CRLF do.
_LINE_BREAK_RE = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def _states_digits(segment: str) -> bool:
    """A ``_STATED_DIGITS_RE`` run that is not a pin measurement or date.

    A run on a later line than "pin" is its value, as in a label above a field:
    "pin\\n1234 px" is a PIN.
    """
    for match in _STATED_DIGITS_RE.finditer(segment):
        word, run = (g for g in match.groups() if g)
        if word.lower() != _PROSE_WORD or _LINE_BREAK_RE.search(
            match.group()[: -len(run)]
        ):
            return True
        if not (_is_measurement(segment, match.end(), run) or _is_date(run)):
            return True
    return False


def _is_measurement(segment: str, end: int, run: str) -> bool:
    unit = _UNIT_AFTER_RE.match(segment, end)
    if not unit or _MORE_DIGITS_RE.match(segment, unit.end()):
        return False
    if not run.isascii():
        return False
    groups = re.findall(r"\d+", _THOUSANDS_RE.sub("", run))
    if sum(len(g) for g in groups) >= _MEASURE_MAX_DIGITS:
        return False
    return min(len(g) for g in groups) >= _MEASURE_GROUP_DIGITS


def _is_date(run: str) -> bool:
    date = _DATE_RE.fullmatch(run)
    if not date:
        return False
    first, second = int(date.group(1)), int(date.group(2))
    return (1 <= first <= 12 and 1 <= second <= 31) or (
        1 <= second <= 12 and 1 <= first <= 31
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    when_json TEXT NOT NULL,
    then_json TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT '',
    source_run TEXT NOT NULL DEFAULT '',
    trust TEXT NOT NULL DEFAULT 'tester',
    status TEXT NOT NULL DEFAULT 'active',
    confirmed INTEGER NOT NULL DEFAULT 0,
    contradicted INTEGER NOT NULL DEFAULT 0,
    supersedes INTEGER,
    valid_from REAL NOT NULL,
    invalid_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    note_id INTEGER,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT ''
);
"""


def _ok(content: object) -> dict:
    return {"error": None, "content": content}


def _err(message: str) -> dict:
    return {"error": message, "content": None}


def _fail(what: str, exc: Exception) -> dict:
    logger.warning("app_knowledge.%s failed: %s", what, exc, exc_info=True)
    return _err("the app knowledge store is unavailable (%s)" % type(exc).__name__)


def db_path(package: object) -> Path | None:
    """Where *package*'s store lives, or ``None`` for a name that is not an
    Android package name. Pure: creates nothing."""
    name = str(package or "").strip()
    if len(name) > _FIELD_CHARS or not _PACKAGE_RE.match(name):
        return None
    return paths.sub("apps") / _dir_name(name) / "knowledge.db"


def _dir_name(name: str) -> str:
    """The store directory for package *name*. Android package names are
    case-sensitive but macOS and Windows file systems are not, so
    ``com.Foo.app`` and ``com.foo.app`` would share one store there. A name
    with an upper-case letter gets a lower-case directory plus a digest of the
    exact name; an all-lower-case name (the usual case) is used as is.
    A mixed-case store made before this rule is left where it was: no longer
    read, never deleted."""
    if name == name.lower():
        return name
    return "%s~%s" % (name.lower(), hashlib.sha256(name.encode()).hexdigest()[:12])


def _store_path(package: object) -> Path | None:
    """``db_path``, plus owner-only modes on a mixed-case store made before the
    ``_dir_name`` rule, which still sits under the exact package name. That
    store is never read again, but its notes stay on disk. Creates nothing."""
    path = db_path(package)
    if path is not None:
        legacy = path.parent.with_name(str(package).strip()) / path.name
        if legacy.parent != path.parent and legacy.parent.is_dir():
            _tighten_store(legacy)
    return path


def _tighten(path: Path, mode: int) -> None:
    """Owner-only *mode* where the OS allows it. Notes are plain text about the
    tester's app; nobody else on the machine needs to read them."""
    try:
        path.chmod(mode)
    except OSError:
        logger.info("app knowledge: could not tighten permissions on %s", path)


def _tighten_store(path: Path) -> None:
    """Owner-only modes on *path*'s directory, the database and its ``-wal`` /
    ``-shm`` files, so a store made before the mode rule is fixed by any open,
    not only the next write. Creates nothing and never raises: a path with no
    file name (``/``) is left alone."""
    if not path.name:
        return
    _tighten(path.parent, 0o700)
    for name in (path.name, path.name + "-wal", path.name + "-shm"):
        member = path.with_name(name)
        if member.exists():
            _tighten(member, 0o600)


def _migrate_once(conn: sqlite3.Connection) -> None:
    """Bring a write connection to schema v2 when the file is not there yet.
    A failure degrades like a v1 file: the slice-1 tables still work."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row[0] != knowledge_schema.SCHEMA_VERSION:
            knowledge_schema.migrate(conn)
    except (sqlite3.Error, ValueError, OSError):
        logger.info("app knowledge: schema migration skipped", exc_info=True)


def _connect(path: Path, *, write: bool) -> sqlite3.Connection:
    """WRITE creates the directory (0700), the file (0600) and the schema. READ
    never creates anything (the caller checks the file exists first) and is
    ``query_only``. Both tighten an existing store (``_tighten_store``).
    ``mode=ro`` is not used: it can fail on a WAL file. SQLite gives new
    ``-wal`` and ``-shm`` files the database file's mode."""
    if write:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600))
    _tighten_store(path)
    conn = sqlite3.connect(str(path), timeout=DB_TIMEOUT_S)
    try:
        conn.row_factory = sqlite3.Row
        if write:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (_SCHEMA_VERSION,),
            )
            # The INSERT opened an implicit transaction; close it, or the caller's
            # BEGIN IMMEDIATE raises "cannot start a transaction within a transaction".
            conn.commit()
            _migrate_once(conn)
        else:
            conn.execute("PRAGMA query_only=ON")
    except BaseException:
        # The caller's closing() never sees a connection this raised out of.
        conn.close()
        raise
    return conn


def _clean(value: object, label: str) -> tuple:
    """``(text, error)`` for one short string field."""
    if not isinstance(value, str):
        return "", "%s must be a string" % label
    text = " ".join(value.split())
    if len(text) > _FIELD_CHARS:
        return "", "%s is over %d characters" % (label, _FIELD_CHARS)
    return text, ""


def _validate_when(when: dict) -> tuple:
    unknown = sorted(str(key) for key in when if key not in _WHEN_KEYS)
    if unknown:
        return {}, "unknown when key(s): " + ", ".join(unknown)
    clean: dict = {}
    for key in ("op", "rid", "activity"):
        if key in when:
            value, problem = _clean(when[key], "when." + key)
            if problem:
                return {}, problem
            if value:
                clean[key] = value
    if "env" in when:
        env = when["env"]
        ok = isinstance(env, dict) and len(env) <= _ENV_ENTRIES
        ok = ok and all(
            isinstance(k, str)
            and isinstance(v, str)
            and len(k) <= _FIELD_CHARS
            and len(v) <= _FIELD_CHARS
            for k, v in env.items()
        )
        if not ok:
            return {}, "when.env must map names to strings"
        if env:
            clean["env"] = dict(env)
    op = clean.get("op")
    if op is not None and (not _OP_RE.match(op) or op == "wait"):
        return {}, "when.op must be a lowercase op name other than wait"
    return clean, ""


def _until_fields(then: dict) -> tuple:
    clean: dict = {}
    for key in ("until_rid", "until_text", "until_rid_text"):
        if key in then:
            value, problem = _clean(then[key], "then." + key)
            if problem:
                return {}, problem
            if value:
                clean[key] = value
    return clean, ""


def _until_gone(then: dict, clean: dict) -> tuple:
    if "until_gone" not in then:
        return {}, ""
    if not isinstance(then["until_gone"], bool):
        return {}, "then.until_gone must be true or false"
    if not then["until_gone"]:
        return {}, ""
    if "until_rid" not in clean:
        return {}, "until_gone needs until_rid"
    return {"until_gone": True}, ""


def _wait_ms(then: dict) -> tuple:
    if "ms" not in then:
        return {}, ""
    ms = then["ms"]
    if isinstance(ms, bool) or not isinstance(ms, int):
        return {}, "then.ms must be a whole number"
    if not 0 <= ms <= actions.MAX_WAIT_MS:
        return {}, "then.ms must be from 0 to %d" % actions.MAX_WAIT_MS
    return ({"ms": ms} if ms else {}), ""


def _validate_then(then: dict, has_when_target: bool) -> tuple:
    if not has_when_target:
        return {}, "a wait note needs when.rid or when.activity"
    unknown = sorted(str(key) for key in then if key not in _THEN_KEYS)
    if unknown:
        return {}, "unknown then key(s): " + ", ".join(unknown)
    clean, problem = _until_fields(then)
    if problem:
        return {}, problem
    if ("until_rid" in clean) == ("until_text" in clean):
        return {}, "then needs exactly one of until_rid or until_text"
    gone, problem = _until_gone(then, clean)
    if problem:
        return {}, problem
    clean.update(gone)
    if "until_rid_text" in clean and "until_rid" not in clean:
        return {}, "until_rid_text needs until_rid"
    wait, problem = _wait_ms(then)
    if problem:
        return {}, problem
    clean.update(wait)
    return clean, ""


def _validate_steps(then: dict) -> tuple:
    """``(steps, error)`` for a precondition's ``then={"steps": [...]}``."""
    extra = sorted(str(key) for key in then if key != "steps")
    if extra:
        return [], "unknown then key(s): " + ", ".join(extra)
    steps = then.get("steps")
    if not isinstance(steps, list) or not steps:
        return [], "a precondition needs then.steps"
    if len(steps) > knowledge_limits.PRECONDITION_STEPS:
        return [], "a precondition has at most %d steps" % (
            knowledge_limits.PRECONDITION_STEPS
        )
    clean = []
    for step in steps:
        if not isinstance(step, dict) or not _OP_RE.match(str(step.get("op", ""))):
            return [], "each precondition step needs an `op` name"
        item: dict = {}
        for key, value in step.items():
            if not isinstance(key, str) or isinstance(value, (dict, list)):
                return [], "precondition step values must be text, numbers or booleans"
            if isinstance(value, str):
                value, problem = _clean(value, "step." + key)
                if problem:
                    return [], problem
            item[key] = value
        clean.append(item)
    return clean, ""


def _validate(text: object, kind: object, when: object, then: object) -> tuple | str:
    """``(text, when, then)`` cleaned, or an error string."""
    if not isinstance(text, str) or not text.strip():
        return "the note text is empty"
    text = " ".join(text.split())
    if len(text) > MAX_NOTE_CHARS:
        return "the note text is over %d characters" % MAX_NOTE_CHARS
    if kind not in KINDS:
        return "kind must be one of: " + ", ".join(KINDS)
    when = {} if when is None else when
    then = {} if then is None else then
    if not isinstance(when, dict):
        return "when must be an object"
    if not isinstance(then, dict):
        return "then must be an object"
    clean_when, problem = _validate_when(when)
    if problem:
        return problem
    return _validate_by_kind(text, kind, clean_when, then)


def _validate_by_kind(
    text: str, kind: str, clean_when: dict, then: dict
) -> tuple | str:
    """The kind-specific part of ``_validate``."""
    if kind == "fact":
        if then:
            return "a fact note takes no `then`"
        return text, clean_when, {}
    if kind == "precondition":
        steps, problem = _validate_steps(then)
        if problem:
            return problem
        return text, clean_when, {"steps": steps}
    if kind == "avoid":
        if "rid" not in clean_when:
            return "an avoid note needs when.rid"
        if then:
            return "an avoid note takes no `then`"
        return text, clean_when, {}
    target = "rid" in clean_when or "activity" in clean_when
    clean_then, problem = _validate_then(then, target)
    if problem:
        return problem
    return text, clean_when, clean_then


def _leaf_strings(obj: object) -> list:
    """Every string inside ``obj``. Numbers such as ``ms`` are not text."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _leaf_strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _leaf_strings(v)]
    return []


def _is_value_token(token: str) -> bool:
    if _DIGITS4_RE.search(token):
        return True
    return (
        len(token) >= 5
        and any(ch.isdigit() for ch in token)
        and any(ch.isalpha() for ch in token)
    )


def _credential_near_value(segment: str) -> bool:
    if _ASSIGNED_RE.search(segment) or _states_digits(segment):
        return True
    segment = _SPACED_DIGITS_RE.sub(lambda m: re.sub(r"\D", "", m.group()), segment)
    tokens = [t.strip(".,;()[]{}\"'") for t in _SPLIT_RE.split(segment)]
    tokens = [t for t in tokens if t]
    terms = [
        i
        for i, t in enumerate(tokens)
        if any(w in actions.CREDENTIAL_TERMS for w in _ALPHA_RE.findall(t.lower()))
    ]
    if not terms:
        return False
    values = [i for i, t in enumerate(tokens) if _is_value_token(t)]
    return any(abs(i - j) <= _NEAR_WORDS for i in terms for j in values)


def _secret_reason(blob: str, secrets: object, source_run: object) -> str:
    """Why *blob* looks like it holds a secret, or ``""``."""
    low = blob.lower()
    held = [str(value) for value in (secrets or ())]
    try:
        held += [str(v) for v in held_inputs.recall(str(source_run or "")).values()]
    except Exception:  # pragma: no cover - recall never raises
        pass
    for value in held:
        value = value.strip()
        if len(value) >= HELD_VALUE_LENGTH and value.lower() in low:
            return "it contains a value the tester typed into this run"
    if _DIGITS_RE.search(blob):
        return "it contains a run of 6 or more digits"
    # The API lane's value-shape scan: API keys, JWTs, `Bearer` tokens, grouped
    # card numbers, e-mail addresses, +phone numbers.
    if api_redact.scan_finds_secret(blob):
        return "it contains a key, token, card number or contact detail"
    if _states_digits(blob) or any(
        _credential_near_value(seg) for seg in blob.split("\n")
    ):
        return "it states a credential value"
    return ""


def secret_reason(blob: object, secrets: object = (), source_run: object = "") -> str:
    """Public face of the note scrub: why *blob* looks like it holds a secret, or ``""``.

    Flows reuse it so a saved screen string passes the SAME test a saved note does."""
    return _secret_reason(str(blob or ""), secrets, source_run)


def _add_event(conn, note_id, event: str, detail: object, run_id: object) -> bool:
    # Only slice-1 events count; knowledge_db events carry a table_name and are
    # pruned by knowledge_db.compact instead. Feedback rows (capped by
    # knowledge_feedback), learn markers and rollback rows have an empty
    # table_name too but are not notes' events, so they never use this budget.
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM events WHERE (table_name IS NULL OR table_name = '')"
        " AND event NOT IN ('feedback', 'learn_marker', 'rollback')"
    ).fetchone()
    if count >= MAX_EVENT_ROWS:
        return False
    conn.execute(
        "INSERT INTO events (ts, note_id, event, detail, run_id) VALUES (?, ?, ?, ?, ?)",
        (time.time(), note_id, event, str(detail or "")[:200], str(run_id or "")[:64]),
    )
    return True


def _load(raw: object) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _extra_keys(row: sqlite3.Row) -> dict:
    """Schema-v2 columns when the file has them (a v1 file has none)."""
    have = row.keys()
    return {
        name: row[name]
        for name in ("trust", "screen_key", "element_fp", "recheck_at")
        if name in have
    }


def _note(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "text": row["text"],
        "when": _load(row["when_json"]),
        "then": _load(row["then_json"]),
        "status": row["status"],
        "confirmed": row["confirmed"],
        "contradicted": row["contradicted"],
        "app_version": row["app_version"],
        "source_run": row["source_run"],
        "supersedes": row["supersedes"],
        **_extra_keys(row),
    }


def _cap_error(conn, superseding: int) -> str:
    """The refusal text when a new note would break a cap, else ``""``."""
    (active,) = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE status = 'active'"
    ).fetchone()
    (total,) = conn.execute("SELECT COUNT(*) FROM notes").fetchone()
    if active - superseding >= MAX_ACTIVE_NOTES:
        return (
            "this app already has %d active notes: retire one first "
            "(qa_mobile_notes action=retire)" % MAX_ACTIVE_NOTES
        )
    if total >= MAX_NOTE_ROWS:
        # Full: make room by dropping the OLDEST retired or superseded rows
        # (and their events). An active note is never deleted.
        stale = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM notes WHERE status != 'active' ORDER BY id LIMIT ?",
                (total - MAX_NOTE_ROWS + 1,),
            )
        ]
        for old_id in stale:
            conn.execute("DELETE FROM events WHERE note_id = ?", (old_id,))
            conn.execute("DELETE FROM notes WHERE id = ?", (old_id,))
        if total - len(stale) >= MAX_NOTE_ROWS:
            return (
                "this app's note history is full (%d rows, all active): "
                "retire one first (qa_mobile_notes action=retire)" % MAX_NOTE_ROWS
            )
    return ""


def _insert_note(conn, values: tuple, old: list) -> int:
    """Insert the row, mark the *old* ids superseded, return the new id.

    *values* ends with ``now``, the ``valid_from`` and ``invalid_at`` time."""
    cur = conn.execute(
        "INSERT INTO notes (kind, text, when_json, then_json, scope_key, "
        "app_version, source_run, supersedes, valid_from) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    for old_id in old:
        conn.execute(
            "UPDATE notes SET status = 'superseded', invalid_at = ? WHERE id = ?",
            (values[-1], old_id),
        )
    return cur.lastrowid


def add_note(
    package: object,
    text: object,
    kind: object,
    when: object = None,
    then: object = None,
    *,
    source_run: str = "",
    secrets: object = (),
    app_version: str = "",
) -> dict:
    """Save a note. ``content`` is ``{id, kind, text, superseded: [ids]}``.

    Validation and the secret check run BEFORE anything is opened, so a refusal
    creates no directory. A note with the same scope (kind plus ``when``)
    SUPERSEDES the old one: the old row is marked, never deleted.
    """
    try:
        path = _store_path(package)
        if path is None:
            return _err("that is not an Android package name")
        clean = _validate(text, kind, when, then)
        if isinstance(clean, str):
            return _err(clean)
        text, when, then = clean
        reason = _plaintext_refusal(text, when, then, secrets, source_run)
        if reason:
            return _err(reason)
        return _write_note(path, (kind, text, when, then), (app_version, source_run))
    except Exception as exc:
        return _fail("add_note", exc)


def _plaintext_refusal(
    text: str, when: object, then: object, secrets: object, source_run: object
) -> str:
    """The refusal text when the note holds a secret, else ``""``."""
    blob = "\n".join([text] + _leaf_strings(when) + _leaf_strings(then))
    reason = _secret_reason(blob, secrets, source_run)
    if not reason:
        return ""
    return (
        "%s. Notes are stored in plain text on this machine: describe "
        "the screen or the timing, never a password, a code or a "
        "personal number" % reason
    )


def _write_note(path: Path, note: tuple, meta: tuple) -> dict:
    """Store a validated *note* ``(kind, text, when, then)``.

    *meta* is ``(app_version, source_run)``. A same-scope active note is
    marked superseded, never deleted."""
    kind, text, when, then = note
    app_version, source_run = meta
    scope = str(kind) + "|" + json.dumps(when, sort_keys=True)
    now = time.time()
    with closing(_connect(path, write=True)) as conn, conn:
        # Take the write lock BEFORE the scope/count reads: legacy
        # sqlite3 isolation opens a transaction only at the first write,
        # so two runs could both pass the cap check.
        conn.execute("BEGIN IMMEDIATE")
        old = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM notes WHERE scope_key = ? AND status = 'active'",
                (scope,),
            )
        ]
        full = _cap_error(conn, len(old))
        if full:
            return _err(full)
        values = (
            kind,
            text,
            json.dumps(when, sort_keys=True),
            json.dumps(then, sort_keys=True),
            scope,
            str(app_version or "")[:64],
            str(source_run or "")[:64],
            old[0] if old else None,
            now,
        )
        new_id = _insert_note(conn, values, old)
        _add_event(conn, new_id, "added", kind, source_run)
    return _ok(
        {"id": new_id, "kind": kind, "text": text, "when": when, "superseded": old}
    )


def _read_notes(package: object, where: str) -> tuple:
    """``(notes, error)``. A missing store is ``([], None)`` and is NOT created.
    *where* is a constant SQL fragment, never caller text."""
    path = _store_path(package)
    if path is None:
        return [], "that is not an Android package name"
    if not path.exists():
        return [], None
    with closing(_connect(path, write=False)) as conn:
        rows = conn.execute(
            "SELECT * FROM notes " + where + " ORDER BY id DESC LIMIT ?",
            (MAX_LIST_ROWS if where == "" else MAX_ACTIVE_NOTES,),
        ).fetchall()
    return [_note(row) for row in reversed(rows)], None


def list_notes(package: object, *, include_inactive: bool = False) -> dict:
    """The app's notes, oldest first. Read-only; a missing store is ``[]``."""
    try:
        notes, problem = _read_notes(
            package, "" if include_inactive else "WHERE status = 'active'"
        )
        return _err(problem) if problem else _ok(notes)
    except Exception as exc:
        return _fail("list_notes", exc)


def load_guards(package: object) -> dict:
    """Every ACTIVE note, for the executor. Read-only; creates nothing."""
    try:
        notes, problem = _read_notes(
            package, "WHERE status IN ('active', 'needs_recheck')"
        )
        return _err(problem) if problem else _ok(notes)
    except Exception as exc:
        return _fail("load_guards", exc)


def retire_note(
    package: object, note_id: object, *, reason: str = "", run_id: str = ""
) -> dict:
    """Retire one active note. The row stays: retire is a status, not a delete."""
    try:
        path = _store_path(package)
        if path is None:
            return _err("that is not an Android package name")
        try:
            number = int(note_id)
        except (TypeError, ValueError, OverflowError):
            return _err("note_id must be a whole number")
        if not path.exists():
            return _err("no notes saved for this app")
        why, _ = _clean(str(reason or ""), "reason")
        if _secret_reason(why, (), run_id):
            why = ""
        with closing(_connect(path, write=True)) as conn, conn:
            # Lock before the status read, as add_note does: two racing
            # retires must not both see the note active.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM notes WHERE id = ?", (number,)
            ).fetchone()
            if row is None:
                return _err("no note #%d" % number)
            if row["status"] != "active":
                return _err("note #%d is already %s" % (number, row["status"]))
            conn.execute(
                "UPDATE notes SET status = 'retired', invalid_at = ? WHERE id = ?",
                (time.time(), number),
            )
            _add_event(conn, number, "retired", why, run_id)
        return _ok({"id": number, "status": "retired"})
    except Exception as exc:
        return _fail("retire_note", exc)


def flush_counters(
    package: object, counts: object, events: object, *, run_id: str = ""
) -> dict:
    """Write a replay's buffered tallies. Counts only notes that are STILL
    active; events stop at ``MAX_EVENT_ROWS``. A missing store is a no-op."""
    try:
        path = _store_path(package)
        if path is None or not path.exists():
            return _ok({"counted": 0, "events": 0})
        counted = logged = 0
        with closing(_connect(path, write=True)) as conn, conn:
            for note_id, tally in dict(counts or {}).items():
                cur = conn.execute(
                    "UPDATE notes SET confirmed = confirmed + ?, "
                    "contradicted = contradicted + ? "
                    "WHERE id = ? AND status = 'active'",
                    (
                        int(tally.get("confirmed", 0)),
                        int(tally.get("contradicted", 0)),
                        int(note_id),
                    ),
                )
                counted += cur.rowcount
            for item in list(events or []):
                if _add_event(
                    conn,
                    item.get("note_id"),
                    str(item.get("event", "")),
                    item.get("detail", ""),
                    run_id,
                ):
                    logged += 1
        return _ok({"counted": counted, "events": logged})
    except Exception as exc:
        return _fail("flush_counters", exc)


def rid_matches(have: object, want: object) -> bool:
    """An element's resource id against a note's: equal, or ``want`` is the id
    WITHOUT its package prefix (``resend`` never matches ``send``)."""
    have_n = " ".join(str(have or "").split()).lower()
    want_n = " ".join(str(want or "").split()).lower()
    if not have_n or not want_n:
        return False
    return have_n == want_n or have_n.endswith("/" + want_n)


def env_matches(when_env: object, run_env: object) -> bool:
    """True when a note's ``when.env`` applies to this run: every pair equals the
    run's declared env. An empty run env never satisfies a non-empty scope (fail
    closed); a note with no env scope always applies."""
    if not when_env:
        return True
    if not isinstance(when_env, dict) or not isinstance(run_env, dict) or not run_env:
        return False
    return all(k in run_env and str(run_env[k]) == str(v) for k, v in when_env.items())


def _env_applies(guard: dict, when_env: object, env: object) -> bool:
    """An env-scoped guard applies when the loader stamped it ``_env_ok`` or the
    run env satisfies its condition (an absent env never does)."""
    return bool(guard.get("_env_ok")) or env_matches(when_env, env)


def candidates(guards: object, op: str, rids: object, env: object = None) -> list:
    """The guards that apply to this op on this target. *rids* is one resource
    id or every id the action could hit (its own ``rid`` plus the ids of the
    elements its text, label or id selects); a rid-scoped note matches when ANY
    of them does. An env-scoped note applies only when *env* (the run's declared
    env) matches it (``env_matches``) or the loader stamped it ``_env_ok``;
    precondition and fact notes are never guards."""
    have = [rids] if isinstance(rids, str) else list(rids or [])
    out = []
    for guard in list(guards or []):
        when = guard.get("when") or {}
        if guard.get("kind", "wait") not in GUARD_KINDS:
            continue
        if when.get("env") and not _env_applies(guard, when["env"], env):
            continue
        if when.get("op") and when["op"] != op:
            continue
        if when.get("rid") and not any(rid_matches(r, when["rid"]) for r in have):
            continue
        out.append(guard)
    return out


def activity_ok(guard: dict, probe: object) -> bool:
    """True when the note names no activity, or names one the focus probe shows.
    An unknown focus (``""``) never satisfies an activity-scoped note."""
    want = str(((guard or {}).get("when") or {}).get("activity") or "").strip()
    if not want:
        return True
    return want.lower() in str(probe or "").lower()


def echo(note: dict) -> str:
    """One note as UNTRUSTED text for the host model."""
    label = "app note #%s (%s)" % (note.get("id"), note.get("kind"))
    if note.get("status") == "needs_recheck":
        label += " unverified@%s" % (note.get("recheck_at") or "")
    return wrap_untrusted(
        label,
        str(note.get("text") or ""),
        MAX_NOTE_CHARS + 200,
    )


def render(package: str, notes: object) -> str:
    """A list reply. Note text is wrapped as untrusted; env-scoped notes say so."""
    items = list(notes or [])
    if not items:
        return "No app notes saved for `%s`." % package
    total = len(items)
    items = items[-MAX_LIST_ROWS:]
    lines = []
    for item in items:
        when = item.get("when") or {}
        line = "#%s [%s] %s (confirmed %s, contradicted %s)" % (
            item.get("id"),
            item.get("kind"),
            item.get("text"),
            item.get("confirmed", 0),
            item.get("contradicted", 0),
        )
        if when.get("env"):
            line += " -- env-scoped: not applied yet"
        if item.get("status") not in (None, "active"):
            line += " [%s]" % item["status"]
        lines.append(line)
    body = wrap_untrusted(
        "app notes for " + package,
        "\n".join(lines),
        MAX_LIST_ROWS * (MAX_NOTE_CHARS + 120),
    )
    count = "%d" % total
    if total > len(items):
        count = "showing the newest %d of %d" % (len(items), total)
    return (
        "App notes for `%s` (%s):\n%s\nRetire one with "
        'qa_mobile_notes(action="retire", note_id=N).' % (package, count, body)
    )
