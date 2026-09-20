"""The perception SEAM: one dump in, one stamped screen out.

``observe`` is a WRAPPER, not a rewrite. It calls ``perception.prune`` exactly
once, passes its refusals through untouched, and then stamps ``origin`` and
``fidelity`` onto each element.

WHY THE STAMP LIVES HERE AND NOT IN ``prune``: the guard's completeness ratchet
(``test_the_guard_reads_every_identifying_string_perception_emits``) calls
``prune`` directly and asserts every string-valued element key is one the guard
reads. Two new string keys there would fail an existing invariant and force it
to be edited. Stamping at the seam keeps that invariant untouched -- and the
element id is unaffected either way, because ``perception.element_seed`` reads
exactly five named keys (cls, text, desc, rid, bounds) and neither new key is
one of them.

NATIVE ONLY today: ``regions_of`` returns no region, so every element is
``native``/``full`` and nothing about a live run changes. ``partition`` is
written and graded now so steps 5 and 6 add host-node DETECTION rather than
re-deciding the rule under deadline.
"""

from __future__ import annotations

import logging

from tools.mobile import perception
from tools.mobile.providers import base

logger = logging.getLogger(__name__)


def observe(
    xml: object,
    activity: str = "",
    display: object = None,
    density: object = None,
) -> dict:
    """``perception.prune``'s answer, with every element stamped. Never raises."""
    pruned = perception.prune(xml, activity, display=display, density=density)
    if not isinstance(pruned, dict) or pruned.get("error"):
        return pruned
    screen = pruned.get("content")
    if not isinstance(screen, dict):
        return pruned
    return {"error": None, "content": stamp(screen)}


def stamp(screen: dict) -> dict:
    """Stamp every element of *screen* in place, and return it.

    In place, and the SAME dict: the screen record's shape is what the run
    store, the report and the case runner already read, and a copy here would
    be a second screen object with the same id.
    """
    try:
        elements = screen.get("elements")
        if not isinstance(elements, list):
            return screen
        regions = regions_of(screen)
        for element in elements:
            if not isinstance(element, dict):
                continue
            origin, fidelity = partition(element, regions)
            element[base.ORIGIN_KEY] = origin
            element[base.FIDELITY_KEY] = fidelity
        return screen
    except Exception:  # pragma: no cover - defensive; perception must not fail
        logger.exception("mobile.providers.composite.stamp failed")
        return screen


def regions_of(screen: object) -> list:
    """The toolkit regions of *screen*.

    THE COORDINATE CONTRACT, and it binds every future implementation of this
    function: ``Region.bounds`` MUST be a rectangle in the SAME coordinate
    space ``perception`` emits for ``element["bounds"]`` -- the RAW ABSOLUTE
    dump rectangle, ``list(parse_bounds(node.get("bounds")))``, exactly as it
    came off uiautomator. NOT the display panel's space, NOT ``frame_bounds``,
    NOT anything rescaled or re-origined: ``perception`` carries BOTH
    ``root_bounds`` and ``frame_bounds`` precisely because they answer
    different questions, and a region derived from either of those is a
    different space wearing the same field name.

    WHY IT IS WRITTEN AS A CONTRACT RATHER THAN A NOTE: ``partition`` compares
    these rectangles against element bounds with ``_inside``. A provider that
    returns the right shape in the wrong space makes ``_inside`` False for
    EVERY element, so ``partition`` silently takes C1, every element stamps
    ``native``/``full``, and the destructive guard never fires -- while the
    whole suite stays green, because the fixture matrix grades the COMPARISON
    and nothing grades the SPACE. That is this repo's frame-box-and-rects bug
    and its reverted viewport rule, both again.
    THIS CONTRACT IS GRADED, and it grades YOUR implementation without you
    adding a test. ``tests/mobile/space_contract.assert_region_space`` is the
    one producer of the rule -- a region's rectangle must EQUAL some element's
    raw absolute ``bounds`` -- and
    ``tests/mobile/test_composite_space_contract.py`` calls it on the REAL
    ``regions_of`` output for three real captures. Empty passes trivially; the
    day this returns something, the contract runs on it.

    Strictness is free here because of the SIGNATURE: you are handed the
    pruned screen and nothing else, so every node you can see is already an
    element of ``screen["elements"]``. A rectangle that is not one of theirs
    was transformed, which is the defect.

    The matrix behind the rule is one fixture per MECHANISM -- re-origined,
    display-panel-substituted, density-rescaled -- plus two legitimate regions
    that must be ACCEPTED, one inset and one that coincides with the panel.
    That last pair is why the rule is membership in the element set and not a
    blacklist of ``root_bounds``/``frame_bounds``: the same rectangle is wrong
    on an inset-dialog screen and right on a full-screen one.

    EMPTY today, and that is the native-only step in one line: with no region,
    ``partition`` takes clause C1 for every element and the lane behaves exactly
    as it did before this module existed. The Compose and Flutter readers fill
    this in later; nothing else has to change when they do.
    """
    return []


