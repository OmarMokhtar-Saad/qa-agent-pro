"""``prepare``'s helpers, moved out of ``__init__``; sibling imports stay lazy."""

from __future__ import annotations

from typing import NamedTuple, cast

from tools.mobile import device_lock
from tools.mobile_capture import ca, cert, ladder, ledger, mitm_provision, probe, proxy


class _Attempt(NamedTuple):
    """Who a reply is for: the device, the lock label and its CA fingerprint."""

    serial: str
    label: str
    fingerprint: str = ""


class _ProbeRun(NamedTuple):
    """What starting and probing the proxy produced."""

    proxy_started: bool
    started_content: dict
    probe_state: str | None


async def _prepare_locked(serial: str, label: str, apply_: bool) -> dict:
    """Take the device lock, install the CA, start and probe the proxy, decide."""

    refusal = _lock_or_refusal(serial, label)
    if refusal:
        return refusal
    if not apply_:
        return _none_reply(_Attempt(serial, label), ladder.REASON_NO_APPLY)

    fingerprint, prior_tier = _ca_fingerprint_and_prior(serial)
    who = _Attempt(serial, label, fingerprint)
    return await _provision_and_decide(who, prior_tier, apply_)


async def _provision_and_decide(
    who: _Attempt, prior_tier: str | None, apply_: bool
) -> dict:
    """Install the CA, find mitmdump, start and probe the proxy, then decide."""

    installed = await cert.install(who.serial, apply=apply_, owner=who.label)
    refusal = _install_refusal(installed, who)
    if refusal:
        return refusal

    if not _mitmdump_binary(apply_):
        return _none_reply(who, ladder.REASON_NO_MITMDUMP)

    run = await _start_and_probe(who.serial, who.label)
    return _decide_reply(who, prior_tier, run)


def _lock_or_refusal(serial: str, label: str) -> dict | None:
    """Take the device lock; the ``device_busy`` refusal when it is held, else ``None``."""

    held = (device_lock.take_device_lock(label, serial=serial) or {}).get(
        "content"
    ) or {}
    if held.get("acquired"):
        return None
    return _none_reply(_Attempt(serial, label), proxy.REASON_DEVICE_BUSY)


def _install_refusal(installed: dict, who: _Attempt) -> dict | None:
    """The refusal for a failed cert install, or ``None`` when it succeeded."""

    error = installed.get("error")
    if not error:
        return None
    if error in (proxy.REASON_DEVICE_BUSY, ladder.REASON_FLAG_OFF):
        return _none_reply(who, error)
    return _none_reply(who, ladder.REASON_CERT_NOT_TRUSTED)


def _none_reply(who: _Attempt, reason: str) -> dict:
    """A refusal-by-name reply at ``TIER_NONE``."""

    return {
        "error": None,
        "content": ladder.record(
            {
                "tier": ladder.TIER_NONE,
                "reason": reason,
                "serial": who.serial,
                "fingerprint": who.fingerprint or "",
                "run_id": who.label,
            }
        ),
    }


def _ca_fingerprint_and_prior(serial: str) -> tuple[str, str | None]:
    """Ensure the CA exists; return its fingerprint and the ledger's prior tier."""

    ca_made = ca.ensure_ca()
    fingerprint = (
        (ca_made.get("content") or {}).get("fingerprint")
        if not ca_made.get("error")
        else ""
    )
    prior = ledger.tier_for(serial, fingerprint) if fingerprint else {"content": {}}
    return cast("str", fingerprint), (prior.get("content") or {}).get("tier")


def _mitmdump_binary(apply_: bool) -> str:
    """Path of a usable mitmdump (found, else provisioned), or ``""``."""

    found = mitm_provision.find_mitmdump()
    binary = (found.get("content") or {}).get("path") if not found.get("error") else ""
    if binary:
        return binary
    provisioned = mitm_provision.provision(apply=apply_)
    return cast(
        "str",
        (provisioned.get("content") or {}).get("path")
        if not provisioned.get("error")
        else "",
    )


async def _start_and_probe(serial: str, label: str) -> _ProbeRun:
    """Start the proxy and probe it; ``(proxy_started, started_content, probe_state)``."""

    started = await proxy.start(serial, run_id=label)
    proxy_started = not started.get("error")
    started_content = (started.get("content") if proxy_started else {}) or {}
    port = started_content.get("port")

    probe_state = None
    if proxy_started and port:
        probed = await probe.run(serial, port)
        probe_state = (
            (probed.get("content") or {}).get("state")
            if not probed.get("error")
            else None
        )
    return _ProbeRun(proxy_started, started_content, probe_state)


def _decided_tier(prior_tier: str | None, run: _ProbeRun) -> dict:
    """Ask the ladder (always with ``apply_=True``)."""

    return ladder.decide(
        ladder.Situation(
            consent=True,
            apply_=True,
            mitmdump_available=True,
            proxy_started=run.proxy_started,
            probe_state=run.probe_state,
            ledger_prior_tier=prior_tier,
        )
    )


# Only reached once ``apply_`` is true (``prepare`` refuses earlier), so the
# ladder is always asked with ``apply_=True``.
def _decide_reply(who: _Attempt, prior_tier: str | None, run: _ProbeRun) -> dict:
    """Decide the tier, record it in the ledger and build the final reply."""

    decided = _decided_tier(prior_tier, run)
    if who.fingerprint:
        note = decided.get("reason") or ""
        entry = ledger.LedgerEntry(who.serial, who.fingerprint, decided["tier"], note)
        ledger.record(entry)
    return _final_reply(who, decided, run)


def _final_reply(who: _Attempt, decided: dict, run: _ProbeRun) -> dict:
    """The ``{"error", "content"}`` reply for a decided tier."""

    return {
        "error": None,
        "content": ladder.record(
            {
                "tier": decided["tier"],
                "reason": decided.get("reason"),
                "serial": who.serial,
                "fingerprint": who.fingerprint or "",
                "run_id": who.label,
                "started_ms": run.started_content.get("started_ms"),
            }
        ),
    }
