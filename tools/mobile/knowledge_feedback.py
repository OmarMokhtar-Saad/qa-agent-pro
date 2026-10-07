"""Host feedback on applied knowledge (design sec 13).

The tester's model may report ``{item_id, verdict, observation, proposed}`` on an
item it was told about. This module validates that input, weighs it against the
dump, and records it in memory (``ctx.kbuf``); a plug ``flush`` persists it as
``feedback`` events; the gate consumes them at run end.

Nothing here calls a model, touches the DB on the hot path (``record`` and
``post_step`` are in-memory) or raises. Secrets are refused; stored text is only
ever echoed through ``wrap_untrusted``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import TypedDict

from tools.mobile import app_knowledge, knowledge_hooks
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits
from tools.untrusted import single_line, wrap_untrusted

logger = logging.getLogger(__name__)

PRIORITY = 90
VERDICTS = ("wrong", "outdated", "refine")
EVENT = "feedback"
_CONTEXT_WORDS = (
    ("logged_out", re.compile(r"\blogged[ _-]?out\b|\bsigned[ _-]?out\b", re.I)),
    ("logged_in", re.compile(r"\blogged[ _-]?in\b|\bsigned[ _-]?in\b", re.I)),
    ("keyboard", re.compile(r"\bkeyboard\b", re.I)),
    ("tablet", re.compile(r"\btablet\b", re.I)),
)
_ENV_PAIR = re.compile(r"\b([A-Za-z][\w-]{0,31})=([\w.-]{1,40})\b")
_IGNORED = frozenset({"item_id", "verdict", "observation", "proposed"})


class Feedback(TypedDict, total=False):
    item_id: str
    verdict: str
    observation: str
    proposed: str


def _text(value: object) -> str:
    return " ".join(str(value or "").split())[: limits.FEEDBACK_CHARS]


def validate_checked(raw: object) -> tuple:
    """``(items, problems)``. Items are clean, at most ``MAX_FEEDBACK_ITEMS``;
    a malformed item or one holding a secret is dropped and named in problems."""
    items: list = []
    problems: list = []
    try:
        listing = (
            raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        )
        for entry in listing[: limits.MAX_FEEDBACK_ITEMS]:
            if not isinstance(entry, dict):
                problems.append("item is not an object")
                continue
            parsed = kdb.parse_item_id(entry.get("item_id"))
            verdict = str(entry.get("verdict") or "").strip().lower()
            if parsed is None or verdict not in VERDICTS:
                problems.append(
                    "item_id must be <table>:<id> and verdict one of "
                    + "/".join(VERDICTS)
                )
                continue
            fb = Feedback(
                item_id="%s:%d" % parsed,
                verdict=verdict,
                observation=_text(entry.get("observation")),
                proposed=_text(entry.get("proposed")),
            )
            reason = app_knowledge.secret_reason(
                fb["observation"] + "\n" + fb["proposed"]
            )
            if reason:
                problems.append("%s: refused (%s)" % (fb["item_id"], reason))
                continue
            items.append(fb)
    except Exception:
        logger.exception("knowledge feedback validate failed")
    return items, problems


def validate(raw: object) -> list:
    """The clean feedback items in *raw* (never raises, never more than the cap)."""
    return validate_checked(raw)[0]


def echo(fb: object) -> str:
    """One feedback item as UNTRUSTED text."""
    try:
        data = fb if isinstance(fb, dict) else {}
        body = "%s %s: %s -> %s" % (
            data.get("item_id", ""),
            data.get("verdict", ""),
            data.get("observation", ""),
            data.get("proposed", ""),
        )
        return wrap_untrusted(
            "knowledge feedback", body, limit=2 * limits.FEEDBACK_CHARS + 100
        )
    except (TypeError, ValueError, AttributeError):
        return ""


def _rids(screen: object) -> list:
    rids: list = []
    try:
        for element in (screen or {}).get("elements") or []:
            if isinstance(element, dict) and element.get("rid"):
                rids.append(str(element["rid"]))
    except Exception:
        logger.exception("knowledge feedback: screen unreadable")
    return rids


def verify(fb: object, screen: object) -> float:
    """1.0 when the proposal is checkable against the dump (a proposed resource id
    is on screen), else ``HALF_WEIGHT``."""
    try:
        proposed = str((fb or {}).get("proposed") or "")
        have = _rids(screen)
        for token in re.findall(r"[\w.:/-]{3,}", proposed):
            if any(app_knowledge.rid_matches(r, token) for r in have):
                return 1.0
    except Exception:
        logger.exception("knowledge feedback verify failed")
    return limits.HALF_WEIGHT


def record(ctx: object, items: object, screen: object = None, index: int = -1) -> int:
    """Append validated *items* to ``ctx.kbuf`` (in memory only). Returns how many fit."""
    taken = 0
    try:
        for fb in validate(items):
            event = {
                "kind": EVENT,
                "item_id": fb["item_id"],
                "verdict": fb["verdict"],
                "observation": fb["observation"],
                "proposed": fb["proposed"],
                "weight": verify(fb, screen),
                "index": int(index),
            }
            if knowledge_hooks.kbuf_add(ctx, event):
                taken += 1
    except Exception:
        logger.exception("knowledge feedback record failed")
    return taken


def post_step(ctx: object, entry: object, before: object, after: object) -> None:
    """Plug seam: a step entry carrying ``feedback`` is recorded in memory."""
    try:
        if isinstance(entry, dict) and entry.get("feedback"):
            record(ctx, entry.get("feedback"), after, int(entry.get("index", -1)))
    except Exception:
        logger.exception("knowledge feedback post_step failed")


def flush(package: str, run_id: str, events: list) -> int:
    """Plug seam: persist the feedback events of one kbuf flush as ``feedback`` event
    rows (``detail`` = item id, ``before_json`` = the payload; ``table_name`` stays
    empty so a rollback never replays them). One batched write."""
    rows = [e for e in events or [] if isinstance(e, dict) and e.get("kind") == EVENT]
    if not rows or not run_id:
        return 0
    conn = kdb.open_rw(package)
    if conn is None:
        return 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        for event in rows:
            payload = {
                k: event.get(k)
                for k in ("verdict", "observation", "proposed", "weight")
            }
            conn.execute(
                "INSERT INTO events (ts, note_id, event, detail, run_id, table_name, row_id, before_json)"
                " VALUES (?, NULL, ?, ?, ?, '', NULL, ?)",
                (
                    now,
                    EVENT,
                    str(event.get("item_id") or "")[:60],
                    str(run_id)[:64],
                    json.dumps(payload),
                ),
            )
        conn.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE event = ?"
            " ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (EVENT, limits.MAX_FEEDBACK_EVENTS),
        )
        conn.commit()
        return len(rows)
    except (sqlite3.Error, TypeError, ValueError):
        logger.exception("knowledge feedback flush failed")
        try:
            conn.rollback()
        except sqlite3.Error:
            logger.exception("knowledge feedback rollback failed")
        return 0
    finally:
        conn.close()


def run_feedback(conn: object, run_id: str) -> list:
    """The feedback events of *run_id* as dicts (item_id, verdict, observation,
    proposed, weight), oldest first. Read-only; ``[]`` on any error."""
    out: list = []
    try:
        found = conn.execute(
            "SELECT detail, before_json FROM events WHERE event = ? AND run_id = ? ORDER BY id",
            (EVENT, str(run_id)),
        ).fetchall()
        for row in found:
            try:
                body = json.loads(row[1] or "{}")
            except ValueError:
                continue
            if isinstance(body, dict):
                out.append({"item_id": str(row[0]), **body})
    except sqlite3.Error:
        logger.exception("knowledge feedback read failed")
    return out


def latest_proposal(conn: object, item_id: str) -> dict:
    """The newest non-empty proposal ever reported for *item_id*, or ``{}``."""
    try:
        found = conn.execute(
            "SELECT before_json, run_id FROM events WHERE event = ? AND detail = ? ORDER BY id DESC LIMIT 20",
            (EVENT, str(item_id)),
        ).fetchall()
        for row in found:
            body = json.loads(row[0] or "{}")
            if isinstance(body, dict) and str(body.get("proposed") or "").strip():
                return {**body, "run_id": str(row[1])}
    except (sqlite3.Error, ValueError):
        logger.exception("knowledge feedback proposal read failed")
    return {}


def context_of(observation: object) -> dict:
    """The context an observation names, as a narrower ``when.env``: explicit
    ``name=value`` pairs, else one of logged in/out, keyboard, tablet."""
    text = str(observation or "")
    env = {
        k.lower(): v for k, v in _ENV_PAIR.findall(text) if k.lower() not in _IGNORED
    }
    if env:
        return dict(list(env.items())[: limits.ENV_ENTRIES])
    for name, pattern in _CONTEXT_WORDS:
        if pattern.search(text):
            return {"context": name}
    return {}


def match_existing(conn: object, note: object) -> int | None:
    """The id of an open note with the same (screen_key, element rid, kind), or ``None``."""
    try:
        data = note if isinstance(note, dict) else {}
        kind = str(data.get("kind") or "")
        rid = str((data.get("when") or {}).get("rid") or "")
        screen = str(data.get("screen_key") or "")
        if not kind or not (rid or screen):
            return None
        mine = int(data.get("id") or 0)
        found = conn.execute(
            "SELECT id, when_json, screen_key FROM notes WHERE kind = ? AND invalid_at IS NULL"
            " AND status IN ('active', 'disputed', 'needs_recheck') AND id != ? ORDER BY id DESC LIMIT 200",
            (kind, mine),
        ).fetchall()
        for row in found:
            try:
                theirs = json.loads(row["when_json"] or "{}")
            except ValueError:
                continue
            if (
                str(row["screen_key"] or "") == screen
                and str(theirs.get("rid") or "") == rid
            ):
                return int(row["id"])
    except (sqlite3.Error, TypeError, ValueError, IndexError):
        logger.exception("knowledge feedback match failed")
    return None


def describe(fb: object) -> str:
    """A short plain line (no stored text beyond a clipped observation)."""
    data = fb if isinstance(fb, dict) else {}
    return "%s %s: %s" % (
        data.get("item_id"),
        data.get("verdict"),
        single_line(data.get("observation"), 80),
    )
