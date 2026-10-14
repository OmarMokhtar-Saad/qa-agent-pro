"""The shared core of the machine report: ``Row``, the caps, the install facts.

Every ``machine_report_*`` leaf builds on this module; it imports none of them.
See ``tools/machine_report.py`` for what the report is and the rule that keeps
it from drifting from the doctor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tools import updater

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("tools.machine_report")

#: The closed status vocabulary. ONE producer, one meaning: a consumer that
#: receives something outside this tuple has met a bug, not a new state, and a
#: client renders an unknown status as ``undetermined`` rather than silently
#: dropping the row -- a dropped row is how a machine looks healthy while a
#: component nobody rendered is failing.
STATUSES = ("ok", "warn", "fail", "off", "undetermined")

#: How many rows ONE SECTION of a reply may carry (so `section="all"` carries at
#: most that many per section it contains). Bounds the payload a polling GUI parses
#: and renders, not the work: every builder below enumerates a bounded set
#: already (a fixed component list, the MCP clients on this machine), so
#: this is the backstop against a future
#: section that enumerates something device-sized -- a run's cases, a corpus --
#: and turns a polled reply into a multi-megabyte one. Applied in ONE place,
#: ``_rows``, so no builder can forget it and no two can disagree about it.
MAX_ROWS = 200

#: The detail every "off" row carries on a build with no mobile modules.
NO_MOBILE_DETAIL = "The mobile modules are not present in this edition."


@dataclass(frozen=True)
class Row:
    """One component's verdict, in the shape a table renders directly."""

    component: str
    status: str
    detail: str
    fix_hint: str = ""
    #: For a client row, the config file the WIZARD would edit. Empty for every
    #: other row. It is carried rather than re-derived because the app deciding
    #: which file to back up and merge must use the path the SERVER checked --
    #: two derivations of "where is Cursor's config" is how a backup lands
    #: beside one file and the merge lands in another.
    config_path: str = ""
    #: For a row about a BINARY, the absolute path that binary was found at.
    #: Carried as a field rather than left inside ``detail`` because the desktop
    #: app has to know WHERE a tool is, and a GUI reading a path out of an
    #: English sentence is the silent consumer of an unpromised format that this
    #: module exists to prevent. Empty for every row that is not about a binary.
    tool_path: str = ""

    def as_dict(self) -> dict:
        out = asdict(self)
        if out["status"] not in STATUSES:
            out["status"] = "undetermined"
        return out


def _rows(items) -> list:
    """Serialise, normalise and CAP -- the one place the cap is applied."""
    out: list[Any] = []
    for item in items:
        if len(out) >= MAX_ROWS:
            logger.info("machine_report: row cap reached at %d", MAX_ROWS)
            break
        out.append(item.as_dict())
    return out


def install_dir() -> Path:
    """THIS install's root -- the directory ``tools/updater`` versions and the
    one a registered MCP entry must point at to be current.

    One producer for both questions: a second derivation of "where am I" is how
    a version row and a registration row end up disagreeing about the same
    install.
    """
    return Path(updater._INSTALL_DIR)


def local_version() -> str | None:
    """The installed version, read from THIS install's directory.

    ``updater._local_version`` takes the install directory as an ARGUMENT -- it
    has no default -- so calling it bare raises, and a swallowed raise here
    would report every machine as "unknown" forever.
    """
    return updater._local_version(install_dir())


#: What a probe of this machine can raise: a missing module, a bad shape, a
#: failed OS call, or a timeout. Anything else is a bug and should surface.
_PROBE_ERRORS = (
    ImportError,
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
    RuntimeError,
    OSError,
    asyncio.TimeoutError,
)


def backend_info(edition_probe: Callable[[], dict] | None = None) -> dict:
    """Version AND edition in one call -- what a client needs to decide whether
    the backend it found is one it can talk to.

    ``edition_probe`` supplies the edition facts; with none, or when it raises,
    the edition is ``{}``, which a client reads as "unknown"."""
    version, root = "unknown", ""
    try:
        version = str(local_version() or "unknown")
        root = str(install_dir())
    except _PROBE_ERRORS as exc:  # never raises: a client polls this
        logger.info("machine_report: version unavailable: %s", exc)
    edition = {}
    try:
        edition = edition_probe() if edition_probe else {}
    except _PROBE_ERRORS as exc:
        logger.info("machine_report: edition unavailable: %s", exc)
    return {"version": version, "install_dir": root, "edition": edition}
