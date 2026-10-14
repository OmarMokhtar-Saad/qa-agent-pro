"""The ``adb devices`` parser (stdlib-only leaf of ``tools.device_ids``)."""

from __future__ import annotations

from tools.device_ids_check import _valid_device_id

#: Lines of ``adb devices`` output parsed. adb prints one line per transport,
#: and a machine with a hub full of phones is a handful; this exists only so a
#: wedged adb server streaming garbage cannot grow the work unboundedly.
_MAX_ADB_LINES = 200

#: Characters of a raw state token kept for the tester-facing text. adb's
#: "no permissions ..." line carries a sentence AND a URL; the tester needs the
#: state named, not the essay, and this string is rendered into a chat reply.
_MAX_ADB_STATE_CHARS = 64

#: adb transport states this server can name. ANYTHING ELSE is reported as
#: ``unknown`` carrying its raw token -- an adb release can add a state, and a
#: tester looking at a plugged-in phone must be told something rather than
#: nothing. Silently dropping the line is the defect this parser exists to fix.
#:
#: THIS VOCABULARY BOUNDS ``state``, NOT ``raw_state``: the unknown branch keeps
#: whatever adb printed, and ``-l`` metadata is arbitrary device-supplied text.
#: That is why the renderer wraps those two fields as untrusted rather than
#: claiming an exemption on the strength of this frozenset.
_ADB_KNOWN_STATES = frozenset(
    {
        "device",
        "unauthorized",
        "offline",
        "no_permissions",
        "authorizing",
        "connecting",
        "bootloader",
        "recovery",
        "sideload",
        "rescue",
        "host",
    }
)


class AdbRows(list):
    """The parsed rows, PLUS how many input lines were never read.

    A plain list can only report what it contains; the defect this closes is
    what it silently does NOT -- 500 attached transports came back as 199 rows
    with 301 dropped and no signal to the row list, the caller or the tester,
    which is the exact failure the parser's own docstring says it exists to
    prevent, surviving above the cap.

    A synthetic trailing row was the alternative and was rejected: it would
    flow into ``_android_row``, into the tester's pick list and into
    ``mobile.adb.devices``' usable filter as a device-shaped lie told to fix a
    device-list lie. ``len``, ``==``, iteration and slicing are unchanged here,
    so no existing reader is disturbed.
    """

    #: Class-level default so the attribute EXISTS even on an instance some
    #: future path builds without setting it -- absent is worse than zero.
    dropped_lines: int = 0


# THE ONE PARSER. ``tools/mobile/adb.devices`` and ``_list_android_all`` both
# read it, because the same answer derived twice in two places drifts -- and
# it did: both sites carried ``parts[1] == "device"`` and both dropped an
# ``unauthorized`` phone without a word, so a tester holding a plugged-in
# handset was told "No devices detected."
#
# Returns one dict per line::
#
#     {serial, state, raw_state, metadata, valid_id, usable}
#
# ``state`` is a NAME from ``_ADB_KNOWN_STATES`` or ``"unknown"``;
# ``raw_state`` is what adb actually printed, truncated; ``metadata`` holds
# the ``key:value`` tokens ``adb devices -l`` appends (``model:``, ``usb:``,
# ``transport_id:``, and -- the trap for a future rewrite -- ``device:``).
# ``usable`` is true only for a ``device`` line whose serial passes the id
# whitelist.
#
# WINDOWS/CRLF, MEASURED: no carriage-return handling is needed or present.
# ``splitlines()`` treats ``\\r\\n`` and a lone ``\\r`` as terminators, and the
# ``strip()`` below removes a trailing one, so no ``\\r`` can reach a token.
# An earlier revision carried a neutralisation clause here; deleting it -- and
# even splitting on ``"\\n"`` instead -- was executed and produced BYTE-
# IDENTICAL rows, so the clause was inert and is gone rather than left behind
# with a mutant that cannot die.
#
# Never raises. Anything unparseable is simply not a row.
def parse_adb_devices(text: object) -> AdbRows:
    """Classify every line of ``adb devices`` / ``adb devices -l`` output."""
    candidates = _adb_candidate_lines(text)
    rows = AdbRows()
    # ONE PRODUCER of "how much did we not read", computed at the one site that
    # drops the lines. Deriving it a second time from the same text in a caller
    # is how two answers of one question drift apart.
    rows.dropped_lines = max(0, len(candidates) - _MAX_ADB_LINES)
    for line in candidates[:_MAX_ADB_LINES]:
        row = _adb_row(line)
        if row is not None:
            rows.append(row)
    return rows


# NOISE FIRST, THEN THE CAP. Counting raw lines made the header, blank
# padding and daemon chatter look like dropped transports: one emulator
# behind 250 blank lines reported 52 dropped and adb.devices REFUSED, which
# is the fail-closed path -- so a machine with a device plainly attached
# lost find_running, list_running, device_alive and the qa-doctor probe,
# and was told a sentence about transports that was not true.
def _adb_candidate_lines(text: object) -> list[str]:
    """The non-noise lines of adb output, uncapped."""
    candidates = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        if line.startswith("*"):  # "* daemon started successfully *"
            continue
        candidates.append(line)
    return candidates


# THE STATE IS THE TOKEN IMMEDIATELY AFTER THE SERIAL. Not a historical
# defect -- the code this replaces read `parts[1]`, one positional
# token, and was never fooled. The rule is stated, and fixtured, against
# a plausible REWRITE of this parser ("state = any token equal to
# device", an easy way to think you are handling -l), which would read
# an UNAUTHORIZED -l row carrying `device:emu64x` as usable.
def _state_of(rest: list[str]) -> tuple[str, int]:
    """``(state, tokens consumed)`` from the tokens after the serial."""
    first = rest[0].lower()
    if first == "no" and len(rest) > 1 and rest[1].lower().startswith("permission"):
        return "no_permissions", 2
    return (first if first in _ADB_KNOWN_STATES else "unknown"), 1


def _metadata_of(tokens: list[str]) -> dict[str, str]:
    """The ``key:value`` tokens ``adb devices -l`` appends after the state."""
    metadata: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition(":")
        if sep and key.isidentifier():
            metadata[key] = value[:_MAX_ADB_STATE_CHARS]
    return metadata


def _adb_row(line: str) -> dict | None:
    """One classified row for a single adb line, or None when it has no state."""
    parts = line.split()
    if len(parts) < 2:
        return None
    serial = parts[0]
    rest = parts[1:]
    state, consumed = _state_of(rest)
    valid_id = _valid_device_id(serial)
    return {
        "serial": serial,
        "state": state,
        "raw_state": " ".join(rest[:consumed])[:_MAX_ADB_STATE_CHARS],
        "metadata": _metadata_of(rest[consumed:]),
        "valid_id": valid_id,
        "usable": state == "device" and valid_id,
    }
