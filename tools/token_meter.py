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

import logging

logger = logging.getLogger("qa_agents.token_meter")

_CHARS_PER_TOKEN = 4

# Presentation order for the opt-in per-phase breakdown. Any other phase string
# a call site invents still records fine; it simply sorts after these.
_PHASES = ("generation", "critic", "rewrite", "other")


def _blank_bucket() -> dict:
    """A zeroed per-phase / per-model accumulator.

    ``input``/``output``/``cache_*`` are ALL calls (real + estimated) -- a
    phase's token VOLUME matters regardless of precision. The ``real_*``
    subtotals cover only calls whose true API usage was captured, and are the
    only thing ``estimate_cost_usd`` will price: costing an estimated call
    would be a guess about a guess.
    """
    return {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "calls": 0,
        "estimated_calls": 0,
        "real_input": 0,
        "real_output": 0,
        "real_cache_read": 0,
        "real_cache_write": 0,
    }


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Never raises."""
    try:
        return max(0, len(text or "") // _CHARS_PER_TOKEN)
    except Exception:
        return 0


class TokenMeter:
    """Accumulates input/output tokens across the LLM calls of one run.

    Prefers REAL per-call usage from the Anthropic ``api`` backend (plumbed in
    by ``note()`` via ``llm.last_call_usage``); falls back to the historical
    ~4-chars-per-token estimate on ``cli``/``cursor`` and wherever a test double
    replaced the call. Never raises.
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.estimated_calls = 0
        self.real_calls = 0
        # phase -> bucket (drives the opt-in breakdown); model id -> bucket
        # (drives the opt-in cost estimate). A phase does NOT imply a model --
        # the "other" phase alone spans classifier-tier AC synthesis and
        # generation-tier vision calls -- so pricing needs its own axis.
        self.by_phase: dict[str, dict] = {}
        self.by_model: dict[str, dict] = {}

    def record(self, input_text: str = "", output_text: str = "") -> None:
        """Record one LLM call's estimated input and output tokens.

        Behaviourally UNCHANGED for every pre-existing caller: it still routes
        through the module-level ``estimate_tokens`` (so a monkeypatched
        estimator still governs the outcome) and still leaves the counters
        untouched if that raises.
        """
        try:
            in_tok = estimate_tokens(input_text)
            out_tok = estimate_tokens(output_text)
        except Exception:
            return
        self.record_usage("generation", "", in_tok, out_tok, estimated=True)

    def record_usage(
        self,
        phase: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        estimated: bool = True,
    ) -> None:
        """Accumulate one LLM call. Never raises."""
        try:
            in_tok = max(0, int(input_tokens or 0))
            out_tok = max(0, int(output_tokens or 0))
            c_read = max(0, int(cache_read_tokens or 0))
            c_write = max(0, int(cache_write_tokens or 0))

            self.input_tokens += in_tok
            self.output_tokens += out_tok
            self.cache_read_tokens += c_read
            self.cache_write_tokens += c_write
            self.calls += 1
            if estimated:
                self.estimated_calls += 1
            else:
                self.real_calls += 1

            phase_key = (str(phase or "other").strip().lower()) or "other"
            model_key = (str(model or "").strip()) or "unknown"
            buckets = (
                self.by_phase.setdefault(phase_key, _blank_bucket()),
                self.by_model.setdefault(model_key, _blank_bucket()),
            )
            for bucket in buckets:
                bucket["input"] += in_tok
                bucket["output"] += out_tok
                bucket["cache_read"] += c_read
                bucket["cache_write"] += c_write
                bucket["calls"] += 1
                if estimated:
                    bucket["estimated_calls"] += 1
                else:
                    bucket["real_input"] += in_tok
                    bucket["real_output"] += out_tok
                    bucket["real_cache_read"] += c_read
                    bucket["real_cache_write"] += c_write
        except Exception:
            logger.debug("token meter record_usage failed", exc_info=True)

    def _phase_breakdown(self) -> str:
        """The opt-in per-phase line, or '' when there is nothing to show."""
        if not self.by_phase:
            return ""
        known = [name for name in _PHASES if name in self.by_phase]
        extra = sorted(name for name in self.by_phase if name not in _PHASES)
        parts = []
        for name in known + extra:
            bucket = self.by_phase[name]
            parts.append(
                f"{name}: ~{bucket['input']:,} in / ~{bucket['output']:,} out "
                f"({bucket['calls']} call(s))"
            )
        if not parts:
            return ""
        return "\n> By phase — " + "; ".join(parts) + "."

    def _cost_suffix(self) -> str:
        """The opt-in $ line, or '' when nothing could be priced."""
        cost = estimate_cost_usd(self)
        if not cost:
            return ""
        text = (
            f"\n> Estimated spend: ~${cost['total_usd']:.4f} "
            f"(approximate, priced from {cost['priced_calls']} call(s) of real "
            f"API usage)"
        )
        if cost.get("unpriced_calls"):
            text += f"; {cost['unpriced_calls']} call(s) had no real usage to price"
        return text + "."

    def summary_line(self, *, detailed: bool = False, show_cost: bool = False) -> str:
        """A one-line markdown cost summary, or '' when nothing was recorded.

        The zero-argument call is BYTE-IDENTICAL to the pre-plan output; both
        extras are opt-in and both default OFF.
        """
        if self.calls <= 0:
            return ""
        line = (
            f"\n\n> 💸 Cost estimate: ~{self.input_tokens:,} input / "
            f"~{self.output_tokens:,} output tokens across {self.calls} LLM call(s)."
        )
        try:
            if detailed:
                line += self._phase_breakdown()
            if show_cost:
                line += self._cost_suffix()
        except Exception:
            logger.debug("token meter summary extras failed", exc_info=True)
        return line


