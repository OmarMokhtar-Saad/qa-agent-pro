"""Numbers and their three states: what a measurement IS before the page draws it.

THE THREE STATES. Every measurement carries exactly one, and the renderer prints a
different thing for each: ``KNOWN`` (measured, here it is), ``ABSENT`` (measured,
genuinely none) and ``NOT_CAPTURED`` (this surface cannot see it -- never
measured). ``None`` and ``[]`` are NOT allowed to carry any of these meanings: an
empty list means "measured, and empty"; it must never stand in for "we never
looked", because that is how a tooling gap gets read as a product fact.

Pricing is NOT here. The rates table names a vendor's models and lives in the
profile; :func:`price` takes it as an argument. A model absent from the table gets
NO cost -- not a guessed one. An invented rate on a page that looks authoritative
is worse than an empty cell, because nobody re-checks a number already printed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KNOWN = "known"
ABSENT = "absent"
NOT_CAPTURED = "not-captured"


class Value:
    """One measurement, plus the reason it is missing when it is.

    Construct through :func:`known`, :func:`absent` or :func:`not_captured`, so every
    missing value is forced to carry its reason where the adapter knows it.
    """

    __slots__ = ("state", "value", "why")

    def __init__(self, state: str, value: object, why: str) -> None:
        self.state = state
        self.value = value
        self.why = why

    @property
    def is_known(self) -> bool:
        return self.state == KNOWN

    @property
    def is_not_captured(self) -> bool:
        return self.state == NOT_CAPTURED

    def get(self, default: object = None) -> object:
        """The value when known, else *default*. For arithmetic, never for rendering."""
        return self.value if self.state == KNOWN else default

    def __repr__(self) -> str:
        if self.state == KNOWN:
            return "known(%r)" % (self.value,)
        return "%s(%r)" % (self.state.replace("-", "_"), self.why)


def known(value: object) -> Value:
    return Value(KNOWN, value, "")


def absent(why: str) -> Value:
    """Measured, and there is genuinely none. *why* says what was looked for."""
    return Value(ABSENT, None, why)


def not_captured(why: str) -> Value:
    """Never measured, because this surface cannot see it. The reason is mandatory."""
    return Value(NOT_CAPTURED, None, why)


def price(
    model: object, tokens_in: object, tokens_out: object, rates: dict | None
) -> float | None:
    """USD for one call from a ``{model: [in_per_1M, out_per_1M]}`` table, or None.

    None means "unpriced": the model is not in the table, or the token counts are not
    numbers. The caller counts the tokens anyway and says the cost is partial.
    """
    pair = (rates or {}).get(str(model or ""))
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return None
    try:
        rate_in, rate_out = float(pair[0]), float(pair[1])
        count_in = float(tokens_in or 0)
        count_out = float(tokens_out or 0)
    except (TypeError, ValueError):
        return None
    return count_in / 1e6 * rate_in + count_out / 1e6 * rate_out


def money(usd: float | None) -> str | None:
    """Small amounts need real precision -- $0.00 says nothing about a 17k-token call."""
    if usd is None:
        return None
    if usd >= 1:
        return "$%s" % format(usd, ",.2f")
    if usd >= 0.01:
        return "$%.3f" % usd
    return "$%.5f" % usd


# One definition of the latency bands, for the per-case chip and the figure's legend alike.
LAT_BUCKETS = (
    ("fast", "under 3s", 3000.0),
    ("ok", "3-8s", 8000.0),
    ("slow", "over 8s", None),
)


def lat_bucket(ms: float | None) -> str | None:
    if ms is None:
        return None
    for key, _label, ceiling in LAT_BUCKETS:
        if ceiling is None or ms < ceiling:
            return key
    return LAT_BUCKETS[-1][0]


def pct(sorted_samples: list, q: float) -> float | None:
    """Nearest-rank percentile: the observed sample at or above *q* of the way through.

    Nearest-rank rather than interpolated, because every sample here is a duration that
    actually happened and the percentile should be one too. *sorted_samples* must be sorted.
    """
    if not sorted_samples:
        return None
    k = int(round(q * (len(sorted_samples) - 1)))
    return sorted_samples[max(0, min(k, len(sorted_samples) - 1))]


@dataclass
class Series:
    """One distribution of durations, and what a single sample IS.

    *samples* is a :class:`Value` over a list of milliseconds. *note* says what one sample
    covers, because "the model round trip" and "the case wall time" are not comparable and
    two tiles under the one word "latency" would invite the comparison.
    """

    key: str
    label: str
    samples: Value
    note: str = ""
    hist: bool = False


@dataclass
class Perf:
    """The performance section's whole input. Every field is a :class:`Value`, so a surface
    that measures none of it still renders the section, saying what is missing and why."""

    series: list = field(default_factory=list)
    endpoints: Value = field(default_factory=lambda: absent("no endpoint calls"))
    models: Value = field(default_factory=lambda: absent("no model calls"))
    slowest: Value = field(default_factory=lambda: absent("nothing measured"))
    note: str = ""
