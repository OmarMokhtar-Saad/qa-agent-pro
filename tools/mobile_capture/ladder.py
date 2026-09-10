"""The ONE producer of the capture tier vocabulary, the refuse-by-name
sentinel, and the capture record shape.

``decide()`` maps this RUN's own observations -- consent, apply, whether
``mitmdump`` is provisioned, whether the proxy started, and the functional
probe state -- to a tier. Trust is proven FUNCTIONALLY: :data:`TIER_DECRYPTED`
is reachable ONLY from ``probe_state == probe.DECRYPTED``, a first genuinely
decrypted request, never from reading a cert store or a ledger row. The
ledger's cached tier for a device (:mod:`tools.mobile_capture.ledger`) is a
PRIOR about the DEVICE across earlier runs -- accepted here as
``ledger_prior_tier`` purely so a caller can be tested against it -- and it is
never, on its own, enough to produce this run's tier: a device trusted last
week that decrypts nothing THIS run is not reported as decrypted this run.
Skipping the install/consent steps for an already-trusted device (T4.2a) is a
decision the ORCHESTRATOR makes before ever calling this function; this
function only ever answers "what did THIS run observe".

``refusal()`` is the ONE producer of the refuse-by-name sentinel string, and
``record()`` is the ONE producer of the capture record shape -- every key
present, the same discipline ``tools/mobile/case_runner.py::network_record``
uses for the pcap lane's record, applied here to this lane's.
"""

from __future__ import annotations

from tools.mobile_capture import probe

#: The tier vocabulary. A caller branches on these by NAME.
TIER_NONE = "none"
TIER_PROXIED = "proxied"
TIER_DECRYPTED = "decrypted"
TIER_TLS_FAILURE = "tls_failure"
TIER_INCONCLUSIVE = "inconclusive"
TIERS = frozenset(
    {
        TIER_NONE,
        TIER_PROXIED,
        TIER_DECRYPTED,
        TIER_TLS_FAILURE,
        TIER_INCONCLUSIVE,
    }
)

#: The refusal vocabulary. Every code below has exactly one sentence in
#: ``_REASON_SENTENCES``, proven by ``test_every_reason_code_has_a_sentence_``
#: ``and_no_two_share_one``.
REASON_NO_CONSENT = "no_consent"
REASON_NO_APPLY = "no_apply"
REASON_NO_MITMDUMP = "no_mitmdump"
REASON_CERT_NOT_TRUSTED = "cert_not_trusted"
REASON_PINNED = "pinned"
REASON_TLS_FAILURE = "tls_failure"
REASON_PROXY_START_FAILED = "proxy_start_failed"
REASON_DEVICE_GONE = "device_gone"
REASON_USER_STORE_ONLY = "user_store_only"
REASON_INCONCLUSIVE = "inconclusive"
REASONS = frozenset(
    {
        REASON_NO_CONSENT,
        REASON_NO_APPLY,
        REASON_NO_MITMDUMP,
        REASON_CERT_NOT_TRUSTED,
        REASON_PINNED,
        REASON_TLS_FAILURE,
        REASON_PROXY_START_FAILED,
        REASON_DEVICE_GONE,
        REASON_USER_STORE_ONLY,
        REASON_INCONCLUSIVE,
    }
)

#: One sentence per reason code. THE ONE PRODUCER of the tester-facing
#: sentence for a refusal -- a rendered sentence can change without any
#: caller's branch changing, because callers branch on the CODE.
_REASON_SENTENCES = {
    REASON_NO_CONSENT: (
        "The tester declined API capture, so this run continues without it."
    ),
    REASON_NO_APPLY: (
        "API capture needs apply=true to install or start anything; nothing "
        "was touched."
    ),
    REASON_NO_MITMDUMP: (
        "mitmdump is not provisioned yet, so no proxy could be started."
    ),
    REASON_CERT_NOT_TRUSTED: (
        "The device did not accept the qa-agents root certificate."
    ),
    REASON_PINNED: (
        "The app pins its own certificates, so its requests fail once our "
        "certificate is installed."
    ),
    REASON_TLS_FAILURE: (
        "The proxy could not complete a TLS handshake through the device."
    ),
    REASON_PROXY_START_FAILED: (
        "The qa-agents proxy did not start, so this run continues without "
        "capture."
    ),
    REASON_DEVICE_GONE: (
        "The device disconnected before capture could be confirmed."
    ),
    REASON_USER_STORE_ONLY: (
        "Only the user certificate store accepted the CA; most apps on this "
        "device ignore it."
    ),
    REASON_INCONCLUSIVE: (
        "Capture could not be confirmed one way or the other on this run."
    ),
}


