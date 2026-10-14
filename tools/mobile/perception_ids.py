"""Element and screen identity: content-derived ids, unique within one dump."""

from __future__ import annotations

import hashlib


def _screen_id(package: str, activity: str, texts: list[str]) -> str:
    """Stable identity of a screen: package + activity + its top three texts.

    Deliberately NOT the full element hash -- a list that scrolled by one pixel
    is the same screen to a tester, and the report dedupes on this.
    """
    seed = "|".join([package, activity] + texts[:3])
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


#: How many hex characters of an element's own content hash become its id.
#: 32 bits over at most MAX_ELEMENTS elements; a collision INSIDE one dump is
#: handled explicitly by ``_assign_ids`` rather than left to luck.
ID_HASH_CHARS = 8

#: Kept short because the id travels in every packet and back in every target,
#: and ``actions.Target.id`` caps the field at 16 characters.
ID_PREFIX = "e"


def element_seed(element: object) -> str:
    """The identity of ONE pruned element, as text.

    This is the seed ``prune`` already hashed for the screen's ``hash`` field,
    factored out rather than duplicated: the SCREEN hash and the ELEMENT id are
    now computed from the same five observables, so the two can never drift
    into disagreeing about what "the same element" means.
    """
    body = element if isinstance(element, dict) else {}
    bounds = body.get("bounds") or []
    return "|".join(
        [
            str(body.get("cls") or ""),
            str(body.get("text") or ""),
            str(body.get("desc") or ""),
            str(body.get("rid") or ""),
            ",".join(str(value) for value in bounds),
        ]
    )


# The CONTENT-derived id of one pruned element.
#
# Ids used to be row numbers (``"e" + str(index)``), and that was the defect
# behind a tester watching the lane tap the microphone instead of send. A
# ``type`` action is in ``actions.MUTATING_OPS``, so the executor re-dumps and
# REPLACES the screen after it; a chat app then grows a send control once the
# field is non-empty, every row at or after that slot shifts by one, and the
# tap planned as the second element resolved -- confidently, with ``how="id"``
# and ``candidates=1`` -- to its neighbour. ``MAX_ELEMENTS`` truncation
# shifted them again.
#
# A content-derived id makes that class unrepresentable BY CONSTRUCTION
# rather than by a check somebody has to remember: the id IS a function of
# the element's own five observables, so an id minted against dump N can only
# match an element with the same class, text, description, resource id and
# bounds on dump N+1 -- the same element by everything the model was shown.
# When the element really changed, the id stops matching and the executor's
# existing boomerang hands the CURRENT screen back to be re-planned, which is
# the honest answer rather than a wrong tap.
def element_id(element: object) -> str:
    """The CONTENT-derived id of one pruned element."""
    digest = hashlib.sha256(
        element_seed(element).encode("utf-8", errors="replace")
    ).hexdigest()
    return ID_PREFIX + digest[:ID_HASH_CHARS]


def _assign_ids(elements: list) -> None:
    """Stamp every element with its content id, in place, uniquely.

    Two nodes CAN share all five observables -- a wrapper and its only child
    routinely share bounds and text -- and two elements under one id would put
    ``actions.resolve_target`` straight back to guessing. A collision therefore
    takes a deterministic ordinal suffix in dump order, so the id is still
    stable for a given dump and still never addresses a different content.
    """
    seen: dict[str, int] = {}
    for element in elements:
        base = element_id(element)
        count = seen.get(base, 0) + 1
        seen[base] = count
        element["id"] = base if count == 1 else base + "-" + str(count)
