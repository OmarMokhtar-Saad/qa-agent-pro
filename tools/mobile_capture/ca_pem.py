"""Reading the CA PEM, its fingerprint and Android hashed name (leaf of ``ca``)."""

from __future__ import annotations

import hashlib
import logging
import ssl
from pathlib import Path

from cryptography import x509

from tools.mobile_capture import ca_base

logger = logging.getLogger("tools.mobile_capture.ca")


def _size_refusal(path: Path) -> dict | None:
    size = path.stat().st_size
    if size <= ca_base.MAX_CA_BYTES:
        return None
    logger.warning(
        "mobile_capture.ca: %s is %d bytes, over MAX_CA_BYTES -- refusing",
        path,
        size,
    )
    return {"error": ca_base.REASON_CA_TOO_LARGE, "content": None}


def _read_text(path: Path) -> dict:
    return {
        "error": None,
        "content": {
            "pem": path.read_text(encoding="utf-8"),
            "path": str(path),
        },
    }


def read_pem(target: Path | None = None) -> dict:
    """The CA PEM text, bounded by ``MAX_CA_BYTES``. Never raises."""
    path = ca_base.cert_path() if target is None else Path(target)
    try:
        if not path.is_file():
            return {"error": ca_base.REASON_NO_CA_ON_DISK, "content": None}
        refusal = _size_refusal(path)
        if refusal is not None:
            return refusal
        return _read_text(path)
    except Exception as exc:
        logger.exception("mobile_capture.ca.read_pem failed")
        return {"error": str(exc), "content": None}


#: Refusal code for :func:`android_hashed_name`, kept distinct from
#: :data:`REASON_NOT_A_PEM` even though it is reached through the same
#: ``fingerprint``/``read_pem`` plumbing -- a cert-install caller branches on
#: THIS name, not on the DER-parsing one two layers down, so a future
#: refactor of ``read_pem`` cannot silently change what it sees.
REASON_HASH_FAILED = "android_hash_failed"


def _subject_hash(pem: str) -> str:

    cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    subject_der = cert.subject.public_bytes()
    digest = hashlib.md5(subject_der).digest()
    value = int.from_bytes(digest[:4], byteorder="little", signed=False)
    return format(value, "08x")


# The on-device filename Android's system trust store expects for
# *target* (the CA certificate by default): ``<8 hex chars>.0``.
#
# This is OpenSSL's ``subject_hash_old`` (``X509_NAME_hash_old``): the
# first four bytes of the MD5 digest of the certificate's DER-encoded
# SUBJECT name, read as a little-endian ``uint32`` and printed as lowercase
# hex. Deferred out of Phase 1 for exactly this reason -- it needed a real
# certificate to grade against, not a hand-written fixture.
#
# ``cryptography``'s ``Name.public_bytes()`` returns the SAME DER bytes
# that were embedded in the certificate this module minted (this module is
# the one and only writer of that ``x509.Name``), so no independent
# re-encoding step exists to drift from what is actually on disk.
#
# Never raises: ``{"error", "content": {"hash": "1a2b3c4d", "filename":
# "1a2b3c4d.0"}}``.
def android_hashed_name(target: Path | None = None) -> dict:
    """The on-device ``<8 hex chars>.0`` filename Android's trust store expects."""
    got = read_pem(target)
    if got["error"]:
        return got
    try:
        hashed = _subject_hash(got["content"]["pem"])
        return {
            "error": None,
            "content": {"hash": hashed, "filename": hashed + ".0"},
        }
    except Exception:
        logger.exception("mobile_capture.ca.android_hashed_name failed")
        return {"error": REASON_HASH_FAILED, "content": None}


# Computed from the DER rather than the PEM TEXT: re-writing the same
# certificate with different line wrapping, a different header comment or a
# different trailing newline must not invent a new device identity, because
# the ledger is keyed on this string and a new identity silently discards
# every device's earned trust.
def fingerprint(target: Path | None = None) -> dict:
    """SHA-256 of the certificate's DER, lowercase hex. The ONE producer."""
    got = read_pem(target)
    if got["error"]:
        return got
    try:
        der = ssl.PEM_cert_to_DER_cert(got["content"]["pem"])
    except ValueError:
        logger.warning(
            "mobile_capture.ca: %s is not a PEM certificate",
            got["content"]["path"],
        )
        return {"error": ca_base.REASON_NOT_A_PEM, "content": None}
    return {
        "error": None,
        "content": {
            "fingerprint": hashlib.sha256(der).hexdigest(),
            "path": got["content"]["path"],
        },
    }
