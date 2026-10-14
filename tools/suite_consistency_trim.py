"""Row and bullet trimming that keeps the advisory block within its byte budget."""

from __future__ import annotations

from tools import suite_consistency_limits as _lim


def _advisory_omission_marker(names: list[str]) -> str:
    """Disclose which advisory bullets the block budget dropped, BY NAME.

    Deliberately NOT mcp_handlers._omission_marker, which is the reply-level
    equivalent. That one promises the reader that what it dropped "repeats
    something you can still reach (the export, the coverage notes, or a tool you
    can call on demand)". For an advisory bullet that promise is FALSE: these
    findings are written nowhere else -- not into the workbook, which carries the
    cases and (since F06) their requirement ids, but no advisory. So this marker
    makes the opposite admission, and says how to get the dropped bullet back.
    """
    return (
        "\n  - \u2139\ufe0f  "
        f"{len(names)} further advisory bullet(s) were left out to keep this "
        f"block within its budget: {', '.join(names)}. Unlike the sections the "
        "reply drops, these are NOT written into the export -- re-run the "
        "submit against a smaller suite to see them."
    )


def _example_rows(bullet: list[str]) -> list[str]:
    return [
        ln
        for ln in bullet[1:]
        if ln.startswith(_lim._ROW_PREFIX) and not _lim._MORE_ROW_RE.match(ln)
    ]


def _already_hidden(bullet: list[str]) -> int:
    already = 0
    for ln in bullet[1:]:
        match = _lim._MORE_ROW_RE.match(ln)
        if match:
            already = int(match.group(1))
    return already


def _trim_bullet_rows(bullet: list[str], row_cap: int) -> list[str]:
    """One bullet with its example rows reduced to *row_cap*.

    The ``... and N more`` line is RECOMPUTED, never left stale: N becomes the
    count it already carried plus the rows this trim removed. A row list that is
    trimmed but still claims the old overflow number is a worse defect than the
    budget overrun this exists to prevent, so the arithmetic is the thing the
    tests assert.

    A bullet with no overflow line gains one when trimmed -- which is how the
    contradictory-state bullet (which slices with no disclosure at all) gets an
    honest count on this path. Returns the bullet unchanged when it already fits.
    """
    rows = _example_rows(bullet)
    if len(rows) <= row_cap:
        return bullet
    hidden = _already_hidden(bullet) + (len(rows) - row_cap)
    return [bullet[0]] + rows[:row_cap] + [f"  - ... and {hidden} more"]


def _shrink_rows_to_fit(
    header: list[str], bullets: list[list[str]], joined: str
) -> tuple[str | None, list[list[str]]]:
    """Shrink every bullet's rows down the cap ladder until the block fits.

    Returns (block, bullets): block is None when even the floor does not fit,
    and bullets is then the most-trimmed set.
    """
    for row_cap in _lim._ROW_CAP_LADDER:
        trimmed = [_trim_bullet_rows(b, row_cap) for b in bullets]
        body = "\n".join(header + [ln for b in trimmed for ln in b])
        if len(body) <= _lim._BLOCK_CAP:
            # Never let trimming make the block longer than leaving it alone.
            return (body if len(body) < len(joined) else joined), trimmed
        bullets = trimmed
    return None, bullets


def _join_kept(header: list[str], bullets: list[list[str]], keep: list[int]) -> str:
    return "\n".join(header + [ln for j in keep for ln in bullets[j]])


def _drop_sequence(order: list[tuple[str, int]]) -> list[int]:
    return sorted(
        (i for i, (_n, p) in enumerate(order) if p),
        key=lambda i: (order[i][1], i),
    )


def _drop_until_fit(
    header: list[str], bullets: list[list[str]], order: list[tuple[str, int]]
) -> tuple[list[int], list[int]]:
    keep = list(range(len(bullets)))
    dropped: list[int] = []
    for idx in _drop_sequence(order):
        body = _join_kept(header, bullets, [j for j in keep if j != idx])
        reserve = len(_advisory_omission_marker([order[j][0] for j in dropped + [idx]]))
        keep.remove(idx)
        dropped.append(idx)
        if len(body) + reserve <= _lim._BLOCK_CAP:
            break
    return keep, dropped


def _drop_bullets_to_fit(
    header: list[str], bullets: list[list[str]], order: list[tuple[str, int]]
) -> str | None:
    """Drop whole bullets, lowest priority first, until the block fits.

    Returns the block with a named omission marker, or None when no droppable
    bullet existed.
    """
    keep, dropped = _drop_until_fit(header, bullets, order)
    if not dropped:
        return None
    return _join_kept(header, bullets, keep) + _advisory_omission_marker(
        [order[j][0] for j in sorted(dropped)]
    )
