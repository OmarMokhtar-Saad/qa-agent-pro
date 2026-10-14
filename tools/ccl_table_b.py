"""Coverage class table, part B."""

from __future__ import annotations

from tools.ccl_types import CoverageClass, _rx

CLASSES_B: list[CoverageClass] = [
    CoverageClass(
        class_id="foreign-environment",
        label="a pinned clock / timezone boundary seen from elsewhere",
        # Period-scoped so that audit cases carrying a "UTC timestamp" do not
        # match the SUBJECT at all, and therefore cannot be qualified by the
        # timezone token in the adversity list.
        subject=_rx(
            r"month(?:ly)?\b(?:[^.]{0,80})(?:reset|rollover|roll\s+over|boundary|counter|cap)"
            r"|(?:reset|rollover|boundary)(?:[^.]{0,80})month"
            r"|daily\s+(?:reset|cut[-\s]?off)|midnight"
        ),
        adversity=_rx(
            r"device\s+(?:time\s?zone|tz)"
            r"|\b(?:UTC|GMT|PST|PDT|EST|EDT|CET|CEST|IST|JST)\b"
            r"|different\s+time\s?zone|another\s+time\s?zone|non[-\s]?Riyadh"
            r"|abroad|travel|overseas"
        ),
    ),
    CoverageClass(
        class_id="reversal-accounting",
        label="refunds / reversals against a running total",
        subject=_rx(r"refund|reversal|reversed|chargeback|credited\s+back"),
        adversity=_rx(r"month(?:ly)?|counter|cap|spent|total|balance"),
        unspecified=True,
    ),
    CoverageClass(
        class_id="pending-holds",
        label="pre-authorisation holds against a cap",
        subject=_rx(
            r"pre[-\s]?auth|authorisation\s+hold|authorization\s+hold"
            r"|\bhold\b(?:[^.]{0,40})(?:amount|cap|counter|fuel|hotel)"
            r"|fuel\s+(?:pump|station)|hotel\s+(?:booking|check[-\s]?in)"
        ),
        adversity=_rx(r"month(?:ly)?|counter|cap|spent|limit|total"),
        unspecified=True,
    ),
    CoverageClass(
        class_id="currency-conversion",
        label="currency conversion against a monetary limit",
        subject=_rx(
            r"conver(?:t|sion|ted)|exchange\s+rate|\bFX\b|foreign\s+currency"
            r"|\b(?:EUR|USD|GBP|AED)\b"
        ),
        adversity=_rx(
            r"per[-\s]?transaction\s+limit"
            r"|(?:before|after|pre|post)[-\s]?conversion"
            r"|settled?\s+amount|billing\s+amount"
        ),
        unspecified=True,
    ),
    CoverageClass(
        class_id="accessibility-depth",
        label="accessibility depth",
        subject=_rx(
            r"accessib|screen\s+reader|voice\s?over|talkback|aria\b|wcag"
            r"|contrast|focus\s+order|dynamic\s+type|font\s+size|text\s+siz"
            r"|tab\s+order|touch\s+target"
        ),
        # A DEPTH class, not an adversity class: the question is not "is there a
        # failure path" but "is there more than one token case", so presence is
        # the measure and the FLOOR is the bar.
        adversity=None,
        floor=2,
    ),
    CoverageClass(
        class_id="localization-depth",
        label="localization depth",
        subject=_rx(
            r"localis|localiz|arabic|\bRTL\b|right[-\s]?to[-\s]?left"
            r"|locale|translat|language|bidi"
        ),
        adversity=None,
        floor=2,
    ),
]
