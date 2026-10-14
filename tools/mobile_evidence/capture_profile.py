"""Which app profile a package gets, and the words the record uses for it.

This module knows no app: every vocabulary word comes from the profile. The
fallback to the generic profile is decided here rather than inside
``profile_for``, so "this package has no profile" stays an observable fact.
"""

from __future__ import annotations

import logging

from tools.mobile_evidence import profiles

logger = logging.getLogger("tools.mobile_evidence.capture")


def _profile(package: object) -> profiles.Profile | None:
    try:
        return profiles.profile_for(package)
    except Exception:  # pragma: no cover - profile_for never raises
        logger.exception("mobile_evidence.capture: profile lookup failed")
        return None


def _profile_or_generic(package: object) -> profiles.Profile:
    """The package's own profile, or the generic one. Never None.

    The fallback is decided HERE rather than inside ``profile_for``, so "this
    package has no profile" stays an observable fact and the record can say
    which of the two was used.
    """
    return _profile(package) or profiles.generic_profile(package)


def _is_generic(profile: object) -> bool:
    return getattr(profile, "source", "") == profiles.GENERIC_PROFILE_NAME


def _profile_label(profile: profiles.Profile) -> str:
    return str(profile.name or profile.package or profile.source)[:80]


def _no_log_dir_reason(profile: profiles.Profile, package: str) -> str:
    # The generic profile always lands here: it names no on-device log,
    # so there is no event stream to pull and the slices are the whole
    # of the evidence. Said in the reason rather than left as a blank.
    if _is_generic(profile):
        return (
            "no profile for "
            + str(package)[:80]
            + "; a "
            + profiles.GENERIC_PROFILE_NAME
            + " captured the app's own logcat by pid instead, and there "
            "is no on-device event log to pull; write a per-app profile "
            "into ~/.qa-agents/mobile/profiles/ to turn one on"
        )
    return "the profile names no on-device log directory"
