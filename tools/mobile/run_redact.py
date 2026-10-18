"""Redaction at the WRITE, not at the reader.

:mod:`tools.mobile.run_mask_id` adds the value rules: ID-like digit runs masked
to the last four, credential-field values redacted, password nodes blanked.

:func:`redact` runs inside every write path. It masks two things: any value
under a key in ``SECRET_VALUE_KEYS`` inside an object marked ``secret: true``,
and any value under a key in ``ALWAYS_MASKED_KEYS`` wherever it appears.

Every string also passes :func:`tools.mobile.act_scrub.scrub`, which removes each
secret value the process has been given (typed into a credential field, marked
``secret`` or supplied as a tester input) whatever key it sits under.

:func:`redact` is applied on every write in the store. Structure is preserved --
the report still shows that a credential was typed into a named field, which is
what a tester needs, without showing the credential.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import act_scrub, run_consts, run_mask_id


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


def _leaf(key: object, value: object) -> Any:
    # Structural keys (bounds, ids, class) are never value-scrubbed.
    if isinstance(value, str) and str(key).lower() in act_scrub.STRUCTURAL_KEYS:
        return value
    return redact(value)


# Applied on every write in this module. Structure is preserved -- the report
# still shows that a credential was typed into a named field, which is what a
# tester needs, without showing the credential.
def redact(obj: object) -> Any:
    """Recursively mask the value of anything marked ``secret``."""
    if isinstance(obj, str):
        return act_scrub.scrub(obj)
    if isinstance(obj, dict):
        act_scrub.register_action(obj)
        out: dict = {key: _leaf(key, value) for key, value in obj.items()}
        _mask_marked(out)
        _mask_always(out)
        run_mask_id.mask_node(out)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    return obj
