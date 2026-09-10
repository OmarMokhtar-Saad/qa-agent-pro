"""Our own persistent root CA: generate ONCE, fingerprint, read.

The fingerprint is what the ledger is keyed on, so this module is the ONE
producer of that string. Anything else deriving a fingerprint of its own would
be a second answer to one question, and the two would drift the first time one
of them changed hash or encoding.

Phase 1 is device-free: nothing here installs, removes or reads a DEVICE's
certificate store. Structural trust is not provable that way anyway -- the
system store is unreadable on a non-rooted device -- so trust is earned
functionally in a later phase, and this module only owns the certificate itself.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import ssl
from pathlib import Path

from tools.mobile_capture import paths

logger = logging.getLogger(__name__)

#: One PEM read off disk into memory, in bytes.
MAX_CA_BYTES: int = 65536

#: The backend that minted the root. Recorded on disk beside the certificate,
#: so "who made this" is a fact rather than an inference from whichever machine
#: happens to be reading it later.
BACKEND_CRYPTOGRAPHY = "cryptography"

#: Refusal codes. Constants rather than prose so a caller branches by NAME and
#: a rendered sentence can be changed without breaking a branch.
REASON_NO_CA_BACKEND = "no_ca_backend"
REASON_CA_TOO_LARGE = "ca_too_large"
REASON_NO_CA_ON_DISK = "no_ca_on_disk"
REASON_NOT_A_PEM = "not_a_pem_certificate"

CERT_NAME = "qa-agents-ca.pem"
KEY_NAME = "qa-agents-ca-key.pem"
META_NAME = "ca.json"

#: How long the root is valid. Long, because the ledger's entire value is that a
#: device's trust survives; a root that expires mid-programme re-asks every
#: tester at once, on a day nobody chose.
VALIDITY_DAYS = 3650


def cert_path() -> Path:
    return paths.ca_dir() / CERT_NAME


def key_path() -> Path:
    return paths.ca_dir() / KEY_NAME


def meta_path() -> Path:
    return paths.ca_dir() / META_NAME


def _ca_backend() -> dict:
    """WHICH backend can mint the root, decided in ONE function.

    ``ensure_ca`` records the answer, so the record names its own maker. In this
    phase there is one backend; a second (the provisioned ``mitmdump``'s own
    confdir CA) is added HERE and nowhere else when that binary exists, so the
    choice never grows a second site.
    """
    try:
        import cryptography  # noqa: F401
    except Exception:
        return {
            "error": None,
            "content": {"backend": None, "reason": REASON_NO_CA_BACKEND},
        }
    return {
        "error": None,
        "content": {"backend": BACKEND_CRYPTOGRAPHY, "reason": None},
    }


def read_pem(target: Path | None = None) -> dict:
    """The CA PEM text, bounded by ``MAX_CA_BYTES``. Never raises."""
    path = cert_path() if target is None else Path(target)
    try:
        if not path.is_file():
            return {"error": REASON_NO_CA_ON_DISK, "content": None}
        size = path.stat().st_size
        if size > MAX_CA_BYTES:
            logger.warning(
                "mobile_capture.ca: %s is %d bytes, over MAX_CA_BYTES -- refusing",
                path,
                size,
            )
            return {"error": REASON_CA_TOO_LARGE, "content": None}
        return {
            "error": None,
            "content": {
                "pem": path.read_text(encoding="utf-8"),
                "path": str(path),
            },
        }
    except Exception as exc:
        logger.exception("mobile_capture.ca.read_pem failed")
        return {"error": str(exc), "content": None}


#: Refusal code for :func:`android_hashed_name`, kept distinct from
#: :data:`REASON_NOT_A_PEM` even though it is reached through the same
#: ``fingerprint``/``read_pem`` plumbing -- a cert-install caller branches on
#: THIS name, not on the DER-parsing one two layers down, so a future
#: refactor of ``read_pem`` cannot silently change what it sees.
REASON_HASH_FAILED = "android_hash_failed"


def android_hashed_name(target: Path | None = None) -> dict:
    """The on-device filename Android's system trust store expects for
    *target* (the CA certificate by default): ``<8 hex chars>.0``.

    This is OpenSSL's ``subject_hash_old`` (``X509_NAME_hash_old``): the
    first four bytes of the MD5 digest of the certificate's DER-encoded
    SUBJECT name, read as a little-endian ``uint32`` and printed as lowercase
    hex. Deferred out of Phase 1 for exactly this reason -- it needed a real
    certificate to grade against, not a hand-written fixture.

    ``cryptography``'s ``Name.public_bytes()`` returns the SAME DER bytes
    that were embedded in the certificate this module minted (this module is
    the one and only writer of that ``x509.Name``), so no independent
    re-encoding step exists to drift from what is actually on disk.

    Never raises: ``{"error", "content": {"hash": "1a2b3c4d", "filename":
    "1a2b3c4d.0"}}``.
    """
    got = read_pem(target)
    if got["error"]:
        return got
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(got["content"]["pem"].encode("utf-8"))
        subject_der = cert.subject.public_bytes()
        digest = hashlib.md5(subject_der).digest()
        value = int.from_bytes(digest[:4], byteorder="little", signed=False)
        hashed = format(value, "08x")
        return {
            "error": None,
            "content": {"hash": hashed, "filename": hashed + ".0"},
        }
    except Exception:
        logger.exception("mobile_capture.ca.android_hashed_name failed")
        return {"error": REASON_HASH_FAILED, "content": None}


def fingerprint(target: Path | None = None) -> dict:
    """SHA-256 of the certificate's DER, lowercase hex. The ONE producer.

    Computed from the DER rather than the PEM TEXT: re-writing the same
    certificate with different line wrapping, a different header comment or a
    different trailing newline must not invent a new device identity, because
    the ledger is keyed on this string and a new identity silently discards
    every device's earned trust.
    """
    got = read_pem(target)
    if got["error"]:
        return got
    try:
        der = ssl.PEM_cert_to_DER_cert(got["content"]["pem"])
    except Exception:
        logger.warning(
            "mobile_capture.ca: %s is not a PEM certificate",
            got["content"]["path"],
        )
        return {"error": REASON_NOT_A_PEM, "content": None}
    return {
        "error": None,
        "content": {
            "fingerprint": hashlib.sha256(der).hexdigest(),
            "path": got["content"]["path"],
        },
    }


def _write_secret(target: Path, data: bytes, mode: int) -> None:
    """Atomic write at *mode*, with the mode set BEFORE the file is visible.

    ``chmod`` after ``replace`` leaves a window in which the private key is
    world-readable, and a window is all a shared machine needs.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    handle = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(handle, data)
        os.fsync(handle)
    finally:
        os.close(handle)
    try:
        os.chmod(tmp, mode)
    except OSError:
        # Windows ignores POSIX modes; the mobile lane discloses that platform
        # as unverified rather than claiming a permission it did not set.
        logger.info("mobile_capture.ca: could not set mode on %s", tmp)
    os.replace(tmp, target)


