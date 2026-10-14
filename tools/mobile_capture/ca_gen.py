"""Minting the self-signed root and writing it to disk (leaf of ``ca``)."""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tools.mobile_capture import ca_base

logger = logging.getLogger("tools.mobile_capture.ca")


def _open_private(target: Path, mode: int) -> tuple[Path, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    handle = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    return tmp, handle


def _write_secret(target: Path, data: bytes, mode: int) -> None:
    """Atomic write at *mode*, with the mode set BEFORE the file is visible.

    ``chmod`` after ``replace`` leaves a window in which the private key is
    world-readable, and a window is all a shared machine needs.
    """
    tmp, handle = _open_private(target, mode)
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


def _new_key():

    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def _write_pair(key, cert) -> None:

    _write_secret(
        ca_base.key_path(),
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        0o600,
    )
    _write_secret(
        ca_base.cert_path(), cert.public_bytes(serialization.Encoding.PEM), 0o644
    )


def generate_with_cryptography() -> None:
    """Mint a self-signed root. KEY first, CERTIFICATE last.

    The order is load-bearing: ``ensure_ca`` treats "both files present" as
    "already generated", so a crash between the two writes must leave the pair
    INCOMPLETE rather than a certificate with no key behind it.
    """
    key = _new_key()
    cert = _self_signed_root(key)
    _write_pair(key, cert)


def _subject_name():

    return x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "qa-agents API capture"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "qa-agents"),
        ]
    )


def _key_usage():

    return x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False,
    )


def _build_cert(key, name):

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=ca_base.VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert


def _self_signed_root(key):
    """The self-signed CA certificate for ``key`` (SHA-256, cert-sign only)."""
    return _build_cert(key, _subject_name())
