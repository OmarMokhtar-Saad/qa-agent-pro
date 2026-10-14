"""CA constants, on-disk paths and the backend choice (leaf of ``ca``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from tools.mobile_capture import paths

# cryptography is optional: a Python where the wheel did not build has no CA backend.
try:
    import cryptography
except ImportError as _exc:
    if _exc.name != "cryptography":
        raise
    cryptography = cast("Any", None)

logger = logging.getLogger("tools.mobile_capture.ca")

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


def ca_backend() -> dict:
    """WHICH backend can mint the root, decided in ONE function.

    ``ensure_ca`` records the answer, so the record names its own maker. In this
    phase there is one backend; a second (the provisioned ``mitmdump``'s own
    confdir CA) is added HERE and nowhere else when that binary exists, so the
    choice never grows a second site.
    """
    if cryptography is None:
        return {
            "error": None,
            "content": {"backend": None, "reason": REASON_NO_CA_BACKEND},
        }
    return {
        "error": None,
        "content": {"backend": BACKEND_CRYPTOGRAPHY, "reason": None},
    }
