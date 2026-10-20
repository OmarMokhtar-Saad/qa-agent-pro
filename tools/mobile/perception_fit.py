"""Fit a packet block under MAX_BLOCK_CHARS: staged legend, trimmed header."""

from __future__ import annotations

from tools.mobile import perception_limits
from tools.mobile.perception_consts import _LEGEND_STAGES
from tools.mobile.perception_notice import _block_notice, _last_resort_text
from tools.mobile.perception_viewport import more_below_line


# The (text, count) that shows the MOST element lines under `base`.
#
# EVERY stage is measured and the widest wins -- it is not a
# first-fit. First-fit does not work here and the failure is silent:
# popping element lines eventually removes the last REFERENCE, at
# which point the legend is no longer needed, the block fits, and the
# loop stops -- with one element line, having never tried the cheaper
# explanation that would have carried eight. Measured on a packet with
# an 11540-character header. The tie goes to the earliest stage, which
# is the fullest explanation.
def _fit_stage(base: list, stage: tuple, content_drops: tuple) -> tuple:
    """The widest ``(text, count)`` of one legend stage, or ``("", -1)``."""
    source, legend_text = stage
    content, drops = content_drops
    count = len(source)
    while True:
        body = source[:count]
        legend = [legend_text] if legend_text and any("@#" in x for x in body) else []
        note = _block_notice(content, (*drops, count, len(source) - count))
        below = more_below_line(content, count)
        text = "\n".join(base + legend + ([note] if note else []) + body + below)
        if len(text) <= perception_limits.MAX_BLOCK_CHARS:
            return text, count
        if not count:
            return "", -1
        count -= 1


def _best_fit(base: list, rows: tuple, content_drops: tuple) -> tuple:
    """The (text, count) that shows the MOST element lines under `base`."""
    rendered, plain = rows
    best_text, best_count = "", -1
    for use_refs, legend_text in _LEGEND_STAGES:
        source = rendered if use_refs else plain
        text, count = _fit_stage(base, (source, legend_text), content_drops)
        if count > best_count:
            best_text, best_count = text, count
    return best_text, best_count


# THE BLOCK OWNS ITS OWN SIZE. There are TWO truncations: the element budget in
# `prune`, and `wrap_untrusted`'s character cap. A notice derived from the
# packet knows only the first. Round 8f moved the notice ABOVE the element
# lines so the cap would stop cutting the notice -- and the cap then cut the
# LINES while the notice went on claiming them. Measured: 225 claimed, 181
# present, and 45 on-panel elements absent while the notice said 20 were lost
# on screen, which understates the tester's own loss worse than the message
# that was replaced. So the lines are trimmed HERE, and the cap can no longer
# cut an element line, so `wrap_untrusted` cannot make the notice false.
#
# A FIXED POINT, because the artifact must be measured AFTER the notice is
# written. The previous version reserved a guessed number of characters for the
# notice and trimmed against it -- and the notice grew past the guess at
# three-digit counts, so `wrap_untrusted` cut an element line after the count
# had already been printed. The cut landed mid-`at[...]`, which hands the
# planner a rectangle in the SHRINKING direction: worse than the false count it
# was meant to prevent.
#
# So: compose the whole block, measure it, drop the LAST element line, and
# recompose -- until it fits. Terminates because `shown` strictly shrinks. The
# header is dropped only as a last resort, and the notice is never what goes: a
# notice the cap cuts off is not a notice.
#
# REFERENCES ARE A PAIR: the `@id` notation and the legend that explains it
# travel together or neither travels. The first version decided the legend ONCE,
# from the full `rendered` list, and inserted it at head index 1 -- above both
# summaries. Two things followed, and an adversarial review measured both.
# Above the summaries, the head-drop path ate `controls`, then `fields`, then
# every element line before it touched the legend: 11 element lines with the
# legend suppressed, 0 with it present, on the same packet. And decided from
# `rendered` rather than from what survives, it was emitted for references the
# tail trim had already removed, and omitted while 7 surviving lines still
# carried `text=@e0`.
#
# So the choice is made INSIDE the loop, from `shown`, and the pair degrades in
# the order that costs the tester least. An unexplained `@e0` is worse than the
# bytes it saves, but so is throwing the notation away: measured, dropping the
# references to keep the paragraph left ONE element line where the referenced
# form fit eleven, because a plain line is three times longer. So the
# EXPLANATION degrades first (paragraph, then one line), and only if even one
# line cannot be afforded do the references go -- `plain` is the same elements
# with none, which is why both lists are carried. (The staged legend choice
# lives in `_best_fit`.)
#
# THE FREED ROOM BELONGS TO THE ELEMENTS. Dropping a header line
# without restoring them spent 11793 characters on nothing:
# measured 0 of 20 elements emitted where 18 fitted, so the
# planner got a screen with no actionable element and scrolled
# or gave up instead of tapping.
#
# Controls first, FIELDS only if that was not enough -- the
# fields summary is the only thing that says which element takes
# typing, and it exists because a model tapped Voice mode as
# Send. Neither is a convenience, but one is cheaper to lose.
def _trim_to_fit(head_base: list[str], rows: tuple, content_drops: tuple) -> str:
    """Compose the block, dropping header lines until the elements fit."""
    while True:
        text, count = _best_fit(head_base, rows, content_drops)
        if count >= 0 and (count or len(head_base) <= 1):
            return text
        if len(head_base) > 1:
            head_base = head_base[:-1]
            continue
        return _last_resort_text(head_base, content_drops, len(rows[1]))
