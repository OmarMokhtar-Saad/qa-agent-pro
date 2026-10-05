"""Which devices the tester allowed the uiautomator2 helper on.

The helper installs APKs on the tester's device, so the choice is the TESTER's:
:func:`enable` takes a :class:`~tools.mobile.user_confirm.UserConsent`, which only
``user_confirm.confirm`` can mint after an elicitation answer, and checks that it
was given for THIS serial's action. No ``qa_*`` tool takes a consent-like
argument, so the host model has nothing to pass.

Default OFF: an empty or unreadable store means no serial opted in. Bounded to
:data:`MAX_OPTIN_SERIALS` (oldest dropped). The store holds device serials only.
"""

from __future__ import annotations

import json
import logging
import os
import re

from tools.mobile import paths, tree_source
from tools.mobile.user_confirm import UserConsent

logger = logging.getLogger(__name__)

STATE_FILE = "u2-optin.json"

#: The registry name of the uiautomator2 source (see ``u2_tree_source``).
U2_SOURCE = "uiautomator2"

#: Serials remembered as opted in; the oldest is dropped first.
MAX_OPTIN_SERIALS = 32

_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def action_for(serial: str) -> str:
    """The action string a consent must name for ``serial``."""
    return "u2-helper:" + str(serial or "")


def _load() -> list:
    try:
        raw = json.loads(paths.state_file(STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("serials") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    clean = [s for s in items if isinstance(s, str) and _SERIAL_RE.match(s)]
    return clean[-MAX_OPTIN_SERIALS:]


def _save(serials: list) -> bool:
    path = paths.state_file(STATE_FILE)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"serials": serials[-MAX_OPTIN_SERIALS:]}), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError:
        logger.warning("tree_optin: could not write %s", path, exc_info=True)
        return False
    return True


def is_enabled(serial: str) -> bool:
    return str(serial or "") in _load()


def enable(serial: str, consent: object) -> bool:
    """Opt ``serial`` in. Raises TypeError unless ``consent`` is a genuine
    :class:`UserConsent`, ValueError unless it names this serial's action.
    Returns False when the choice could not be saved. State is untouched on a
    raise."""
    if type(consent) is not UserConsent:
        raise TypeError("enable needs the UserConsent from user_confirm.confirm")
    key = str(serial or "")
    if not _SERIAL_RE.match(key):
        raise ValueError("unusable device serial")
    if consent.action != action_for(key):
        raise ValueError("that consent was given for a different action")
    return _save([s for s in _load() if s != key] + [key])


def disable(serial: str) -> bool:
    key = str(serial or "")
    return _save([s for s in _load() if s != key])


def allowed(name: str, serial: str) -> bool:
    """The predicate for ``tree_source.select_tree_source``: the fallback is
    always allowed, the uiautomator2 source only for an opted-in serial, and
    any other name never."""
    if name == tree_source.FALLBACK_SOURCE:
        return True
    return name == U2_SOURCE and is_enabled(serial)
