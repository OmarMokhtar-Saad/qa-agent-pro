"""Our own persistent root CA: generate ONCE, fingerprint, read.

The fingerprint is what the ledger is keyed on, so this module is the ONE
producer of that string. Anything else deriving a fingerprint of its own would
be a second answer to one question, and the two would drift the first time one
of them changed hash or encoding.

Phase 1 is device-free: nothing here installs, removes or reads a DEVICE's
certificate store. Structural trust is not provable that way anyway -- the
system store is unreadable on a non-rooted device -- so trust is earned
functionally in a later phase, and this module only owns the certificate itself.

Facade: the code lives in ``ca_base`` (constants, paths, backend), ``ca_pem``
(read, fingerprint, Android name), ``ca_gen`` (minting and writing) and
``ca_make`` (``ensure_ca``).
"""

from __future__ import annotations

from tools.mobile_capture.ca_base import (  # noqa: F401
    BACKEND_CRYPTOGRAPHY,
    CERT_NAME,
    KEY_NAME,
    MAX_CA_BYTES,
    META_NAME,
    REASON_CA_TOO_LARGE,
    REASON_NO_CA_BACKEND,
    REASON_NO_CA_ON_DISK,
    REASON_NOT_A_PEM,
    VALIDITY_DAYS,
    ca_backend,
    cert_path,
    key_path,
    meta_path,
)
from tools.mobile_capture.ca_gen import (  # noqa: F401
    _self_signed_root,
    _write_secret,
    generate_with_cryptography,
)
from tools.mobile_capture.ca_make import (  # noqa: F401
    _existing_ca_reply,
    _read_meta,
    _write_meta,
    ensure_ca,
)
from tools.mobile_capture.ca_pem import (  # noqa: F401
    REASON_HASH_FAILED,
    android_hashed_name,
    fingerprint,
    read_pem,
)
