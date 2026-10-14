"""App-log evidence under ``evidence/`` (plan mobile-app-evidence, P2).

:func:`write_evidence_text` writes one scrubbed text file (``tc_id`` None =
run-level); :func:`write_evidence_json` writes one JSON document through the
redacting writer; :func:`list_evidence` gives ``{tc_id or "": [names]}`` for
everything under ``evidence/``; :func:`read_evidence_text` returns one file as
text, or ``content: None`` when it does not exist.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from tools.mobile import run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


class EvidenceRef(NamedTuple):
    run_id: str
    tc_id: object
    name: str


def _evidence_path(run_id: str, tc_id: object, name: str) -> Path | None:
    if not run_io.valid_run_id(run_id):
        return None
    if not (isinstance(name, str) and run_consts._EVIDENCE_NAME_RE.match(name)):
        return None
    root = run_io.run_path(run_id) / run_consts.EVIDENCE_DIR
    if tc_id is None or tc_id == "":
        return root / name
    if not run_io.valid_tc_id(tc_id):
        return None
    return root / str(tc_id) / name


def _refused(run_id: str, tc_id: object, name: str) -> dict:
    return {
        "error": "Refusing evidence path "
        + repr((str(run_id)[:40], str(tc_id)[:20], str(name)[:60])),
        "content": None,
    }


def write_evidence_text(ref: EvidenceRef, text: str) -> dict:
    """Write one scrubbed text file under ``evidence/`` (``tc_id`` None = run-level)."""
    try:
        target = _evidence_path(*ref)
        if target is None:
            return _refused(*ref)
        run_io._write_text(target, text)
        return {"error": None, "content": {"path": str(target)}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_evidence_text failed")
        return {"error": str(exc), "content": None}


def write_evidence_json(ref: EvidenceRef, payload: object) -> dict:
    """Write one JSON document under ``evidence/`` through the redacting writer."""
    try:
        target = _evidence_path(*ref)
        if target is None:
            return _refused(*ref)
        run_io._write_json(target, payload)
        return {"error": None, "content": {"path": str(target)}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_evidence_json failed")
        return {"error": str(exc), "content": None}


def _names_in(child: Path) -> list[str]:
    return sorted(
        grandchild.name
        for grandchild in child.iterdir()
        if grandchild.is_file() and run_consts._EVIDENCE_NAME_RE.match(grandchild.name)
    )


def list_evidence(run_id: str) -> dict:
    """``{tc_id or "": [names]}`` for everything under ``evidence/``."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        root = run_io.run_path(run_id) / run_consts.EVIDENCE_DIR
        out: dict = {}
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if child.is_file() and run_consts._EVIDENCE_NAME_RE.match(child.name):
                    out.setdefault("", []).append(child.name)
                elif child.is_dir() and run_io.valid_tc_id(child.name):
                    out[child.name] = _names_in(child)
        return {"error": None, "content": out}
    except Exception as exc:
        logger.exception("mobile.run_store.list_evidence failed")
        return {"error": str(exc), "content": None}


def read_evidence_text(run_id: str, tc_id: object, name: str) -> dict:
    """One evidence file as text, or ``content: None`` when it does not exist."""
    try:
        target = _evidence_path(run_id, tc_id, name)
        if target is None:
            return {"error": "Invalid evidence path.", "content": None}
        if not target.is_file():
            return {"error": None, "content": None}
        return {
            "error": None,
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }
    except Exception as exc:
        logger.exception("mobile.run_store.read_evidence_text failed")
        return {"error": str(exc), "content": None}
