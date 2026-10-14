"""Constants of the host-mode prepare handler (re-exported by handlers.generate)."""

from __future__ import annotations

# 2026-08-31 (C11): ids WERE sent and none resolved. That is a
# different situation from "never captured anything", and the generic
# menu never said so -- the tester re-reads five options wondering
# why option 3, which they already did, was ignored.
_BEAT1_MISSING_HEAD = "\n\n> ⚠️  The `capture_ids` on this call resolved to NOTHING: "
_BEAT1_MISSING_TAIL = (
    ". An id with a reason beside it says why; one without "
    "has expired or was never issued. Run `qa_capture_screens` "
    "again and send the NEW ids with `source_plan='jira_device'`."
)
