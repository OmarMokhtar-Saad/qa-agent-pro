"""Updater release-signature keyring and MANIFEST.sig verification (split from tools/updater.py)."""

from __future__ import annotations

import base64
from pathlib import Path

from config.settings_guard import never_raise
from tools import settings_port
from tools.updater_log import logger
from tools.updater_paths import _MANIFEST_NAME, _MANIFEST_SIG_NAME

# Ed25519 release-signing PUBLIC keys (hex, 32 bytes each): the KEYRING. A
# release is trusted when ANY key here verifies its MANIFEST.sig. The matching
# PRIVATE keys are held ONLY by the release maintainer and never live in this
# repo. Releases now ship UNSIGNED (publisher signing was removed; see
# docs/DECISIONS.md), so this verifier is dormant. Empty => no embedded
# key => signature verification is inert (logged) and the
# QA_UPDATE_REQUIRE_SIGNATURE gate decides whether an unsigned release proceeds.
#
# NEVER replace a key outright. An install trusts only the keys it already
# carries, so dropping the old key strands every install that holds only that
# one: a rotation that shipped without a bridge did exactly that to every
# install from before it. Rotate by ADDING the new key in a release signed with
# the OLD one, and drop an old key only once no install can still depend on it.
# The first entry is the current signing key. See operations/runbook.md,
# "Release signing".
RELEASE_PUBLIC_KEYS_HEX: tuple[str, ...] = (
    "78a4e6d773483159637930e01db8de2448112117706aa908172e87211e0ac2d9",
    # Previous signing key: installs from before the rotation carry only this.
    "4c43769703fb44da543f15402a88c590990f0b0f7c0574caa935c6e16353beff",
)


def _release_keys() -> tuple[str, ...]:
    """The usable keyring: stripped, blanks dropped. Empty => no embedded key."""
    return tuple(k.strip() for k in RELEASE_PUBLIC_KEYS_HEX if k and k.strip())


# True when ANY public key in ``keys`` (hex) verifies ``sig`` over ``payload``. An
# unusable entry (bad hex / wrong length) is skipped with a warning, never fatal:
# one bad keyring entry must not hide a good one. Raises ImportError when
# ``cryptography`` is absent -- the caller decides what that means. Also used by
# scripts/build_dist.py's pre-push gate, so the build asks exactly the question
# the client asks.
def _any_key_verifies(keys: tuple[str, ...], sig: bytes, payload: bytes) -> bool:
    """True when any key in ``keys`` verifies ``sig`` over ``payload``."""
    # Kept lazy: documented ImportError contract for an old venv without cryptography.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    for key_hex in keys:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(
                sig, payload
            )
            return True
        except InvalidSignature:
            continue
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Skipping an unusable release public key %s... (%s).", key_hex[:8], exc
            )
    return False


def _no_crypto() -> str:
    """The "cryptography absent" outcome: warn, treat the release as unsigned."""
    logger.warning(
        "cryptography not installed -- cannot verify MANIFEST.sig (treating "
        "as unsigned)."
    )
    return "missing"


def _log_sig_invalid(keys: tuple[str, ...], tree: Path) -> str:
    """The "does not verify" outcome: warn, report ``"invalid"``."""
    logger.warning(
        "MANIFEST.sig failed Ed25519 verification against all %d embedded release "
        "key(s) (%s).",
        len(keys),
        tree,
    )
    return "invalid"


def _verify_present(tree: Path, keys: tuple[str, ...]) -> str:
    """Status of a PRESENT ``MANIFEST.sig``; any unexpected error is ``"invalid"``."""
    with never_raise() as g:
        try:
            sig_text = (tree / _MANIFEST_SIG_NAME).read_text(encoding="utf-8").strip()
            signature = base64.b64decode(sig_text)
            if _any_key_verifies(keys, signature, (tree / _MANIFEST_NAME).read_bytes()):
                return "valid"
        except ImportError:
            return _no_crypto()
    if g.failed:
        logger.warning("MANIFEST.sig verification error (%s) at %s.", g.exc, tree)
        return "invalid"
    return _log_sig_invalid(keys, tree)


# Verify ``MANIFEST.sig`` (base64 Ed25519 signature over the raw bytes of
# ``MANIFEST.sha256``) against the embedded keyring: valid when ANY key
# verifies. Returns one of:
#
# * ``"valid"``   -- signature present and cryptographically verified
# * ``"missing"`` -- no ``MANIFEST.sig``, no embedded key, or cryptography absent
# * ``"invalid"`` -- signature present but does NOT verify (tamper/forgery)
#
# Never raises: any unexpected error on a PRESENT signature is treated as
# ``"invalid"`` (fail-closed), while a genuinely absent signature/key is
# ``"missing"`` so the caller's policy gate can decide.
def verify_manifest_signature(tree: Path) -> str:
    """Status of the release signature: ``valid``, ``missing`` or ``invalid``."""
    keys = _release_keys()
    if not keys:
        return "missing"
    if (
        not (tree / _MANIFEST_SIG_NAME).is_file()
        or not (tree / _MANIFEST_NAME).is_file()
    ):
        return "missing"
    return _verify_present(tree, keys)


def _check_embedded_pubkey() -> None:
    """Startup footgun guard: warn if QA_UPDATE_REQUIRE_SIGNATURE is ON but
    the release keyring (_RELEASE_PUBLIC_KEYS_HEX) is empty -- the setting would
    reject every release."""
    if settings_port.get().qa_update_require_signature and not _release_keys():
        logger.warning(
            "QA_UPDATE_REQUIRE_SIGNATURE is ON but _RELEASE_PUBLIC_KEYS_HEX is empty "
            "in tools/updater.py -- every release will be REJECTED. "
            "Either (a) embed a public key and rebuild, or (b) set "
            "QA_UPDATE_REQUIRE_SIGNATURE=false in .env to allow unsigned releases "
            "during migration."
        )


_RELEASE_PUBLIC_KEYS_HEX = RELEASE_PUBLIC_KEYS_HEX  # legacy name (WX removes it)
