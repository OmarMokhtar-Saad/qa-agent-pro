"""Version / code skew disclosure notes for a prep submitted to a different build."""

from __future__ import annotations

import logging

logger = logging.getLogger("agents.host_mode")

# [version_skew_note]
# Warning for a prep staged by one build and submitted to another.
#
# Returns "" when the versions match or either is unknown, so callers can use it
# unconditionally. Never raises.
#
# 2026-08-03: the previous wording asserted ONE cause -- "the server updated
# mid-flow" -- for a condition that has two, and the unstated one is worse. The
# first production run of v1.34.0 staged its prep on a SEPARATE install (a dev
# checkout reporting v0.1.0) and submitted it to the packaged v1.34.0 server,
# because both were registered in the same client and the agent split the flow
# across them. Two installs mean two `.env` files and two sets of feature flags,
# so the suite was prepared under one configuration and finalized under another.
# The old closing line -- "the suite below is fine unless something looks wrong"
# -- waved exactly that through.
#
# So: state the observed fact, name BOTH causes, mark which one is harmful, and
# let the version SHAPE point at the likely one. A self-update moves between
# released versions; a dev checkout reports 0.x, which is the strong hint that a
# second install is involved. Extracted from handle_submit_suite so the wording
# is directly testable rather than only reachable through a full submit.
#
#
# [code_skew_note]
# Warning for a prep staged by DIFFERENT CODE than is now running.
#
# Returns "" when the fingerprints match or either is unknown, so callers can
# use it unconditionally. Never raises.
#
# F4 (2026-08-15). This is the case version_skew_note structurally cannot
# see. That note compares ``meta.app_version`` against ``BOOT_VERSION``, and
# a developer checkout reports ``0.1.0`` before and after any edit -- so
# through exactly the changes a tester most needs to know about (a grounding
# fix, a sanitisation fix), the versions match and it stays silent. Meanwhile
# ``data/suites.db`` is shared by every server process on the machine and a
# prep survives a restart, so "restart the server and submit the prep_id you
# already have" reuses pre-fix context against fixed code and produces
# pre-fix output with nothing disclosing it. That is what happened on the
# TICKET-5645 validation run.
#
# WARNING, NEVER A REFUSAL -- the same stance as version_skew_note, and for
# the same reason: the prepared work is the tester's, and most code changes
# do not invalidate a staged prep. What is fixed here is the SILENCE.
#
# ``version_matched`` shapes the wording only. When the versions ALSO differ,
# version_skew_note has already said the builds are different and named both
# causes, so this adds the one fact that note cannot supply (the prepared
# context itself is stale) without repeating it.
#
#

_CODE_SKEW_BODY = (
    " The prepared context in this prep -- the grounded ticket text, "
    "the acceptance criteria, the category specs -- was built by the "
    "OLD code and is replayed here as it was stored; restarting the "
    "server did not rebuild it. If the change was to how ticket text "
    "is read or grounded, this suite still carries the old behaviour. "
    "Run `qa_prepare_test_cases` again on the same ticket to rebuild "
    "the context, then generate against the NEW prep_id.\n"
)


def _version_head(staged: str, running: str) -> str:
    return (
        f"\n> \u26a0\ufe0f  Version mismatch: this prep was staged by "
        f"v{staged} and is being submitted to v{running}. Either this install "
        "self-updated between the two calls (harmless), or the prep came from "
        "a DIFFERENT install of this server (not harmless: a second install "
        "has its own `.env`, so the suite was prepared under one set of "
        "feature flags and finalized under another)."
    )


def _version_dev_hint() -> str:
    return (
        " The staging version looks like a development checkout rather "
        "than a release, which points at the second case: check whether "
        "more than one qa-agents server is registered in your client, and "
        "run the whole flow against ONE of them."
    )


def _version_tail() -> str:
    return (
        " The suite below was still built from the cases you submitted; "
        "re-run `qa_generate_test_cases` end to end on a single install if "
        "anything looks inconsistent.\n"
    )


def _version_note_text(staged: str, running: str) -> str:
    note = _version_head(staged, running)
    if staged.startswith("0."):
        note += _version_dev_hint()
    return note + _version_tail()


def version_skew_note(staged: str, running: str) -> str:
    """Warning for a prep staged by one build and submitted to another."""
    try:
        staged = str(staged or "").strip()
        running = str(running or "").strip()
        if not staged or not running or staged == running:
            return ""
        return _version_note_text(staged, running)
    except Exception:  # pragma: no cover - defensive; must never break a submit
        logger.exception("_version_skew_note failed")
        return ""


def _code_head(staged: str, running: str) -> str:
    return (
        "\n> \u26a0\ufe0f  Stale prepared context: this prep was staged by a "
        f"DIFFERENT BUILD of the generation code (`{staged[:12]}`) than the "
        f"one now running (`{running[:12]}`)."
    )


def _code_same_version_hint() -> str:
    return (
        " The version numbers are identical, so this is a code change "
        "on the same install -- typically a fix applied between "
        "`qa_prepare_test_cases` and this submit, with the server "
        "restarted in between."
    )


def code_skew_note(staged: str, running: str, *, version_matched: bool) -> str:
    """Warning for a prep staged by DIFFERENT CODE than is now running."""
    try:
        staged = str(staged or "").strip()
        running = str(running or "").strip()
        if not staged or not running or staged == running:
            return ""
        head = _code_head(staged, running)
        if version_matched:
            head += _code_same_version_hint()
        return head + _CODE_SKEW_BODY
    except Exception:  # pragma: no cover - defensive; must never break a submit
        logger.exception("_code_skew_note failed")
        return ""
