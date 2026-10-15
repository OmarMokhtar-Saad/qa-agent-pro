"""App Tester UI: what earlier lookups learned so the next one skips the scan.

* the list page a package's row was read at, per (serial, package), counted in
  scrolls from the list's top (only recorded when the top was proven);
* the Firebase app id of a package (``1:<number>:android:<hash>``), which makes
  the App Tester release page reachable by a link with no scrolling at all;
* the newest build the app page listed (``latest``), trusted for minutes, so an
  installed copy that already is it skips the App Tester entirely.

Persisted by ``app_tester_ui_store`` (survives the process): a wrong entry
costs one verifying read, never a wrong tap.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote

from tools.mobile.install_sources import app_tester_ui_store as store

_APP_ID_RE = re.compile(r"\d+:\d+:android:[0-9a-fA-F]+")
_LINK_RE = re.compile(r"appdistribution\.firebase\.google\.com/testerapps/([^/?#\s]+)")
LINK_BASE = "https://appdistribution.firebase.google.com/testerapps/"


def _key(serial: str, package: str) -> str:
    return serial + "|" + package


def parse_app_id(text: str) -> str:
    """The Firebase app id in a tester link (or a bare id anywhere) in *text*, else ``""``."""
    found = _LINK_RE.search(text or "")
    candidate = unquote(found.group(1)) if found else (text or "")
    hit = _APP_ID_RE.search(candidate)
    return hit.group(0) if hit else ""


def release_url(app_id: str) -> str:
    """The App Tester deep link of an app (its release list page)."""
    return LINK_BASE + app_id


def remember_app_id(package: str, text: str) -> None:
    """Learn the Firebase id of *package* from a link or id in *text* (if any)."""
    app_id = parse_app_id(text)
    if package and app_id:
        store.put("ids", package, app_id)


def known_app_id(package: str) -> str:
    return str(store.get("ids", package) or "") if package else ""


def remember_page(serial: str, package: str, page: int) -> None:
    if package:
        store.put("pages", _key(serial, package), page)


def known_page(serial: str, package: str) -> Optional[int]:
    page = store.get("pages", _key(serial, package)) if package else None
    return page if isinstance(page, int) else None


def forget_page(serial: str, package: str) -> None:
    store.drop("pages", _key(serial, package))
    store.drop("spages", _key(serial, package))


def remember_start_page(serial: str, package: str, page: int) -> None:
    """A page counted from the screen the scan started on (the top is unproven)."""
    if package:
        store.put("spages", _key(serial, package), page)


def known_start_page(serial: str, package: str) -> Optional[int]:
    page = store.get("spages", _key(serial, package)) if package else None
    return page if isinstance(page, int) else None


def _label_key(serial: str, label: str) -> str:
    return _key(serial, " ".join((label or "").lower().split()))


def remember_label(serial: str, label: str, bundle: str) -> None:
    """The bundle id the label picked (the row's identity for the next call)."""
    if (label or "").strip() and bundle:
        store.put("rows", _label_key(serial, label), bundle)


def known_bundle(serial: str, label: str) -> str:
    if not (label or "").strip():
        return ""
    return str(store.get("rows", _label_key(serial, label)) or "")


def remember_latest(serial: str, package: str, code: int) -> None:
    """The highest versionCode the app's page listed."""
    if package and code:
        store.put("latest", _key(serial, package), code)


def known_latest(serial: str, package: str) -> Optional[int]:
    code = store.get("latest", _key(serial, package)) if package else None
    return code if isinstance(code, int) else None


def reset() -> None:
    """Drop everything learned (tests, and a new device session)."""
    store.clear()
