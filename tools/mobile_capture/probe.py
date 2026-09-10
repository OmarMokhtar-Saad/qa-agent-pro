"""The three-state decryption probe. Never a bool.

``run()`` drives ONE HTTPS request from the device, through the qa-agents
proxy, and answers exactly one of :data:`DECRYPTED` / :data:`TLS_FAILURE` /
:data:`INCONCLUSIVE`. Collapsing "the device is offline" into "the CA was
rejected" tells a tester to retry something that can never work until the
device comes back -- the borrowed invariant this module exists to keep, named
in the owner brief. So :data:`INCONCLUSIVE` is not a residual "something went
wrong" bucket for TLS problems: it is the state for every failure that says
nothing about whether our certificate would be trusted, which is why a dead
adb call, an unreachable endpoint, a probe timeout and a garbled reply all
land there rather than defaulting to :data:`TLS_FAILURE`.

:func:`classify` is the pure decision -- one raw ``adb.shell`` reply in, one
state out -- kept separate from :func:`run` so the fixture matrix that proves
the three-way split can drive it directly, without an event loop or a real
device.
"""

from __future__ import annotations

import logging
import re

from tools.mobile import adb

logger = logging.getLogger(__name__)

#: The probe's three states. A caller branches on these by NAME; nothing in
#: this lane ever reduces them to a boolean.
DECRYPTED = "decrypted"
TLS_FAILURE = "tls_failure"
INCONCLUSIVE = "inconclusive"
STATES = frozenset({DECRYPTED, TLS_FAILURE, INCONCLUSIVE})

#: One HTTPS request, on the packet path a tester is waiting on.
#: See tests/test_bounds_upper.py::CEILINGS.
PROBE_TIMEOUT_S: int = 15

#: An address that resolves to nothing real. The probe never needs the
#: endpoint to answer meaningfully -- only the qa-agents proxy addon (a later
#: phase) needs to recognise the request and stamp the marker header below;
#: until that addon exists, every real probe reads as INCONCLUSIVE, which is
#: the correct, honest answer for a run this lane has not wired yet.
PROBE_URL = "https://qa-agents-capture-probe.invalid/handshake"

#: The ONE marker this probe looks for in the response headers. Owned here,
#: not re-derived by the addon that will one day emit it.
MARKER_HEADER = "x-qa-agents-capture-probe"
MARKER_VALUE = "decrypted"

#: ``curl`` exit codes that name a TLS-layer failure specifically (bad
#: certificate, SSL connect error, peer verification failed, and similar) --
#: never a plain connection failure, which curl reports through a DIFFERENT
#: code and this probe must not conflate with a rejected certificate.
CURL_TLS_ERROR_CODES = frozenset({35, 51, 58, 60, 77, 82, 83})

#: adb answering on behalf of a device that is not there at all. This is
#: checked BEFORE the exit code, because a gone device also makes ``curl``
#: exit non-zero for reasons that have nothing to do with TLS.
_DEVICE_UNREACHABLE_RE = re.compile(
    r"device.*(?:offline|not found|unauthorized)|no devices/emulators found",
    re.IGNORECASE,
)


def _build_command(port: int) -> list[str]:
    """The ONE place the probe's ``curl`` invocation is built."""
    return [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-D",
        "-",
        "--max-time",
        str(int(PROBE_TIMEOUT_S)),
        "-x",
        "127.0.0.1:" + str(int(port)),
        PROBE_URL,
    ]


def classify(shell_result: object) -> str:
    """One raw ``adb.shell`` reply in, one of :data:`STATES` out. Never raises.

    The fixture matrix this is graded against varies device online/offline,
    proxy up/down, CA trusted/untrusted and response present/absent/garbled
    -- each dimension is read from a DIFFERENT part of *shell_result*, which
    is what lets a fixture isolate one clause: the device dimension reads
    ``error`` and the "device ... not found" text in ``err``; the proxy
    dimension and the TLS dimension both read ``rc`` but through disjoint
    sets (curl's TLS-specific codes vs. every other non-zero exit); the
    response dimension reads ``out`` only once ``rc == 0``.
    """
    if not isinstance(shell_result, dict):
        return INCONCLUSIVE
    if shell_result.get("error"):
        # adb itself could not run the command at all (spawn failure, adb
        # missing, adb-level timeout). The device may be fine; the probe
        # simply never reached it, which says nothing about TLS.
        return INCONCLUSIVE
    body = shell_result.get("content")
    body = body if isinstance(body, dict) else {}
    err = str(body.get("err") or "")
    if _DEVICE_UNREACHABLE_RE.search(err):
        # The device is offline or gone. THE headline case: this must never
        # read as TLS_FAILURE, or a tester is told to fix a certificate that
        # was never reached.
        return INCONCLUSIVE
    rc = body.get("rc")
    if rc is None:
        return INCONCLUSIVE
    try:
        rc = int(rc)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is the third one: int(float("inf")) raises it, and rc
        # comes from a device-supplied payload. A probe that raises here would
        # break the never-raise contract for the whole capture stage, on an
        # input a device can produce.
        return INCONCLUSIVE
    if rc in CURL_TLS_ERROR_CODES:
        return TLS_FAILURE
    if rc != 0:
        # A reachable device that still could not complete the request --
        # proxy down, connection refused, curl's own timeout. None of these
        # says the certificate was rejected, so this is INCONCLUSIVE, not
        # TLS_FAILURE.
        return INCONCLUSIVE
    out = str(body.get("out") or "")
    marker_line = (MARKER_HEADER + ": " + MARKER_VALUE).lower()
    if marker_line in out.lower():
        return DECRYPTED
    # rc == 0 but the marker line is absent, truncated or the body is
    # garbled: an unparseable answer, never a bare success and never a
    # failure state that implies something about TLS.
    return INCONCLUSIVE


async def run(serial: str, port: int) -> dict:
    """Drive ONE HTTPS request from *serial* through *port*. Never raises.

    Always ``{"error": None, "content": {"state": <one of STATES>}}`` -- the
    probe itself never fails upward. Every adb-level fault becomes
    INCONCLUSIVE *content* instead of an *error*, because a probe whose
    result a caller must handle two different ways (an error branch AND a
    three-state content branch) is exactly the kind of second answer this
    lane's "one producer" rule exists to prevent.
    """
    try:
        shell_result = await adb.shell(
            serial, _build_command(port), timeout=PROBE_TIMEOUT_S + 5
        )
        return {"error": None, "content": {"state": classify(shell_result)}}
    except Exception:
        logger.exception("mobile_capture.probe.run failed")
        return {"error": None, "content": {"state": INCONCLUSIVE}}
