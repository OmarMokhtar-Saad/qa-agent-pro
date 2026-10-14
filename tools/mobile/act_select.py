"""Selector vocabulary and the per-selector candidate searches of the resolver."""

from __future__ import annotations


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


def _text_candidates(elements: list, want: str) -> tuple:
    exact = [
        e
        for e in elements
        if _norm(e.get("text")) == want or _norm(e.get("desc")) == want
    ]
    if exact:
        return "text", exact
    return "contains", [
        e
        for e in elements
        if want in _norm(e.get("text")) or want in _norm(e.get("desc"))
    ]