def _generate_with_cryptography() -> None:
    """Mint a self-signed root. KEY first, CERTIFICATE last.

    The order is load-bearing: ``ensure_ca`` treats "both files present" as
    "already generated", so a crash between the two writes must leave the pair
    INCOMPLETE rather than a certificate with no key behind it.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "qa-agents API capture"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "qa-agents"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write_secret(
        key_path(),
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        0o600,
    )
    _write_secret(cert_path(), cert.public_bytes(serialization.Encoding.PEM), 0o644)


def _read_meta() -> dict:
    try:
        if not meta_path().is_file():
            return {}
        raw = json.loads(meta_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        logger.warning("mobile_capture.ca: %s is unreadable", meta_path())
        return {}


def ensure_ca() -> dict:
    """Generate the root ONCE. ``{path, key_path, fingerprint, created, backend}``.

    Idempotent by design: a second call over an existing, READABLE certificate
    does not regenerate. Regenerating would change the fingerprint, which
    invalidates every ledger row and every device that already trusts us -- a
    silent mass re-ask that would look like the feature simply not working.

    Refuses BY NAME (``REASON_NO_CA_BACKEND``) when no backend can mint one,
    rather than returning a bare ``None`` a caller has to guess about.
    """
    try:
        tree = paths.ensure_tree()
        if tree["error"]:
            return {"error": tree["error"], "content": None}
        if cert_path().is_file() and key_path().is_file():
            got = fingerprint()
            if not got["error"]:
                return {
                    "error": None,
                    "content": {
                        "path": str(cert_path()),
                        "key_path": str(key_path()),
                        "fingerprint": got["content"]["fingerprint"],
                        "created": False,
                        "backend": _read_meta().get("backend"),
                    },
                }
            logger.warning(
                "mobile_capture.ca: the CA on disk is unusable (%s) -- regenerating",
                got["error"],
            )
        chosen = _ca_backend()["content"]
        if chosen["backend"] != BACKEND_CRYPTOGRAPHY:
            return {"error": chosen["reason"] or REASON_NO_CA_BACKEND, "content": None}
        _generate_with_cryptography()
        got = fingerprint()
        if got["error"]:
            return {"error": got["error"], "content": None}
        _write_secret(
            meta_path(),
            json.dumps(
                {
                    "backend": BACKEND_CRYPTOGRAPHY,
                    "fingerprint": got["content"]["fingerprint"],
                    "created_ms": int(
                        datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
                    ),
                },
                indent=2,
                sort_keys=True,
            ).encode("utf-8"),
            0o600,
        )
        return {
            "error": None,
            "content": {
                "path": str(cert_path()),
                "key_path": str(key_path()),
                "fingerprint": got["content"]["fingerprint"],
                "created": True,
                "backend": BACKEND_CRYPTOGRAPHY,
            },
        }
    except Exception as exc:
        logger.exception("mobile_capture.ca.ensure_ca failed")
        return {"error": str(exc), "content": None}
