"""Install sources: where an app's new build comes from.

Strategy interface plus a registry, so a caller asks for a source by name and a
new one (a Firebase API download, a local APK, a store) plugs in without editing
the callers. Today only the on-device Firebase App Tester flow ships
(``install_sources.app_tester_ui``) -- there is no credentialed API source.

Two rules every implementation obeys:

* ask, do not guess: an ambiguous release goes through ``resolve.resolve_or_ask``;
* never destroy to succeed: an install that needs an uninstall (signature
  mismatch) returns ``needs_confirmation="uninstall"`` and the CALLER asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from tools.mobile.resolve import AskCb, Candidate
from tools.mobile.tree_source import TreeResult

#: Releases offered in one question or one listing.
MAX_RELEASES_LISTED = 10


@dataclass(frozen=True)
class Release:
    key: str
    label: str = ""
    version_name: str = ""
    version_code: Optional[int] = None


@dataclass(frozen=True)
class InstallRequest:
    serial: str
    package: str
    app_label: str = ""
    release: Optional[Release] = None
    target_version_code: Optional[int] = None


@dataclass(frozen=True)
class InstallOutcome:
    """``code`` is a machine reason (``INSTALL_FAILED_*``, ``not_found`` ...);
    ``needs_confirmation`` is non-empty when the CALLER must ask the tester
    before a destructive follow-up (``"uninstall"``)."""

    ok: bool
    code: str = ""
    message: str = ""
    version_code: Optional[int] = None
    needs_confirmation: str = ""


class DeviceUi(Protocol):
    """What an on-device source may do to a screen. Implemented over adb by
    ``adb_device_ui``; faked in tests."""

    serial: str

    async def screen(self) -> Optional[TreeResult]: ...

    async def tap_text(self, text: str) -> bool: ...

    async def tap_resource_id(self, rid: str) -> bool: ...

    async def press_back(self) -> None: ...

    async def scroll_down(self) -> None: ...

    async def scroll_up(self) -> None: ...

    async def launch(self, package: str) -> bool: ...

    async def foreground(self) -> str: ...


class InstallSource(Protocol):
    name: str

    async def available(self, serial: str) -> bool: ...

    async def list_releases(self, request: InstallRequest) -> list: ...

    async def install(
        self, request: InstallRequest, ask: Optional[AskCb] = None
    ) -> InstallOutcome: ...


_SOURCES: dict = {}


def register_source(source: InstallSource) -> None:
    _SOURCES[str(source.name).strip().lower()] = source


def unregister_source(name: str) -> None:
    _SOURCES.pop(str(name).strip().lower(), None)


def get_source(name: str) -> Optional[InstallSource]:
    return _SOURCES.get(str(name or "").strip().lower())


def source_names() -> list:
    return sorted(_SOURCES)


def clip_releases(releases: Sequence[Release]) -> list:
    return list(releases)[:MAX_RELEASES_LISTED]


def release_candidates(releases: Sequence[Release]) -> list:
    """Releases as resolve-or-ask candidates (clipped to the listing cap)."""
    out = []
    for rel in clip_releases(releases):
        detail = "versionCode " + str(rel.version_code) if rel.version_code else ""
        out.append(Candidate(rel.key, rel.label or rel.version_name, detail))
    return out