def refusal(code: object) -> dict:
    """The ONE producer of the refuse-by-name sentinel. Never a bare string.

    An unrecognised code still gets a sentence naming itself rather than a
    generic "not available" -- the half-wired-sentinel failure this lane's
    consumer sweep (T4.3) exists to catch.
    """
    code = str(code)
    sentence = _REASON_SENTENCES.get(code)
    if sentence is None:
        sentence = "Capture stopped (" + code + ")."
    return {"reason": code, "message": sentence}


def decide(
    *,
    consent: bool,
    apply_: bool,
    mitmdump_available: bool,
    device_gone: bool = False,
    proxy_started: bool = False,
    probe_state: str | None = None,
    pinned: bool = False,
    user_store_only: bool = False,
    ledger_prior_tier: str | None = None,
) -> dict:
    """``{"tier": ..., "reason": ... | None}``. The ONE place a tier is decided.

    ``ledger_prior_tier`` is accepted only so a caller (and this module's own
    tests) can prove it is INERT for this decision: passing
    ``TIER_DECRYPTED`` here changes nothing about the result, because this
    run's tier comes from ``probe_state`` alone. A structural cert check --
    reading that a certificate is installed, or that the ledger once recorded
    success -- can never produce :data:`TIER_DECRYPTED` through this
    function.
    """
    del ledger_prior_tier  # intentionally not read; see the docstring above.
    if not consent:
        return {"tier": TIER_NONE, "reason": REASON_NO_CONSENT}
    if not apply_:
        return {"tier": TIER_NONE, "reason": REASON_NO_APPLY}
    if not mitmdump_available:
        return {"tier": TIER_NONE, "reason": REASON_NO_MITMDUMP}
    if device_gone:
        return {"tier": TIER_NONE, "reason": REASON_DEVICE_GONE}
    if not proxy_started:
        return {"tier": TIER_NONE, "reason": REASON_PROXY_START_FAILED}
    if probe_state == probe.DECRYPTED:
        return {"tier": TIER_DECRYPTED, "reason": None}
    if probe_state == probe.TLS_FAILURE:
        return {
            "tier": TIER_TLS_FAILURE,
            "reason": REASON_PINNED if pinned else REASON_TLS_FAILURE,
        }
    if user_store_only:
        return {"tier": TIER_PROXIED, "reason": REASON_USER_STORE_ONLY}
    return {"tier": TIER_PROXIED, "reason": REASON_INCONCLUSIVE}


def record(source: object) -> dict:
    """The capture record shape. EVERY KEY PRESENT, normalised from a
    ``decide()`` reply, a checkpoint already on disk, or nothing at all --
    the ``tools/mobile/case_runner.py::network_record`` precedent, applied to
    this lane. Never raises: a corrupt or missing value on disk reads as this
    record's own empty value, not a KeyError inside a checkpoint splice.
    """
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    body = body if isinstance(body, dict) else {}

    tier = body.get("tier")
    tier = tier if tier in TIERS else TIER_NONE
    reason = body.get("reason")
    reason = reason if reason in REASONS else None
    try:
        flow_count = max(0, int(body.get("flow_count") or 0))
    except (TypeError, ValueError, OverflowError):
        flow_count = 0

    return {
        "tier": tier,
        "reason": reason,
        "message": refusal(reason)["message"] if reason else None,
        "serial": str(body.get("serial") or ""),
        "fingerprint": str(body.get("fingerprint") or ""),
        "run_id": str(body.get("run_id") or ""),
        "started_ms": body.get("started_ms"),
        "flow_count": flow_count,
    }
