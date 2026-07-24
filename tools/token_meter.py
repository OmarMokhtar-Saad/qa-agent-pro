"""Lightweight token/cost meter for a generation run (T-05 / I-032).

There was previously zero cost visibility: a single generation fans out to 8+
LLM calls and ~36K-100K input tokens, but nothing surfaced that to the tester or
operator. This accumulator gives a cheap, backend-agnostic ESTIMATE (≈4 chars per
token) of the input/output tokens spent, which the summary appends as one line.

Precise usage would come from the API response's `usage` field on the `api`
backend; the estimate keeps this useful on the `cli` backend too, where usage is
not exposed. Never raises.
"""

from __future__ import annotations

_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Never raises."""
    try:
        return max(0, len(text or "") // _CHARS_PER_TOKEN)
    except Exception:
        return 0


class TokenMeter:
    """Accumulates estimated input/output tokens across the LLM calls of one run."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def record(self, input_text: str = "", output_text: str = "") -> None:
        """Record one LLM call's estimated input and output tokens."""
        try:
            self.input_tokens += estimate_tokens(input_text)
            self.output_tokens += estimate_tokens(output_text)
            self.calls += 1
        except Exception:
            pass

    def summary_line(self) -> str:
        """A one-line markdown cost summary, or '' when nothing was recorded."""
        if self.calls <= 0:
            return ""
        return (
            f"\n\n> 💸 Cost estimate: ~{self.input_tokens:,} input / "
            f"~{self.output_tokens:,} output tokens across {self.calls} LLM call(s)."
        )
