"""API capture for the mobile lane -- paths, trust ledger and CA (Phase 1).

This package is a SIBLING of ``tools/mobile`` rather than ``tools/mobile/capture``
on purpose: ``tools/mobile_evidence/capture.py`` already exists and is imported
under the bare name ``capture`` in ``tools/mobile/case_runner.py`` and
``tools/mobile/session.py``. A second module reachable as ``capture`` in the
same call graph is one name with two meanings, which this project has shipped
before. Consumers import this one as ``from tools import mobile_capture as
api_capture``.

Every public function in this package returns ``{"error", "content"}`` and never
raises. Capture is EVIDENCE: an evidence fault may no more change a verdict than
a failed log slice can.

Phase 1 is deliberately device-free and network-free. Nothing here spawns a
process, opens a socket or calls adb; the proxy, the probe and the cert install
arrive in later phases.

The split of ownership this package assumes, stated once so a reader of one file
does not have to infer it: device trust is per SERIAL and outlives every run
(``ledger.py``); what was OBSERVED is per CASE and belongs to the run (later
phases, written under ``runs/``); a live proxy is neither -- it is a runtime
marker whose only job is to make the next run's reap possible after a crash.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def prepare(serial: str, *, owner: str = "", apply_: bool = False) -> dict:
    """The ONE orchestrator both call sites reuse (Phase 6, T3.2a/T4.2a): the
    consent stage inside ``handle_mobile_test`` and the standalone
    ``qa_setup_capture`` tool. Neither ever re-decides this on its own --
    they call this function and fold its reply into their own reply.

    ``owner`` is the caller's OWN device-lock label when it has one (the
    run's ``pre_run_owner``/``run_id``, per ``session.take_device_lock``'s
    reentrant-same-owner contract) -- passing it makes every lock take below
    a free re-entry. With no ``owner`` (the standalone tool, which has no
    run), this mints a throwaway provisioning label and releases it in a
    ``finally``.

    Never raises; always ``{"error": None, "content": <ladder.record(...)>}``.
    An evidence/provisioning fault is reported as a refusal-by-name tier, not
    an exception a caller has to separately handle.
    """
    from tools.mobile import session
    from tools.mobile_capture import (
        ca,
        cert,
        ladder,
        ledger,
        mitm_provision,
        probe,
        proxy,
    )

    serial = str(serial or "")
    minted = not str(owner or "").strip()
    label = str(owner or "").strip() or session.new_provisioning_owner()

    def _none(reason: str, *, fingerprint: str = "") -> dict:
        return {
            "error": None,
            "content": ladder.record(
                {
                    "tier": ladder.TIER_NONE,
                    "reason": reason,
                    "serial": serial,
                    "fingerprint": fingerprint,
                    "run_id": label,
                }
            ),
        }

    try:
        held = (session.take_device_lock(label, serial=serial) or {}).get(
            "content"
        ) or {}
        if not held.get("acquired"):
            return _none(proxy.REASON_DEVICE_BUSY)
        if not apply_:
            return _none(ladder.REASON_NO_APPLY)

        ca_made = ca.ensure_ca()
        fingerprint = (
            (ca_made.get("content") or {}).get("fingerprint")
            if not ca_made.get("error")
            else ""
        )
        prior = ledger.tier_for(serial, fingerprint) if fingerprint else {"content": {}}
        prior_tier = (prior.get("content") or {}).get("tier")

        installed = await cert.install(serial, apply=apply_, owner=label)
        if installed.get("error"):
            if installed["error"] == proxy.REASON_DEVICE_BUSY:
                return _none(installed["error"], fingerprint=fingerprint or "")
            return _none(ladder.REASON_CERT_NOT_TRUSTED, fingerprint=fingerprint or "")

        found = mitm_provision.find_mitmdump()
        binary = (
            (found.get("content") or {}).get("path") if not found.get("error") else ""
        )
        if not binary:
            provisioned = mitm_provision.provision(apply=apply_)
            binary = (
                (provisioned.get("content") or {}).get("path")
                if not provisioned.get("error")
                else ""
            )
        if not binary:
            return _none(ladder.REASON_NO_MITMDUMP, fingerprint=fingerprint or "")

        started = await proxy.start(serial, run_id=label)
        proxy_started = not started.get("error")
        started_content = started.get("content") if proxy_started else {}
        port = (started_content or {}).get("port")

        probe_state = None
        if proxy_started and port:
            probed = await probe.run(serial, port)
            probe_state = (
                (probed.get("content") or {}).get("state")
                if not probed.get("error")
                else None
            )

        decided = ladder.decide(
            consent=True,
            apply_=apply_,
            mitmdump_available=True,
            proxy_started=proxy_started,
            probe_state=probe_state,
            ledger_prior_tier=prior_tier,
        )
        if fingerprint:
            ledger.record(
                serial, fingerprint, decided["tier"], note=decided.get("reason") or ""
            )
        return {
            "error": None,
            "content": ladder.record(
                {
                    "tier": decided["tier"],
                    "reason": decided.get("reason"),
                    "serial": serial,
                    "fingerprint": fingerprint or "",
                    "run_id": label,
                    "started_ms": (started_content or {}).get("started_ms"),
                }
            ),
        }
    except Exception:
        logger.exception("mobile_capture.prepare failed")
        return _none(ladder.REASON_DEVICE_GONE)
    finally:
        if minted:
            session.release_device_lock(label, as_holder=True)
