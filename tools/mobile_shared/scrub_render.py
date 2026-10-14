"""The text and JSON entry points of the redaction nets, and the ONE HTML escape."""

from __future__ import annotations

import html
import json

from tools.mobile_shared.scrub_arm import _SENSITIVE_VALUES
from tools.mobile_shared.scrub_keys import SENSITIVE_KEY_RE
from tools.mobile_shared.scrub_mask import mask_value
from tools.mobile_shared.scrub_nets import scrub_pairs, scrub_query
from tools.mobile_shared.scrub_prose import scrub_prose


def scrub_text(text: object) -> str:
    """Mask every armed value wherever it appears in free text, every sensitive
    key/value pair that survives as text (JSON-shaped OR query-shaped), and the
    labelled prose values. NOT a no-op on an unarmed render: the pair, query and
    prose passes always run."""
    out = scrub_prose(scrub_query(scrub_pairs("" if text is None else str(text))))
    values = _SENSITIVE_VALUES.get()
    if not values:
        return out
    # Longest first, so a value that contains another is masked whole rather than being
    # half-rewritten by its own substring.
    for value in sorted(values, key=len, reverse=True):
        if value in out:
            out = out.replace(value, mask_value(value))
    return out


def _mask_keyed(obj: object) -> object:
    if isinstance(obj, (dict, list)):
        # Masked as its serialised self, so two different subtrees still differ.
        return mask_value(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    text = str(obj)
    return obj if not text or text in ("None", "null") else mask_value(text)


def _scrub_scalar(obj: object) -> object:
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        # An id arriving as a NUMBER under a key nobody listed is still an id, but only the
        # value net can see it -- and only when this run presented it as a credential.
        return mask_value(obj) if str(obj) in _SENSITIVE_VALUES.get() else obj
    return obj


def scrub_json(obj: object, _key: object = None) -> object:
    """Walk a parsed body, masking by key name and by value.

    THE KEY IS CHECKED FIRST, BEFORE ANY RECURSION, and this order is the whole point.
    Recursing first would re-key each child by its OWN field name and throw the parent
    away, so ``{"patient_name": {"first": "John", "last": "Doe"}}`` would sail through
    untouched. A matched key masks its WHOLE value, scalar or subtree, and a list under a
    sensitive key is still that key's value.
    """
    if _key and SENSITIVE_KEY_RE.search(str(_key)):
        return _mask_keyed(obj)
    if isinstance(obj, dict):
        return {key: scrub_json(value, key) for key, value in obj.items()}
    if isinstance(obj, list):
        return [scrub_json(value, _key) for value in obj]
    return _scrub_scalar(obj)


def e(value: object, quote: bool = True) -> str:
    """Escape for HTML -- and mask on the way through. THIS IS THE CHOKE POINT."""
    return html.escape(
        scrub_text(value if isinstance(value, str) else str(value)), quote
    )
