"""App Tester UI: what one session learns so the next lookup skips the scan.

* the list page a package's row was read at, per (serial, package), counted in
  scrolls from the list's top (only recorded when the top was proven);
* the Firebase app id of a package (``1:<number>:android:<hash>``), which makes
  the App Tester release page reachable by a link with no scrolling at all.

Process memory only: a wrong entry costs one verifying read, never a wrong tap.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote

_APP_ID_RE = re.compile(r"\d+:\d+:android:[0-9a-fA-F]+")
_LINK_RE = re.compile(r"appdistribution\.firebase\.google\.com/testerapps/([^/?#\s]+)")
LINK_BASE = "https://appdistribution.firebase.google.com/testerapps/"

_PAGES: dict = {}
_IDS: dict = {}


def parse_app_id(text: str) -> str:
    """The Firebase app id in a tester link (or a bare id) in *text*, else ``""``."""
    found = _LINK_RE.search(text or "")
    candidate = unquote(found.group(1)) if found else (text or "").strip()
    return candidate if _APP_ID_RE.fullmatch(candidate) else ""


def release_url(app_id: str) -> str:
    """The App Tester deep link of an app (its release list page)."""
    return LINK_BASE + app_id


def remember_app_id(package: str, text: str) -> None:
    """Learn the Firebase id of *package* from a link or id in *text* (if any)."""
    app_id = parse_app_id(text)
    if package and app_id:
        _IDS[package] = app_id


def known_app_id(package: str) -> str:
    return _IDS.get(package, "") if package else ""


def remember_page(serial: str, package: str, page: int) -> None:
    if package:
        _PAGES[(serial, package)] = page


def known_page(serial: str, package: str) -> Optional[int]:
    return _PAGES.get((serial, package)) if package else None


def forget_page(serial: str, package: str) -> None:
    _PAGES.pop((serial, package), None)


def reset() -> None:
    """Drop everything learned (tests, and a new device session)."""
    _PAGES.clear()
    _IDS.clear()
