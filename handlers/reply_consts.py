"""Caps and drop priorities of the finalize reply (moved from handlers._reply)."""

from __future__ import annotations

_SUMMARY_CAP = 4000  # cap the embedded generation summary in the shaped result

# --------------------------------------------------------------------------- #
# Finalize-reply budget (F03, 2026-08-16)
# --------------------------------------------------------------------------- #
# _SUMMARY_CAP above bounds ONE of the twenty slots the submit reply is
# assembled from -- the `summary` embedded by shape_generation_result. The
# 2026-08-16 live run measured 4844 chars, of which 2006 (41%) sat in the notice
# preamble that no cap has ever seen: the notes are concatenated one level up,
# at the return of handle_submit_suite, and that string is what the tester
# reads.
#
# WHAT YIELDS. Almost every note in that preamble is a disclosure with its own
# written "must always be surfaced" contract -- dropped_note (:5390), the
# always-on coverage signal (:5346), the image-relevance warning the submit
# AUDIT row claims was shown (:8095), the duplicate note that reports cases the
# server actually REMOVED (:7842). Trimming those to make room for prose would
# be a worse bug than an over-long reply, and one of them would additionally
# make the audit trail assert a disclosure that never happened. So they are
# PROTECTED, and the block that yields is `summary`: its content is the suite,
# the suite is in the workbook, and its truncation marker already says so.
# Only when the summary has yielded to its floor do four genuinely repeatable
# sections drop -- and they are NAMED where they were, so nothing is silent.
_REPLY_CAP = 6000

# The summary never shrinks below this. A reply with no summary at all is not a
# cheaper reply, it is a different (and useless) one.
_SUMMARY_FLOOR = 1200

# What shape_generation_result appends AFTER the slice when it truncates -- a
# blank line and the "…(truncated — …)" marker. It is charged to the reply but
# is not part of `summary`, so a budget that ignores it over-runs the cap by
# exactly this much on every truncated reply. Reserved unconditionally rather
# than only when the summary is actually cut, because the budget is computed
# BEFORE the shaping that would decide.
_TRUNC_RESERVE = 64

# Drop priorities for the four trimmable sections. LOWER drops FIRST, and each
# constant names WHY that section is droppable at all -- a section that cannot
# answer that question is protected instead.
_REPLY_P_EXPORTED = 1  # the same disclosure is written into the export
_REPLY_P_POINTER = 2  # names an on-demand tool that reproduces it
_REPLY_P_PROVENANCE = 3  # how a field was derived, not what it says
_REPLY_P_REPORT = 4  # a measurement section, re-derivable from the suite
