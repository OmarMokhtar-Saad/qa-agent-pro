"""Refuse, rather than drop, an undeclared key that looks like a safety switch.

``dryrun=true`` is not ``dry_run``: dropping it would run the action LIVE
(``apply`` defaults on), and the "ignored" note only arrives afterwards. A key
that is close to a declared name, or that names a safety concept, is refused
before anything runs. Names only are ever used; never a value.
"""

from __future__ import annotations

import difflib
import re

CUTOFF = 0.8
SENSITIVE = ("dry", "apply", "preview", "grant", "confirm", "consent", "permission")


def _norm(name: object) -> str:
    return re.sub(r"[-_\s.]", "", str(name)).lower()


def _sensitive(name: str) -> bool:
    return any(word in _norm(name) for word in SENSITIVE)


def closest(key: str, declared: set[str]) -> str | None:
    """The declared name *key* is probably a typo of, or None to just drop it."""
    by_norm = {_norm(d): d for d in sorted(declared)}
    near = difflib.get_close_matches(_norm(key), list(by_norm), 1, CUTOFF)
    if near:
        return by_norm[near[0]]
    if _sensitive(key):
        return next((d for d in sorted(declared) if _sensitive(d)), "")
    return None


def refusal(refused: dict[str, str]) -> str:
    """The error text: each unknown name and the declared one to use instead."""
    parts = [
        f"'{k}' (did you mean '{v}'?)" if v else f"'{k}'" for k, v in refused.items()
    ]
    return (
        "Refused, nothing was run: unknown safety-related argument(s) "
        + ", ".join(parts)
        + ". Resend with the exact declared argument name."
    )
