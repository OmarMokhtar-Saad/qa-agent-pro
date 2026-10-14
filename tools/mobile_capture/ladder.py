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

from dataclasses import dataclass

from tools.mobile_capture import probe
from tools.mobile_capture.ladder_reasons import (  # noqa: F401  re-exports: the vocabulary moved
    _REASON_SENTENCES,
    REASON_CERT_NOT_TRUSTED,
    REASON_DEVICE_GONE,
    REASON_FLAG_OFF,
    REASON_INCONCLUSIVE,
    REASON_NO_APPLY,
    REASON_NO_CONSENT,
    REASON_NO_MITMDUMP,
    REASON_NOT_OFFERED,
    REASON_PROXY_START_FAILED,
    REASON_TLS_FAILURE,
    REASON_USER_STORE_ONLY,
    REASONS,
)
from tools.mobile_shared.capture_tiers import (  # noqa: F401  re-exports: tier names keep their old home
    REASON_PINNED,
    TIER_DECRYPTED,
    TIER_INCONCLUSIVE,
    TIER_NONE,
    TIER_PROXIED,
    TIER_TLS_FAILURE,
    TIERS,
)


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


@dataclass(frozen=True)
class Situation:
    """What THIS run observed; the one argument of :func:`decide`."""

    consent: bool
    apply_: bool
    mitmdump_available: bool
    device_gone: bool = False
    proxy_started: bool = False
    probe_state: str | None = None
    pinned: bool = False
    user_store_only: bool = False
    # Intentionally never read by decide(); see its docstring.
    ledger_prior_tier: str | None = None


def _gate_refusal(s: Situation) -> dict | None:
    """The first unmet precondition as a no-tier reply, else None."""
    if not s.consent:
        return {"tier": TIER_NONE, "reason": REASON_NO_CONSENT}
    if not s.apply_:
        return {"tier": TIER_NONE, "reason": REASON_NO_APPLY}
    if not s.mitmdump_available:
        return {"tier": TIER_NONE, "reason": REASON_NO_MITMDUMP}
    if s.device_gone:
        return {"tier": TIER_NONE, "reason": REASON_DEVICE_GONE}
    if not s.proxy_started:
        return {"tier": TIER_NONE, "reason": REASON_PROXY_START_FAILED}
    return None


def _probe_tier(s: Situation) -> dict:
    """The tier for a run whose proxy started, from the functional probe."""
    if s.probe_state == probe.DECRYPTED:
        return {"tier": TIER_DECRYPTED, "reason": None}
    if s.probe_state == probe.TLS_FAILURE:
        return {
            "tier": TIER_TLS_FAILURE,
            "reason": REASON_PINNED if s.pinned else REASON_TLS_FAILURE,
        }
    if s.user_store_only:
        return {"tier": TIER_PROXIED, "reason": REASON_USER_STORE_ONLY}
    return {"tier": TIER_PROXIED, "reason": REASON_INCONCLUSIVE}


def decide(situation: Situation) -> dict:
    """``{"tier": ..., "reason": ... | None}``. The ONE place a tier is decided.

    ``Situation.ledger_prior_tier`` is accepted only so a caller (and this
    module's own tests) can prove it is INERT for this decision: passing
    ``TIER_DECRYPTED`` there changes nothing about the result, because this
    run's tier comes from ``probe_state`` alone. A structural cert check --
    reading that a certificate is installed, or that the ledger once recorded
    success -- can never produce :data:`TIER_DECRYPTED` through this
    function.
    """
    return _gate_refusal(situation) or _probe_tier(situation)


def _body_of(source: object) -> dict:
    """The dict a record is read from: ``content`` when present, else *source*."""
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    return body if isinstance(body, dict) else {}


def _flow_count(body: dict) -> int:
    """``flow_count`` as a non-negative int; a corrupt value reads as 0."""
    try:
        return max(0, int(body.get("flow_count") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _shape(body: dict) -> dict:
    """Every key of the capture record, normalised from *body*."""
    tier = body.get("tier")
    reason = body.get("reason")
    reason = reason if reason in REASONS else None
    return {
        "tier": tier if tier in TIERS else TIER_NONE,
        "reason": reason,
        # Carried by the ONE producer so a consumer can branch on it. The
        # renderer read this key before anything emitted it, which is how a
        # released build showed no offer, never prompted a tester, and
        # captured nothing on every run.
        "offer": bool(body.get("offer")),
        "message": refusal(reason)["message"] if reason else None,
        "serial": str(body.get("serial") or ""),
        "fingerprint": str(body.get("fingerprint") or ""),
        "run_id": str(body.get("run_id") or ""),
        "started_ms": body.get("started_ms"),
        "flow_count": _flow_count(body),
    }


def record(source: object) -> dict:
    """The capture record shape. EVERY KEY PRESENT, normalised from a
    ``decide()`` reply, a checkpoint already on disk, or nothing at all --
    the ``tools/mobile/case_runner.py::network_record`` precedent, applied to
    this lane. Never raises: a corrupt or missing value on disk reads as this
    record's own empty value, not a KeyError inside a checkpoint splice.
    """
    return _shape(_body_of(source))
