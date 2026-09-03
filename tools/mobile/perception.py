"""Screen perception: the uiautomator XML dump, pruned to what a model needs.

**The dump is attacker-influenced content.** Any app on the device controls the
``text``, ``content-desc`` and ``resource-id`` of its own nodes, and a malicious
or merely careless app can put anything at all in them -- including markup that
looks like ours. Three consequences, each pinned by a test:

1. **No DOCTYPE, no ENTITY.** ``xml.etree.ElementTree`` does not fetch external
   entities, but it DOES expand internal ones, which is the billion-laughs
   amplification. ``defusedxml`` would close that for us; it is not a dependency
   of this project and the programme contract forbids adding one. So the parse
   refuses outright when the document declares either -- a real uiautomator dump
   never does, so nothing legitimate is lost.
2. **Byte cap BEFORE parsing.** ``MAX_DUMP_BYTES`` is checked against the encoded
   length before a parser ever sees the string, because a cap applied afterwards
   is not a cap.
3. **Every attribute is untrusted text.** Attribute values are length-capped,
   control characters are stripped, and anything that could be mistaken for our
   own prompt scaffolding (an ``<untrusted_content>`` tag, the ``_GUARD``
   security-note opener) is neutralised HERE -- ``untrusted.wrap_untrusted``
   strips the tags but knows nothing about the guard note, and a screen that
   could forge one would be talking directly to the tester's model.

Nothing here raises; every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from xml.etree import ElementTree

from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

# Matched against the TRANSPORT cap in ``adb.uiautomator_dump``. Both exist on
# purpose: the transport one stops a huge dump from being carried at all, this
# one stops a huge string reaching a parser by any other route (a replayed
# checkpoint, a test fixture, a future file-backed dump).
MAX_DUMP_BYTES = 4 * 1024 * 1024

# The packet cap. 150 elements is roughly three screens' worth of controls; the
# reason for a cap at all is that the packet goes to the tester's own model and
# a 900-node ScrollView would crowd out the case it is meant to execute.
MAX_ELEMENTS = 150

# Per-attribute cap. Long enough for a paragraph of on-screen copy, short enough
# that one hostile node cannot dominate the packet.
MAX_ATTR_CHARS = 200

# Prompt-block cap handed to ``wrap_untrusted``.
MAX_BLOCK_CHARS = 12000

# A node with no text, no description, no resource id and no affordance is pure
# layout scaffolding: it cannot be a target and it cannot be asserted on.
EDITABLE_CLASS_HINTS = (
    "EditText",
    "AutoCompleteTextView",
    "SearchView",
    "TextInputEditText",
)

_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_BOUNDS_RE = re.compile(r"\[(-?\d{1,7}),(-?\d{1,7})\]\[(-?\d{1,7}),(-?\d{1,7})\]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t]{2,}")

# A derived marker must never be longer than this, so a pathological guard
# cannot turn every screen into one giant redaction.
MAX_GUARD_SENTINEL_CHARS = 60


def _guard_sentinel() -> str:
    """The opening LABEL of ``untrusted._GUARD``, derived rather than copied.

    This used to be a hardcoded copy of that label, and that was a silent-drift
    defect rather than a style problem. ``_GUARD`` is owned by
    ``tools/untrusted.py``; a rewording there would leave this module
    neutralising a phrase the product no longer sends, the forged-guard hole
    would reopen, and **no test would notice**, because a test pinning the old
    copy still passes. Note that this docstring deliberately does not spell the
    current label either: a comment naming it would defeat any future grep for
    a hardcoded guard phrase.

    So the marker is taken as a PREFIX of the live ``_GUARD``. Both branches
    below slice ``text``, which makes "the marker is a substring of the live
    guard" true BY CONSTRUCTION rather than by anyone remembering.
    ``test_the_guard_sentinel_is_derived_from_the_live_guard_not_copied``
    asserts that property, so a divergence fails CI instead of failing quietly.
    """
    text = str(_GUARD or "").strip()
    head, separator, _rest = text.partition(":")
    if separator and 0 < len(head) <= MAX_GUARD_SENTINEL_CHARS:
        return head + separator
    return text[:MAX_GUARD_SENTINEL_CHARS]


GUARD_SENTINEL = _guard_sentinel()

# Everything a screen must not be able to say to a model. ``untrusted_content``
# duplicates ``wrap_untrusted``'s own strip deliberately: the pruned dict is also
# written to the run store and rendered into the report, neither of which goes
# through ``wrap_untrusted``. ``GUARD_SENTINEL`` covers the other half, which
# ``wrap_untrusted`` does NOT strip -- measured, not assumed.
# Falsy markers are DROPPED. An empty or whitespace-only ``_GUARD`` (a refactor
# that assembles it at call time, or renames it to a falsy default) makes
# GUARD_SENTINEL "", and "" is a substring of every string -- the neutraliser
# would then be inserted between every character of every attribute and the
# length cap would discard the real content, leaving a screen where nothing
# resolves and no assert matches, with no refusal to explain it. The
# pathological-LONG case was already handled by MAX_GUARD_SENTINEL_CHARS; this
# is the other end of the same edit.
GUARD_MARKERS = tuple(
    marker
    for marker in (
        "untrusted_content",
        GUARD_SENTINEL,
    )
    if marker
)
NEUTRALIZED = "[neutralized]"

# Refusal texts. Constants so the tests assert on the module's own words rather
# than on a copy, and so the reason a dump was refused is stable enough for a
# handler to branch on.
DOCTYPE_REFUSAL = (
    "This screen's uiautomator dump declares a DOCTYPE or an ENTITY and was "
    "discarded unparsed. A real Android dump never does; something on the "
    "device produced it, so it is treated as hostile rather than repaired."
)
OVERSIZE_REFUSAL = (
    "This screen's uiautomator dump is larger than the "
    + str(MAX_DUMP_BYTES)
    + " byte cap and was discarded before parsing."
)
NOT_XML_REFUSAL = (
    "This screen's uiautomator dump could not be parsed as XML and was "
    "discarded. Re-dump the screen; if it keeps failing, a secure window (a "
    "password field or a payment sheet) is blocking accessibility."
)


def _clean(value: object) -> str:
    """One untrusted attribute value, made safe to show and to store."""
    text = value if isinstance(value, str) else str(value or "")
    text = _CONTROL_RE.sub("", text).replace("\r", " ").replace("\n", " ")
    for marker in GUARD_MARKERS:
        if marker.lower() in text.lower():
            text = re.sub(re.escape(marker), NEUTRALIZED, text, flags=re.IGNORECASE)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > MAX_ATTR_CHARS:
        text = text[:MAX_ATTR_CHARS] + "..."
    return text


def _flag(node: ElementTree.Element, name: str) -> bool:
    return str(node.get(name) or "").strip().lower() == "true"


def parse_bounds(raw: object) -> tuple[int, int, int, int] | None:
    """``[x1,y1][x2,y2]`` -> a 4-tuple, or None when it is not that shape."""
    match = _BOUNDS_RE.search(str(raw or ""))
    if not match:
        return None
    x1, y1, x2, y2 = (int(match.group(index)) for index in (1, 2, 3, 4))
    return x1, y1, x2, y2


def _area(bounds: tuple[int, int, int, int]) -> int:
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _short_class(full: str) -> str:
    tail = str(full or "").rsplit(".", 1)[-1]
    return tail[:60]


def _is_editable(full_class: str) -> bool:
    text = str(full_class or "")
    return any(hint in text for hint in EDITABLE_CLASS_HINTS)


def _screen_id(package: str, activity: str, texts: list[str]) -> str:
    """Stable identity of a screen: package + activity + its top three texts.

    Deliberately NOT the full element hash -- a list that scrolled by one pixel
    is the same screen to a tester, and the report dedupes on this.
    """
    seed = "|".join([package, activity] + texts[:3])
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


def prune(xml: object, activity: str = "") -> dict:
    """Untrusted dump -> ``{"screen_id", "elements", "hash", "package", ...}``.

    Returns the ``{"error", "content"}`` shape every module in this package
    returns, rather than the bare dict the programme spec sketched: a refusal
    here (hostile dump, oversize, unparseable) is information a handler must
    render, and a bare dict has nowhere to put it.
    """
    try:
        if not isinstance(xml, str) or not xml.strip():
            return {"error": NOT_XML_REFUSAL, "content": None}
        if len(xml.encode("utf-8", errors="replace")) > MAX_DUMP_BYTES:
            return {"error": OVERSIZE_REFUSAL, "content": None}
        if _DECL_RE.search(xml):
            return {"error": DOCTYPE_REFUSAL, "content": None}
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            return {"error": NOT_XML_REFUSAL, "content": None}

        root_bounds = None
        elements: list[dict] = []
        texts: list[str] = []
        package = ""
        considered = 0
        for node in root.iter("node"):
            considered += 1
            bounds = parse_bounds(node.get("bounds"))
            if bounds is None or _area(bounds) <= 0:
                continue
            if root_bounds is None:
                root_bounds = bounds
            elif (
                bounds[0] >= root_bounds[2]
                or bounds[1] >= root_bounds[3]
                or bounds[2] <= root_bounds[0]
                or bounds[3] <= root_bounds[1]
            ):
                # Entirely off-screen: laid out but not visible to the tester.
                continue

            full_class = str(node.get("class") or "")
            text = _clean(node.get("text"))
            desc = _clean(node.get("content-desc"))
            rid = _clean(node.get("resource-id"))
            clickable = _flag(node, "clickable") or _flag(node, "long-clickable")
            scrollable = _flag(node, "scrollable")
            editable = _is_editable(full_class)
            if not (text or desc or rid or clickable or scrollable or editable):
                # Pure layout scaffolding: not a target, not assertable.
                continue

            package = package or _clean(node.get("package"))
            if text:
                texts.append(text)
            elements.append(
                {
                    "id": "",
                    "cls": _short_class(full_class),
                    "text": text,
                    "desc": desc,
                    "rid": rid,
                    "bounds": list(bounds),
                    "clickable": clickable,
                    "editable": editable,
                    "checked": _flag(node, "checked"),
                    "scrollable": scrollable,
                }
            )

        truncated = len(elements) > MAX_ELEMENTS
        elements = elements[:MAX_ELEMENTS]
        for index, element in enumerate(elements, start=1):
            element["id"] = "e" + str(index)

        seed = "".join(
            "|".join(
                [
                    element["cls"],
                    element["text"],
                    element["desc"],
                    element["rid"],
                    ",".join(str(v) for v in element["bounds"]),
                ]
            )
            for element in elements
        )
        content = {
            "screen_id": _screen_id(package, _clean(activity), texts),
            "elements": elements,
            "hash": hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[
                :16
            ],
            "package": package,
            "activity": _clean(activity),
            "truncated": truncated,
            "considered": considered,
        }
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.perception.prune failed")
        return {"error": str(exc), "content": None}


def element_line(element: dict) -> str:
    """One pruned element as a single compact line."""
    parts = [str(element.get("id") or "?"), str(element.get("cls") or "?")]
    text = str(element.get("text") or "")
    desc = str(element.get("desc") or "")
    rid = str(element.get("rid") or "")
    if text:
        parts.append('text="' + text + '"')
    if desc and desc != text:
        parts.append('desc="' + desc + '"')
    if rid:
        parts.append("rid=" + rid)
    bounds = element.get("bounds") or []
    if len(bounds) == 4:
        parts.append("at[" + ",".join(str(int(v)) for v in bounds) + "]")
    for name in ("clickable", "editable", "checked", "scrollable"):
        if element.get(name):
            parts.append(name)
    return " ".join(parts)


def to_prompt_block(pruned: object) -> str:
    """The pruned screen, wrapped for a prompt. ``""`` when there is nothing.

    The RAW XML never reaches this function's output: only the fields ``prune``
    produced, each already neutralised. That is what makes the packet compact
    and what a Phase-3 test asserts by looking for ``<node`` in a packet.
    """
    try:
        content = pruned if isinstance(pruned, dict) else {}
        if content.get("content") and isinstance(content.get("content"), dict):
            content = content["content"]
        elements = content.get("elements") or []
        if not elements:
            return ""
        header = [
            "screen " + str(content.get("screen_id") or "?"),
            "package " + str(content.get("package") or "?"),
        ]
        activity = str(content.get("activity") or "")
        if activity:
            header.append("activity " + activity)
        lines = [" | ".join(header)]
        lines.extend(element_line(element) for element in elements)
        if content.get("truncated"):
            lines.append(
                "...[only the first "
                + str(MAX_ELEMENTS)
                + " elements of this screen are shown]"
            )
        return wrap_untrusted("screen", "\n".join(lines), limit=MAX_BLOCK_CHARS)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.perception.to_prompt_block failed")
        return ""
