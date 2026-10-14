"""Infer the install source from the words the tester already wrote.

Pure text in, install-source KEY out (see ``render_menu.INSTALL_SOURCES``).
``""`` means "nothing matched, or two sources matched": the caller then asks,
because guessing between two lanes is the failure the install menu exists for.
"""

from __future__ import annotations

import re

_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_APK = re.compile(r"\S+\.apk\b", re.IGNORECASE)

# Keyword -> source. Matched on lower-cased text.
_APP_TESTER = (
    "app tester",
    "apptester",
    "app distribution",
)
_PLAY_STORE = ("play store", "playstore", "google play")


def _url_source(url: str) -> str:
    """The source a link points at: store page, App Tester page, or a download."""
    host = url.lower().split("/")[2] if url.count("/") >= 2 else ""
    if "play.google.com" in host:
        return "play_store"
    if host == "appdistribution.firebase.google.com":
        return "app_tester"
    return "download_url"


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def infer_install_source(text: str) -> str:
    """The one install source *text* names, else ``""``."""
    raw = str(text or "")
    lowered = raw.lower()
    found = {_url_source(url) for url in _URL.findall(raw)}
    if _APK.search(_URL.sub(" ", raw)):
        found.add("local_apk")
    if _has_any(lowered, _APP_TESTER):
        found.add("app_tester")
    if _has_any(lowered, _PLAY_STORE):
        found.add("play_store")
    return found.pop() if len(found) == 1 else ""
