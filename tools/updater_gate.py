"""Updater release-signature policy gate (split from tools/updater.py)."""

from __future__ import annotations

from pathlib import Path

from tools.updater_keys import verify_manifest_signature
from tools.updater_log import logger


def _abort_invalid(context: str) -> bool:
    """Abort: a present signature that does not verify (tamper/forgery)."""
    logger.warning(
        "ABORTING %s: MANIFEST.sig is present but INVALID -- refusing to "
        "trust this release; staying on the current version.",
        context,
    )
    return False


def _abort_unsigned(context: str) -> bool:
    """Abort: unsigned release while QA_UPDATE_REQUIRE_SIGNATURE is on."""
    logger.warning(
        "ABORTING %s: no valid MANIFEST.sig and QA_UPDATE_REQUIRE_SIGNATURE "
        "is on -- refusing an unsigned release; staying on the current "
        "version.",
        context,
    )
    return False


def _proceed_unsigned(context: str) -> bool:
    """Proceed with an unsigned release, with a loud migration warning."""
    logger.warning(
        "%s: proceeding with an UNSIGNED release (no MANIFEST.sig / no embedded "
        "public key). Set QA_UPDATE_REQUIRE_SIGNATURE=true once all releases are "
        "signed.",
        context,
    )
    return True


# Apply the release-signature policy before a manifest tree is trusted for
# install/heal. Returns True to PROCEED, False to ABORT. Never raises.
#
# * valid   -> proceed
# * invalid -> ALWAYS abort (tamper/forgery), regardless of ``require``
# * missing -> abort iff ``require`` (QA_UPDATE_REQUIRE_SIGNATURE); otherwise
#   proceed with a loud migration warning.
def _signature_gate_ok(tree: Path, *, require: bool, context: str) -> bool:
    """True to proceed with the release at ``tree``, False to abort."""
    status = verify_manifest_signature(tree)
    if status == "valid":
        logger.info("Release signature verified (%s).", context)
        return True
    if status == "invalid":
        return _abort_invalid(context)
    if require:
        return _abort_unsigned(context)
    return _proceed_unsigned(context)
