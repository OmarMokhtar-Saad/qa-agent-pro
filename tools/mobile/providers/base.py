"""The provider vocabulary, and THE one derivation of "can the guard trust this".

Two fields, stamped once, at the composite seam and nowhere else:

* ``origin`` -- what read this element (native / compose / flutter / webview).
* ``fidelity`` -- how well it was read, PER ELEMENT.

Per element, never per screen. A real screen mixes toolkits, so a screen-level
fidelity is two meanings wearing one name: the display value would be a floor
over regions while every DECISION needs the value of the element in hand.

ABSENT or UNKNOWN fidelity is NOT untrusted. Only ``degraded`` and ``blind``
are. Refusing on absence would stop every hand-built fixture and every
checkpoint written before this module existed -- a false-stop class -- and a
guard stop is TERMINAL for a scripted case. What keeps that honest is the seam:
``composite.observe`` stamps EVERY element it returns, so absence means "this
did not come through the seam", not "this was read poorly".

This module imports nothing internal on purpose: the guard reads it, and the
guard must not grow an import of the perception stack to ask one question.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The two keys, named once. Every producer and every consumer reads these
#: rather than spelling the strings again -- mirrored spellings drift.
ORIGIN_KEY = "origin"
FIDELITY_KEY = "fidelity"

ORIGIN_NATIVE = "native"
ORIGIN_COMPOSE = "compose"
ORIGIN_FLUTTER = "flutter"
ORIGIN_WEBVIEW = "webview"

#: Closed on purpose: an origin nobody enumerated is a region nobody wrote a
#: reader for, and the seam must refuse to stamp it rather than invent one.
ORIGINS = frozenset({ORIGIN_NATIVE, ORIGIN_COMPOSE, ORIGIN_FLUTTER, ORIGIN_WEBVIEW})

#: ``full``    -- every identifying string the toolkit has was read.
#: ``merged``  -- the toolkit merged a subtree into one node (Compose semantics).
#: ``degraded``-- the node is there but its identifying strings are not.
#: ``blind``   -- the region is one opaque node; what is inside is unknown.
FIDELITY_FULL = "full"
FIDELITY_MERGED = "merged"
FIDELITY_DEGRADED = "degraded"
FIDELITY_BLIND = "blind"

FIDELITIES = frozenset(
    {FIDELITY_FULL, FIDELITY_MERGED, FIDELITY_DEGRADED, FIDELITY_BLIND}
)

#: The fidelities a destructive action may NOT be judged under. ``merged`` is
#: absent deliberately: a merged node still carries the strings of what it
#: merged, so the lexicon can still read it. ``blind`` and ``degraded`` cannot
#: be read at all, which is the whole question the guard is asking.
UNTRUSTED_FIDELITY = frozenset({FIDELITY_DEGRADED, FIDELITY_BLIND})


@dataclass(frozen=True)
class Region:
    """One toolkit region of one dump.

    ``order`` is the node's position in DOCUMENT order, which is the tie-break
    ``executor.actuated_element`` already applies to overlapping elements. The
    same derivation, extended to regions -- not a second one.
    """

    origin: str = ORIGIN_NATIVE
    fidelity: str = FIDELITY_FULL
    bounds: tuple = (0, 0, 0, 0)
    order: int = 0


def is_untrusted(element: object) -> bool:
    """Whether the guard must refuse to judge *element*. Never raises.

    THE one place that answers this, and the ONE caller today is the
    executor's destructive guard (``screen_hit(..., judged=)``), which
    DELEGATES here rather than re-deriving the rule inline. The seam does NOT
    call it: ``composite`` STAMPS fidelity and never judges it, so this
    docstring claims one caller because there is one. Deriving this answer a
    second time at the guard is what this function exists to prevent --
    mirrored conditions drift, and two answers to "is this readable" agree
    only by coincidence.
    """
    if not isinstance(element, dict):
        return False
    return str(element.get(FIDELITY_KEY) or "") in UNTRUSTED_FIDELITY
