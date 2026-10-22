"""Selector vocabulary and the per-selector candidate searches of the resolver."""

from __future__ import annotations

from tools.mobile.text_fold import fold


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip().lower()


#: resolver reports. THEIR order, kept verbatim: ``id`` > ``rid`` > ``role`` >
#: ``label`` > ``text``. Preference decides which element is acted on when
#: several agree, and which selector is named in ``how``; it never decides
#: whether the others are consulted -- see :func:`resolve_target`.
#:
#: A property test enumerates ``Target.model_fields`` against this tuple, so a
#: selector added to the vocabulary without a resolver fails immediately rather
#: than silently skipping the agreement check. That is what forced this tuple to
#: grow from three to five when the two branches met.
SELECTORS: tuple[str, ...] = ("id", "rid", "role", "label", "text")

#: The selectors this SERVER mints, as opposed to the ones the app or the plan
#: chose. ``perception.element_id`` derives an ``id`` from the element's own
#: content, so an id that no longer resolves is OUR bookkeeping going stale --
#: after a ``type`` the keyboard opens and every id moves at once -- and
#: ``case_runner``'s uncharged-stop budget declines to charge the case for it.
#: A ``rid``, a ``role``, a ``label`` or a text that matches nothing is the
#: PLANNER naming something absent, and that is charged like any other
#: boomerang. The exemption is narrow on purpose: an uncharged stop for any
#: miss would be a free retry.
#:
#: ``role`` and ``label`` are computed by this server too, but from content that
#: is ALREADY in the element -- they do not go stale on a re-layout the way a
#: bounds-derived id does, which is the whole reason they exist. They are the
#: planner's to get right, so they are not in here.
OURS: frozenset[str] = frozenset({"id"})


def _selector_values(target: object) -> dict:
    """The selectors this target actually carries, normalised."""
    out: dict = {}
    for name in SELECTORS:
        raw = (
            target.get(name) if isinstance(target, dict) else getattr(target, name, "")
        )
        value = _norm(raw)
        if value:
            out[name] = value
    return out


def _rid_candidates(elements: list, want: str) -> list:
    exact = [e for e in elements if _norm(e.get("rid")) == want]
    if exact:
        return exact
    # A rid given without its package prefix is a documented convenience.
    return [e for e in elements if _norm(e.get("rid")).endswith("/" + want)]


def _labelled_candidates(elements: list, key: str, want: str) -> tuple:
    """Exact before contains, for one string selector. Their tiering."""
    contains = f"{key}_contains"
    exact = [e for e in elements if _norm(e.get(key)) == want]
    if exact:
        return key, exact
    return contains, [e for e in elements if want in _norm(e.get(key))]


def _cased(ch: str) -> bool:
    """True for a letter of a cased script (Latin, Cyrillic, Greek...)."""
    return ch.isalpha() and ch.lower() != ch.upper()


def _glued(ch: str) -> bool:
    """True for a char that runs on into a word: a cased letter, a digit, ``_``."""
    return _cased(ch) or ch.isdigit() or ch == "_"


def _bounded(label: str, want: str) -> bool:
    """True when *want* occurs in *label* not inside a longer word.

    ``install`` is not in ``no release installed`` or ``btn_install``, ``2`` is
    not in ``12 items``; an uncased script (Arabic, CJK) glues affixes to the
    word, so a letter of it is always a boundary there."""
    start = label.find(want)
    while start >= 0:
        end = start + len(want)
        before = label[start - 1] if start else " "
        after = label[end] if end < len(label) else " "
        glued_before = _glued(before) and _glued(want[0])
        glued_after = _glued(after) and _glued(want[-1])
        if not glued_before and not glued_after:
            return True
        start = label.find(want, start + 1)
    return False


def match_class(label: object, want: str) -> int:
    """3 = the label equals *want*, 2 = *want* is a whole word of it,
    1 = a bare substring (last resort), 0 = absent. Both sides are NFKC-folded
    (text paths only; resource ids keep :func:`_norm`)."""
    have, want = fold(label), fold(want)
    if not want or want not in have:
        return 0
    if have == want:
        return 3
    return 2 if _bounded(have, want) else 1


def _text_candidates(elements: list, want: str) -> tuple:
    """The best match class present wins: exact, then whole word, then substring."""
    classed = [
        (max(match_class(e.get("text"), want), match_class(e.get("desc"), want)), e)
        for e in elements
    ]
    best = max((c for c, _ in classed), default=0)
    names = {3: "text", 2: "word", 1: "contains"}
    return names.get(best, "contains"), [e for c, e in classed if c == best and c]
