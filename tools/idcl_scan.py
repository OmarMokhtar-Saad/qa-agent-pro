"""Scan leaf of the identifier-collision detector."""

from __future__ import annotations

from tools import idcl_bind
from tools.idcl_text import _ID_RE, _is_translation

_MAX_OCCURRENCES = 2_000
MAX_IDENTIFIERS = 200
MAX_BINDINGS_PER_ID = 40
_MAX_OVERLAP = 0.0


def _ident_of(m) -> str:
    return f"{m.group(1).upper()}{int(m.group(2)):02d}"


def _bucket_for(by_id: dict, order: list, ident: str) -> list | None:
    if ident not in by_id:
        if len(by_id) >= MAX_IDENTIFIERS:
            return None
        by_id[ident] = []
        order.append(ident)
    return by_id[ident]


def collect_bindings(raw: str) -> tuple[dict, list]:
    """Bindings per normalised identifier, plus the identifiers in first-seen order."""
    by_id: dict = {}
    order: list = []
    seen = 0
    for line in raw.splitlines():
        if seen > _MAX_OCCURRENCES:
            break
        for m in _ID_RE.finditer(line):
            seen += 1
            if seen > _MAX_OCCURRENCES:
                break
            bucket = _bucket_for(by_id, order, _ident_of(m))
            if bucket is None or len(bucket) >= MAX_BINDINGS_PER_ID:
                continue
            bucket.extend(idcl_bind.bindings_in_line(line, m.start(), m.end()))
    return by_id, order


def first_conflict(bucket: list) -> tuple | None:
    """The first (definition, other) pair in ``bucket`` that truly disagrees."""
    for d in bucket:
        if d[0] != "definition":
            continue
        for other in bucket:
            if other is d:
                continue
            if _is_translation(d[2], other[2]):
                continue
            if len(d[2] & other[2]) / len(d[2] | other[2]) > _MAX_OVERLAP:
                continue
            return (d, other)
    return None


def conflict_labels(hit: tuple) -> list:
    labels: list = []
    for b in hit:
        if b[1] and b[1] not in labels:
            labels.append(b[1])
    return labels
