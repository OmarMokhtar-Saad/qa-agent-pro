"""Where an exchange sits in the run sequence: its rank and the cut marker."""

from __future__ import annotations

# ── the run sequence ───────────────────────────────────────────────────────────
#
# One clock-ordered stream per record. Stable within a tie by rank: a turn opens before the
# model is asked, the model before the tool it proposes, the tool before the requests it
# causes. Ties are common -- a tool and its first request often land on the same millisecond.

SEQ_RANK = {
    "turn": -1,
    "user": 0,
    "tap": 0,
    "prompt": 1,
    "llm": 2,
    "event": 3,
    "call": 3,
    "done": 3,
    "cap": 4,
    "api": 5,
    "net": 5,
    "auth": 5,
    "app": 5,
    "voice": 5,
    "step": 6,
    "flow": 7,
    "card": 8,
    "log": 9,
    "reply": 10,
    "agent": 11,
}

SEQ_CUT = "<!--seq-cut-->"
