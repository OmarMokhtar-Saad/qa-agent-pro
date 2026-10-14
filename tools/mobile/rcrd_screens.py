"""The screens a case started on, ended on and passed through."""

from __future__ import annotations

from tools.mobile.report_text import _text


def _case_screens(safe: dict, rows: list) -> dict:
    """The screens a case started on, ended on and passed through."""
    first_before = rows[0]["before"] if rows else _text(safe.get("screen_id"), 40)
    last_after, last_obs, last_look = _last_looks(rows)
    last_substituted = bool(last_look) and last_look != last_obs
    # The observation each end WAS, kept beside the id each end IS. A card that
    # keys its frame on the id alone draws the last look at that screen, which
    # on a conversation is somebody else's turn.
    first_obs = rows[0]["before_obs"] if rows else ""
    screens_seen = {row["before"] for row in rows} | {row["after"] for row in rows}
    screens_seen.discard("")
    return {
        "first": first_before,
        "last": last_after,
        "first_obs": first_obs,
        "last_obs": last_obs,
        "last_look": last_look,
        "last_substituted": last_substituted,
        "screens": len(screens_seen),
    }


# THE LAST LOOK THIS CASE ITSELF RECORDED, for the case whose final action
# carries no after-hash -- which is what thirteen of `executor`'s fourteen
# stamp sites produce. The alternative was an honest blank; a blank is worse
# than the right picture here, because this case's own steps DID observe
# that screen, so the card can show the turn the case actually ran on rather
# than nothing. What it may never do is show a look this case never had,
# which is why the scan is confined to these rows and matches on the screen
# id -- a page-wide search would find the newest turn again, which is the
# defect.
#
# WHAT `last_look` IS, stated positively, because the name invites a wrong
# reading and a disclaimer would not fix that:
#
#     the observation recorded by the LAST of this case's own rows that
#     touched the screen the case ended on, scanning those rows from the
#     end, taking an `after` observation in preference to a `before` one.
#
# It is NOT "the observation immediately before the final action". The scan
# matches on the screen id, so a trailing row with an empty `after` whose
# `before` names that same screen can supply it, and such a row may sit
# later in the trace than the row that set `last_after`. That is harmless
# for what this value is for -- every candidate is this case's own row at
# the right screen, so it can never draw another case's turn or a later
# turn of this one -- but a reader reconciling the card against the trace
# should know which row they are looking at.
#
# Substituted (``last_look != last_obs``) means an EARLIER look at the right
# screen, so the caption must say so rather than claim it is the end state. A
# record written before observations existed substitutes nothing and keeps
# its old caption.
def _last_looks(rows: list) -> tuple:
    """``(last_after, last_obs, last_look)`` scanned from the end of *rows*."""
    last_after = ""
    last_obs = ""
    for row in reversed(rows):
        if row["after"]:
            last_after = row["after"]
            last_obs = row["after_obs"]
            break
    last_look = last_obs
    if not last_look and last_after:
        for row in reversed(rows):
            if row["after"] == last_after and row["after_obs"]:
                last_look = row["after_obs"]
                break
            if row["before"] == last_after and row["before_obs"]:
                last_look = row["before_obs"]
                break
    return last_after, last_obs, last_look