def estimate_cost_usd(meter: TokenMeter) -> dict | None:
    """Approximate $ spend for a meter, or None when nothing is priceable.

    Prices ONLY the real-usage subtotals, and only for a model id that resolves
    to one of the two tiers this codebase configures (``qa_llm_model`` /
    ``qa_classifier_model``). Anything else -- a cursor-backend model name, an
    estimated call -- is counted into ``unpriced_calls`` rather than being
    silently priced at the wrong rate. Never raises.
    """
    try:
        from config.settings import settings
    except Exception:
        logger.debug("estimate_cost_usd: settings unavailable", exc_info=True)
        return None
    try:
        gen_model = (getattr(settings, "qa_llm_model", "") or "").strip()
        cls_model = (getattr(settings, "qa_classifier_model", "") or "").strip()
        read_discount = float(
            getattr(settings, "qa_token_price_cache_read_discount", 0.1) or 0.0
        )
        write_multiplier = float(
            getattr(settings, "qa_token_price_cache_write_multiplier", 1.25) or 0.0
        )
        total = 0.0
        priced_calls = 0
        unpriced_calls = 0
        for model_key, bucket in (getattr(meter, "by_model", None) or {}).items():
            calls = int(bucket.get("calls", 0) or 0)
            real_calls = calls - int(bucket.get("estimated_calls", 0) or 0)
            if gen_model and model_key == gen_model:
                in_rate = float(settings.qa_token_price_generation_input_per_1m)
                out_rate = float(settings.qa_token_price_generation_output_per_1m)
            elif cls_model and model_key == cls_model:
                in_rate = float(settings.qa_token_price_classifier_input_per_1m)
                out_rate = float(settings.qa_token_price_classifier_output_per_1m)
            else:
                unpriced_calls += calls
                continue
            if real_calls <= 0:
                unpriced_calls += calls
                continue
            priced_calls += real_calls
            unpriced_calls += calls - real_calls
            total += (bucket.get("real_input", 0) / 1_000_000.0) * in_rate
            total += (bucket.get("real_output", 0) / 1_000_000.0) * out_rate
            total += (
                (bucket.get("real_cache_read", 0) / 1_000_000.0)
                * in_rate
                * read_discount
            )
            total += (
                (bucket.get("real_cache_write", 0) / 1_000_000.0)
                * in_rate
                * write_multiplier
            )
        if priced_calls <= 0:
            return None
        return {
            "total_usd": round(total, 6),
            "priced_calls": priced_calls,
            "unpriced_calls": unpriced_calls,
        }
    except Exception:
        logger.debug("estimate_cost_usd failed", exc_info=True)
        return None


def model_text(obj: object) -> str:
    """Best-effort text of an LLM response object, for the char-estimate path.

    Deliberately defensive: ``note()``'s promise is that metering NEVER changes
    behaviour, and a call site that wrote ``output_text=result.model_dump_json()``
    inline would break that -- the attribute lookup happens BEFORE note() can
    swallow anything, so a response object without that method (a test double, a
    future non-pydantic model) would raise straight into the caller's own
    try/except and silently degrade a real feature. Never raises.
    """
    try:
        dumper = getattr(obj, "model_dump_json", None)
        if callable(dumper):
            return dumper()
        return obj if isinstance(obj, str) else ""
    except Exception:
        logger.debug("model_text failed", exc_info=True)
        return ""


def note(
    meter: "TokenMeter | None",
    phase: str,
    model: str,
    *,
    system: str = "",
    user: str = "",
    output_text: str = "",
) -> None:
    """Record the LLM call that JUST completed into ``meter``. Never raises.

    Call this immediately after an ``await ask*(...)`` returns. It reads
    ``llm.last_call_usage()`` -- the real-or-estimated snapshot ``llm.py``
    publishes for its own telemetry -- and records those exact numbers. When no
    snapshot exists (a test double replaced the call, or it raised before
    telemetry ran) it falls back to a char estimate over the ``system``/``user``/
    ``output_text`` the caller already holds, which is exactly what the manual
    ``meter.record()`` call at this site did before.

    ``model`` is a LABEL for the cost axis, so pass the id the call actually
    resolved to. CAVEAT: on the ``cursor`` backend the configured Anthropic-style
    id is substituted for a cursor-agent model name inside ``llm.py``
    (``_resolve_cursor_model``), so the label recorded here can name a model that
    was not the one billed. That is harmless: ``estimate_cost_usd`` prices only
    ids matching the two configured tiers and reports everything else as
    unpriced, so a mislabelled cursor call is never charged a wrong rate.
    """
    if meter is None:
        return
    try:
        usage = None
        try:
            import llm

            usage = llm.last_call_usage()
        except Exception:
            # llm unavailable, or no snapshot -- fall through to the estimate.
            usage = None
        if usage:
            in_tok, out_tok, cache_read, cache_write, estimated = usage
            meter.record_usage(
                phase,
                model,
                in_tok,
                out_tok,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                estimated=bool(estimated),
            )
            return
        meter.record_usage(
            phase,
            model,
            estimate_tokens(system) + estimate_tokens(user),
            estimate_tokens(output_text),
            estimated=True,
        )
    except Exception:
        logger.debug("token meter note failed", exc_info=True)
