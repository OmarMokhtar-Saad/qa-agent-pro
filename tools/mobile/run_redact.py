"""Redaction at the WRITE, not at the reader.

:mod:`tools.mobile.run_mask_id` adds the value rules: ID-like digit runs masked
to the last four, credential-field values redacted, password nodes blanked.

:func:`redact` runs inside every write path. It masks two things: any value
under a key in ``SECRET_VALUE_KEYS`` inside an object marked ``secret: true``,
and any value under a key in ``ALWAYS_MASKED_KEYS`` wherever it appears.

It is still not a value scrubber: a credential stored under an unrecognised key
name with no marker WILL reach disk, so a new writer of tester-supplied values
must mark it.

:func:`redact` is applied on every write in the store. Structure is preserved --
the report still shows that a credential was typed into a named field, which is
what a tester needs, without showing the credential.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import run_consts, run_mask_id


def _is_secret(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _mask_marked(out: dict) -> None:
    if _is_secret(out.get("secret")):
        for key in run_consts.SECRET_VALUE_KEYS:
            if key in out:
                out[key] = run_consts.SECRET_MASK


def _mask_always(out: dict) -> None:
    # Mark-INDEPENDENT. The marked pass only fires inside an object that
    # declared itself secret, so a caller that forgets the marker used to
    # put the value on disk in clear. These key names are credentials by
    # name wherever they appear.
    for key in list(out):
        if str(key).lower() in run_consts.ALWAYS_MASKED_KEYS and isinstance(
            out[key], (str, bytes)
        ):
            out[key] = run_consts.SECRET_MASK


# Applied on every write in this module. Structure is preserved -- the report
# still shows that a credential was typed into a named field, which is what a
# tester needs, without showing the credential.
def redact(obj: object) -> Any:
    """Recursively mask the value of anything marked ``secret``."""
    if isinstance(obj, dict):
        out: dict = {key: redact(value) for key, value in obj.items()}
        _mask_marked(out)
        _mask_always(out)
        run_mask_id.mask_node(out)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    return obj
