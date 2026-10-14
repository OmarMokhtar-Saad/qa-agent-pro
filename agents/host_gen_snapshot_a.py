"""Host mode: ticket-snapshot recency notes of the prepare pipeline (C3c).

Moved from handlers/generate.py: the snapshot stamp, the Jira timestamp parser,
the stale-snapshot WARN, the content-size probe and the snapshot line. The
store and the settings are read here by attribute, so patches on
``tools.prep_store`` still bite.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from config.settings_guard import never_raise
from tools import prep_store, settings_port

logger = logging.getLogger("agents.host_mode")


def safe_snapshot_stamp(raw) -> str:
    """A Jira `fields.updated` value as safe, single-line, capped display text.

    Already sanitized by `jira_mcp._sanitize_echo` on the boomerang path; capped
    again here because this module must not depend on WHERE a grounded dict came
    from. Set/str-based like every other sanitizer in this file -- `re` is
    deliberately not imported at module scope. Never raises.
    """
    with never_raise():
        text = "".join(ch for ch in str(raw or "") if ch.isprintable() and ch != "`")
        return " ".join(text.split())[:40]
    return ""


def parse_jira_timestamp(raw):
    """Epoch seconds for a Jira timestamp, or None when it cannot be read.

    None means "say nothing" (HOST-supplied text). Handles a trailing `Z` and
    the compact `+0300` offset; a naive stamp is read as UTC. Never raises.
    """
    try:
        text = str(raw or "").strip()
        if not text:
            return None
        if text[-1] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
            text = text[:-2] + ":" + text[-2:]
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


# I2c (2026-08-10). The host caches `jira_content_json` on disk and re-sends
# hours-old copies -- files written at 09:06 were re-sent at 13:06 -- and the
# server had no recency signal at all. Deliberately a WARNING and never a
# refusal: the timestamps are host-supplied text, so a hard gate would be both
# gameable and annoying. Reads the newest prior prep for the SAME source inside
# QA_PREP_TTL_S that stamped a `jira_updated`. FINALIZED preps count here,
# unlike the duplicate-prep guard, because a finished generation is exactly the
# snapshot a later call must be compared against.
async def stale_snapshot_note(source_text: str, updated: str) -> str:
    """WARN when the ticket snapshot just handed back is OLDER than one this
    install already prepared from. "" (silence) in every other case. Never raises.
    """
    if not source_text or not updated:
        return ""
    with never_raise(logger, "stale-snapshot check failed", exc_info=True):
        hit = await prep_store.find_prep_snapshot_by_source(
            source_text,
            float(getattr(settings_port.get(), "qa_prep_ttl_s", 86400) or 0),
        )
        prior = (hit or {}).get("content") or {}
        return _stale_note_from_prior(prior, updated)
    return ""


def _stale_note_from_prior(prior: dict, updated: str) -> str:
    """The STALE warning when *prior* (a stored prep) is newer than *updated*."""
    prior_stamp = safe_snapshot_stamp(prior.get("jira_updated"))
    if not prior_stamp or prior_stamp == updated:
        return ""
    new_t = parse_jira_timestamp(updated)
    old_t = parse_jira_timestamp(prior_stamp)
    if new_t is None or old_t is None or new_t >= old_t:
        return ""
    when = time.strftime("%H:%M", time.localtime(float(prior.get("created_at") or 0)))
    return _stale_warning(updated, when, prior_stamp)


def _stale_warning(updated: str, when: str, prior_stamp: str) -> str:
    """The STALE-snapshot warning text."""
    return (
        "> ⚠️ The ticket snapshot you just sent (`updated` = "
        f"`{updated}`) is OLDER than the one this install already prepared "
        f"from at {when} (`{prior_stamp}`), so this is almost certainly a "
        "CACHED copy of the ticket rather than a fresh read. Re-run "
        "`getJiraIssue` and prepare again if the ticket may have changed. "
        "Generation continues with the snapshot you sent."
    )


def jira_content_chars(url_content) -> int:
    """How much TICKET TEXT this grounded payload yielded. 0 when unknown.

    F9 (2026-08-15). Counts the two fields that actually ground generation --
    the description and the acceptance criteria -- and deliberately NOT
    ``raw_text``, which has the comment thread appended: comments arrive
    through a different path and a ticket gaining a comment between two runs
    would otherwise read as the ticket growing. Never raises.
    """
    # pragma: no cover - defensive
    with never_raise():
        if not isinstance(url_content, dict) or url_content.get("error"):
            return 0
        return len(str(url_content.get("description") or "")) + len(
            str(url_content.get("acceptance_criteria") or "")
        )
    return 0


def snapshot_note(snapshot_updated: str) -> str:
    """I2b (2026-08-10): WHICH ticket snapshot the cases came from, or ""."""
    if not snapshot_updated:
        return ""
    return (
        f"> 🕒 Ticket snapshot as of `{snapshot_updated}` (the "
        "`updated` timestamp on the Jira payload you handed back). This "
        "server never fetches the ticket itself, so if that looks old, "
        "re-run `getJiraIssue` before generating again."
    )