def partition(element: object, regions: object) -> tuple:
    """``(origin, fidelity)`` for one element. Never raises.

    Four clauses:

    * **C1** no region contains it -> ``native``/``full``. The default is the
      answer for a dump with no host node at all, which is every dump today.
    * **C2** a region contains it -> that region's origin and fidelity.
    * **C3** a host node is contained by its OWN box, so C2 already covers it.
      Written as a pin rather than a branch: a fifth clause spelling the same
      rule is a second derivation of one answer.
    * **C4** two regions contain it -> the LATER one in document order wins,
      which is the tie-break ``executor.actuated_element`` already applies to
      overlapping elements.

    ``_inside`` compares ABSOLUTE coordinates, so an inset window (a region at a
    non-zero origin) is judged by where it really is rather than by its size.
    """
    try:
        bounds = element.get("bounds") if isinstance(element, dict) else None
        found = None
        for region in regions if isinstance(regions, list) else []:
            if not isinstance(region, base.Region):
                continue
            if not _inside(bounds, region.bounds):
                continue
            # C4: LATER in document order wins. `>=` and not `>` so that two
            # regions recorded at the SAME order still resolve to the last one
            # seen, which is the order the dump itself lists them in -- equal
            # order is the only case this operator decides, and
            # `test_equal_order_regions_resolve_to_the_later_one_seen` is the
            # fixture that makes `>` fail.
            if found is None or region.order >= found.order:
                found = region
        if found is None:
            return (base.ORIGIN_NATIVE, base.FIDELITY_FULL)
        origin = found.origin if found.origin in base.ORIGINS else base.ORIGIN_NATIVE
        fidelity = (
            found.fidelity if found.fidelity in base.FIDELITIES else base.FIDELITY_FULL
        )
        return (origin, fidelity)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.providers.composite.partition failed")
        return (base.ORIGIN_NATIVE, base.FIDELITY_FULL)


def _inside(inner: object, outer: object) -> bool:
    """Whether *inner* is contained by *outer*, in ABSOLUTE screen coordinates.

    Absolute, not relative to anything: a rule written against a zero origin
    reads an inset window's child as outside it (or every child as inside).

    FOUR comparisons, and each one is graded ALONE. The fixture matrix carries
    a single-edge overhang per edge -- inside on three edges, outside on
    exactly one -- so deleting any single comparison admits exactly one
    impostor and exactly one test goes red. A fixture that is outside on two
    edges at once (the zero-origin box against an inset window) is rejected
    twice over and grades neither comparison, which is the trap that produced
    this repo's reverted viewport rule.
    """
    try:
        a = [int(value) for value in (inner or [])]
        b = [int(value) for value in (outer or [])]
        if len(a) != 4 or len(b) != 4:
            return False
        return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]
    # OverflowError rides in this tuple because `int(float('inf'))` raises it
    # and NOT ValueError, and a bounds list reaching here is outside input by
    # the time it is compared. `_inside` promises never to raise: a guard that
    # names two of the three coercion failures lets the third out of a
    # function the caller does not wrap.
    except (TypeError, ValueError, OverflowError):
        return False
