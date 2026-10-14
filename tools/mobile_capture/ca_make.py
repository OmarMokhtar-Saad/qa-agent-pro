"""The ``ensure_ca`` flow: reuse a readable CA or generate one (leaf of ``ca``)."""

from __future__ import annotations

import datetime
import json
import logging

from tools.mobile_capture import ca_base, ca_gen, ca_pem, paths

logger = logging.getLogger("tools.mobile_capture.ca")


def _read_meta() -> dict:
    try:
        if not ca_base.meta_path().is_file():
            return {}
        raw = json.loads(ca_base.meta_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, RecursionError):
        logger.warning("mobile_capture.ca: %s is unreadable", ca_base.meta_path())
        return {}


def _meta_backend():
    return _read_meta().get("backend")


def _existing_reply(fp: str) -> dict:
    return {
        "error": None,
        "content": {
            "path": str(ca_base.cert_path()),
            "key_path": str(ca_base.key_path()),
            "fingerprint": fp,
            "created": False,
            "backend": _meta_backend(),
        },
    }


def _existing_ca_reply() -> dict | None:
    """The ``created: False`` reply for a readable CA on disk, else ``None``."""
    if not (ca_base.cert_path().is_file() and ca_base.key_path().is_file()):
        return None
    got = ca_pem.fingerprint()
    if got["error"]:
        logger.warning(
            "mobile_capture.ca: the CA on disk is unusable (%s) -- regenerating",
            got["error"],
        )
        return None
    return _existing_reply(got["content"]["fingerprint"])


def _write_meta(fp: str) -> None:
    ca_gen._write_secret(
        ca_base.meta_path(),
        json.dumps(
            {
                "backend": ca_base.BACKEND_CRYPTOGRAPHY,
                "fingerprint": fp,
                "created_ms": int(
                    datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
                ),
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
        0o600,
    )


def _created_reply(fp: str) -> dict:
    return {
        "error": None,
        "content": {
            "path": str(ca_base.cert_path()),
            "key_path": str(ca_base.key_path()),
            "fingerprint": fp,
            "created": True,
            "backend": ca_base.BACKEND_CRYPTOGRAPHY,
        },
    }


def _create_ca() -> dict:
    chosen = ca_base.ca_backend()["content"]
    if chosen["backend"] != ca_base.BACKEND_CRYPTOGRAPHY:
        reason = chosen["reason"] or ca_base.REASON_NO_CA_BACKEND
        return {"error": reason, "content": None}
    ca_gen.generate_with_cryptography()
    got = ca_pem.fingerprint()
    if got["error"]:
        return {"error": got["error"], "content": None}
    _write_meta(got["content"]["fingerprint"])
    return _created_reply(got["content"]["fingerprint"])


# Idempotent by design: a second call over an existing, READABLE certificate
# does not regenerate. Regenerating would change the fingerprint, which
# invalidates every ledger row and every device that already trusts us -- a
# silent mass re-ask that would look like the feature simply not working.
#
# Refuses BY NAME (``REASON_NO_CA_BACKEND``) when no backend can mint one,
# rather than returning a bare ``None`` a caller has to guess about.
def ensure_ca() -> dict:
    """Generate the root ONCE. ``{path, key_path, fingerprint, created, backend}``."""
    try:
        tree = paths.ensure_tree()
        if tree["error"]:
            return {"error": tree["error"], "content": None}
        existing = _existing_ca_reply()
        if existing is not None:
            return existing
        return _create_ca()
    except Exception as exc:
        logger.exception("mobile_capture.ca.ensure_ca failed")
        return {"error": str(exc), "content": None}
