"""Judge one already-pruned screen for accessibility defects. Pure, never raises.

**The data is already in the packet.** ``perception.prune`` reads content-desc,
resource-id, text, class, bounds, clickable, editable, secure and scrollable off
every node and ``perception.annotate`` derives a ``label`` and a ``role`` per
element. Every run of the mobile lane walks real screens through that parser and
nothing has ever asked whether those screens are USABLE. This module asks, over
the elements the packet already carries, with no device I/O of its own.

Four honesty rules, each of which is the reason a check is shaped the way it is:

* **An empty finding list is not a clean bill of health.** A canvas-drawn app
  (Flutter, React Native, a game, a WebView) exposes no accessibility nodes at
  all, and the audit of such a screen has audited NOTHING. ``auditable`` is
  False there and :data:`NO_NODES_NOTE` says so in words, because "no problems
  found" and "nothing to look at" rendering identically is the failure this
  repository keeps recording. The lane now puts a real screenshot in the packet,
  so that case is both distinguishable and common.
* **A partial audit says it is partial, everywhere.** ``prune`` truncates at
  ``MAX_ELEMENTS`` and reports ``truncated`` plus per-class drop counts. Those
  are read here rather than recomputed -- one producer, one meaning -- and
  ``complete`` is False whenever anything was dropped. :func:`summary_text`
  refuses to print a count without the partiality clause beside it, so no
  surface can quote "3 findings" over 150 of 400 elements as if it described the
  screen.
* **The false-positive direction is the one that makes an audit ignorable.** A
  decorative, non-interactive element with no label is CORRECT -- a screen
  reader should skip it -- so every check here is gated on the element being
  interactive (clickable or editable). Nothing else is ever a finding.
* **A finding is never a verdict.** Nothing in this module writes a verdict,
  a status or a reason, and no caller may map a finding onto one. An
  inaccessible screen is a real defect and a functional pass at the same time,
  and the lane's crash-driven verdict override (``case_runner._crash_override``)
  is a different channel that must not be entangled with this one.

DENSITY IS READ FROM THE DEVICE, AND SAYS SO WHEN IT IS NOT. Android's minimum
touch target is 48 **dp**, and dp needs the display density to become pixels. A
uiautomator dump has no dp in it, so ``adb.display_density`` reads ``wm density``
-- the same reason and the same shape as ``display_size`` reading ``wm size`` --
and every live prune site passes the answer here as ``density_dpi``.

When the device answers, the check is a MEASUREMENT: the floor is
``48dp x dpi/160`` for that device and the finding names the density it used.

When the device cannot answer, the floor falls back to
:data:`MIN_TOUCH_TARGET_PX` and **every finding of that kind says the density
was assumed and states the failure direction in words** -- because the error is
not symmetric. The threshold is ``48 x scale``, so a control that is genuinely
compliant is flagged whenever the real density is BELOW the assumed one: on
mdpi (160dpi) a compliant 48dp control is 48 physical px and on hdpi (240dpi) it
is 72, both under a 96px floor. Those are standard Android buckets, they are
common on budget and older hardware, and a low-density emulator profile reaches
them. Printing the number alone would tell a tester which value was used, not
that the finding may be a false alarm on their device, and a false positive is
what makes an audit ignorable.

:data:`ASSUMED_DENSITY_DPI` is 320 for what it actually does, not for being the
"low end" of anything -- it is xhdpi, the modal density of the emulator profiles
and modern phones this lane drives, so it is the value most likely to be
correct; and it sits below the 420-480dpi of current flagships, so where it is
wrong on a MODERN device it under-reports rather than over-reports. On a
low-density device it over-reports, which is exactly what the fallback wording
has to say out loud.
"""

from __future__ import annotations

import logging

from tools.mobile import perception

logger = logging.getLogger(__name__)

#: An interactive control that a screen-reader user cannot name at all.
KIND_UNNAMED_CONTROL = "unnamed_control"
#: An editable field with no accessible name. Split from the control kind on
#: purpose: the two domains are DISJOINT (editable first, clickable second), so
#: each clause has screens only it can reject and neither grades the other.
KIND_UNLABELLED_FIELD = "unlabelled_field"
#: An interactive target below the platform minimum, in pixels at a stated density.
KIND_SMALL_TARGET = "small_target"
#: Two or more interactive controls a user navigating by NAME cannot tell apart.
KIND_AMBIGUOUS_NAME = "ambiguous_name"
#: A password field whose dump carries the secret it holds.
KIND_SECURE_LABEL_LEAK = "secure_label_leak"

KIND_LABELS = {
    KIND_UNNAMED_CONTROL: "control with no accessible name",
    KIND_UNLABELLED_FIELD: "input field with no label",
    KIND_SMALL_TARGET: "touch target below the platform minimum",
    KIND_AMBIGUOUS_NAME: "controls sharing one accessible name",
    KIND_SECURE_LABEL_LEAK: "password field exposing its own value",
}

#: Findings carried out of one screen. The report renders every one into a
#: self-contained HTML file and the packet quotes the summary to a model.
MAX_FINDINGS = 40
#: Characters of any one finding's detail line, on both surfaces. It must be
#: large enough to carry :data:`ASSUMED_DENSITY_CAVEAT` WHOLE: a cap that clips
#: the words "this may be a false alarm" off the end of a warning leaves a
#: confident finding behind, which is worse than no warning at all. Pinned by
#: ``test_the_assumed_density_caveat_survives_the_character_cap``.
MAX_FINDING_CHARS = 400
#: Android's stated minimum touch target, in dp. The harmful direction is
#: UPWARD: a larger minimum flags targets that are fine.
MIN_TOUCH_TARGET_DP = 48
#: The definition of dp: 1dp is one pixel at 160dpi. Not a bound, a unit.
DENSITY_BASELINE_DPI = 160
#: The density used ONLY when the device did not answer. See the module
#: docstring for why 320 and why the fallback wording is not optional.
ASSUMED_DENSITY_DPI = 320
#: The dp minimum in pixels at :data:`ASSUMED_DENSITY_DPI` -- the FALLBACK floor
#: only; a device that answers gets its own floor computed from its own dpi. A
#: LITERAL, not a computed value: the cap scanner only sees ``NAME = <number>``,
#: so a derived constant would ship unbounded from above. The relation is pinned
#: by ``tests/mobile/test_mobile_screen_audit.py``.
MIN_TOUCH_TARGET_PX = 96

DENSITY_ASSUMED = "assumed"
DENSITY_DEVICE = "device"

#: The second half of an assumed-density finding, and the reason this module
#: does not simply print the number. ONE producer for the sentence, so the
#: packet and the report cannot warn differently -- or one of them not at all.
ASSUMED_DENSITY_CAVEAT = (
    " -- the device did not report its density, so this is an ASSUMPTION. On a "
    "low-density device (160-240dpi) a fully COMPLIANT 48dp control is only "
    "48-72px and would be flagged here, so confirm this one against the device "
    "before filing it."
)

NO_NODES_NOTE = (
    "This screen exposed no accessibility nodes, so NOTHING was audited -- that "
    "is not a clean result. A canvas-drawn app (Flutter, React Native, a game, "
    "a WebView) looks like this; judge it from the screenshot instead."
)
CLEAN_NOTE = "Every element this screen carried was audited; no check fired."

#: Resource-id words that name a widget rather than its purpose. A control whose
#: id reduces to these has no name derivable from it.
GENERIC_ID_WORDS = frozenset(
    {
        "btn",
        "button",
        "container",
        "content",
        "control",
        "edit",
        "field",
        "frame",
        "group",
        "icon",
        "image",
        "img",
        "item",
        "iv",
        "layout",
        "root",
        "row",
        "text",
        "tv",
        "view",
        "widget",
        "wrapper",
    }
)

#: Characters an app uses to MASK a password in its own dump. Text made only of
#: these is a mask, not a leak.
MASK_CHARS = frozenset("*•·●∙ ")


def _empty(present: bool) -> dict:
    """The shape every return of :func:`audit` has, with nothing found."""
    return {
        "present": bool(present),
        "auditable": False,
        "complete": False,
        "audited": 0,
        "dropped": 0,
        "truncated": False,
        "density_dpi": int(ASSUMED_DENSITY_DPI),
        "density_scale": float(ASSUMED_DENSITY_DPI) / float(DENSITY_BASELINE_DPI),
        "density_source": DENSITY_ASSUMED,
        "min_target_px": int(MIN_TOUCH_TARGET_PX),
        "findings": [],
        "capped": 0,
        "note": NO_NODES_NOTE if present else "",
    }


def _unwrap(screen: object) -> dict:
    body = screen if isinstance(screen, dict) else {}
    inner = body.get("content")
    return inner if isinstance(inner, dict) else body


def _rid_name(rid: object) -> str:
    """A name derivable from a resource id, or ``""``.

    ``perception.words`` is the one tokeniser -- deriving the same answer from a
    second regex here is how two rules drift apart.
    """
    tail = str(rid or "").rsplit("/", 1)[-1]
    tokens = [word for word in perception.words(tail) if word not in GENERIC_ID_WORDS]
    return " ".join(tokens)


def _is_interactive(element: dict) -> bool:
    return bool(element.get("clickable") or element.get("editable"))


def _finding(kind: str, ids: list, name: str, detail: str) -> dict:
    return {
        "kind": kind,
        "ids": [str(value) for value in ids][:12],
        "name": str(name or "")[:MAX_FINDING_CHARS],
        "detail": str(detail or "")[:MAX_FINDING_CHARS],
    }


def _masked(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(stripped) and not (set(stripped) - MASK_CHARS)


def _small_side(element: dict, floor: int) -> str:
    box = element.get("bounds") or []
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return ""
    try:
        width = int(box[2]) - int(box[0])
        height = int(box[3]) - int(box[1])
    except (TypeError, ValueError, OverflowError):
        return ""
    if width <= 0 or height <= 0:
        return ""
    if width >= floor and height >= floor:
        return ""
    return str(width) + "x" + str(height) + "px"


def _target_detail(side: str, floor: int, result: dict) -> str:
    """The touch-target finding's words. TWO wordings, never one with a caveat.

    A measured density states a fact. An assumed one states an assumption AND
    its failure direction, because the reader's next action differs: file it, or
    check the device first. The same rule the screenshot notes follow.
    """
    scale = "%.2gx" % float(result.get("density_scale") or 0)
    head = (
        "this target is "
        + side
        + ", under the "
        + str(floor)
        + "px that "
        + str(MIN_TOUCH_TARGET_DP)
        + "dp is at "
    )
    if result.get("density_source") == DENSITY_DEVICE:
        return (
            head
            + "this device's measured density of "
            + str(result.get("density_dpi"))
            + "dpi ("
            + scale
            + ")"
        )
    return (
        head
        + "an assumed "
        + str(result.get("density_dpi"))
        + "dpi ("
        + scale
        + ")"
        + ASSUMED_DENSITY_CAVEAT
    )


def audit(screen: object, density_dpi: object = None) -> dict:
    """Findings for one pruned, annotated screen. Never raises.

    ``density_dpi`` is what ``adb.display_density`` read off the DEVICE. It is
    DPI, never a scale factor -- the two differ by 160x and would be silently
    interchangeable if the parameter were called ``density``, which is the
    one-name-two-meanings defect this package has paid for before. ``None`` (a
    device that could not answer, or a caller with no device) means the floor is
    the assumed one and EVERY finding it produces says so.
    """
    try:
        content = _unwrap(screen)
        present = bool(
            content.get("screen_id") or content.get("hash") or "elements" in content
        )
        if not present:
            # No screen at all -- a different answer from a screen with no nodes,
            # and the surfaces render nothing rather than a false reassurance.
            return _empty(False)
        elements = [e for e in (content.get("elements") or []) if isinstance(e, dict)]
        result = _empty(True)
        # ONE coercion of this value, inside the guard that admits it may not be
        # representable, and everything derived from it computed there too. The
        # reader already rejects an implausible dpi, but by the time it arrives
        # here it is a stored packet's worth of trust, and it is a MULTIPLIER on
        # the floor -- a bad one rescales the check rather than failing it, so
        # out of range means "the device did not answer". Derived values land in
        # locals and are assigned together: a raise between two assignments
        # would otherwise leave a result claiming a measured density while still
        # carrying the default floor.
        try:
            dpi = int(density_dpi) if density_dpi else 0
            measured = dpi > 0 and (
                DENSITY_BASELINE_DPI // 4 <= dpi <= DENSITY_BASELINE_DPI * 8
            )
            scale = dpi / DENSITY_BASELINE_DPI if measured else 0.0
            measured_floor = (
                MIN_TOUCH_TARGET_DP * dpi // DENSITY_BASELINE_DPI if measured else 0
            )
        except (TypeError, ValueError, OverflowError, ZeroDivisionError):
            dpi, measured, scale, measured_floor = 0, False, 0.0, 0
        if measured:
            result["density_dpi"] = dpi
            result["density_scale"] = scale
            result["density_source"] = DENSITY_DEVICE
            result["min_target_px"] = measured_floor
        floor = result["min_target_px"]
        # THE DROP COUNTS ARE PRUNE'S, not a second derivation of "what was
        # lost". `truncated` keeps its own meaning there (the dump offered more
        # than MAX_ELEMENTS) and is reported under that name.
        dropped = (
            perception._count(content.get("dropped_inside_panel"))
            + perception._count(content.get("dropped_outside_panel"))
            + perception._count(content.get("dropped_unclassified"))
        )
        result["dropped"] = dropped
        result["truncated"] = bool(content.get("truncated"))
        result["audited"] = len(elements)
        if not elements:
            result["note"] = NO_NODES_NOTE
            return result
        result["auditable"] = True
        result["complete"] = not dropped and not result["truncated"]

        findings: list = []
        by_name: dict = {}
        for element in elements:
            eid = str(element.get("id") or "?")
            label = str(element.get("label") or "").strip()
            rid_name = _rid_name(element.get("rid"))
            editable = bool(element.get("editable"))
            clickable = bool(element.get("clickable"))
            if element.get("secure"):
                text = str(element.get("text") or "").strip()
                if text and not _masked(text):
                    findings.append(
                        _finding(
                            KIND_SECURE_LABEL_LEAK,
                            [eid],
                            label,
                            "this password field's own dump carries "
                            + str(len(text))
                            + " characters of unmasked text, so the secret is "
                            "readable by anything that reads the screen",
                        )
                    )
            if editable:
                if not label and not rid_name:
                    findings.append(
                        _finding(
                            KIND_UNLABELLED_FIELD,
                            [eid],
                            "",
                            "an input with no text, no content-desc and no name "
                            "in its resource id: a screen reader announces it as "
                            "an edit box and nothing else",
                        )
                    )
            elif clickable:
                if not label and not rid_name:
                    findings.append(
                        _finding(
                            KIND_UNNAMED_CONTROL,
                            [eid],
                            "",
                            "a tappable control with no text, no content-desc and "
                            "no name in its resource id: there is nothing for a "
                            "screen reader to say and no way to work around it",
                        )
                    )
            if not _is_interactive(element):
                # A decorative element without a label is CORRECT. Flagging it is
                # what makes an audit ignorable.
                continue
            side = _small_side(element, floor)
            if side:
                findings.append(
                    _finding(
                        KIND_SMALL_TARGET,
                        [eid],
                        label or rid_name,
                        _target_detail(side, floor, result),
                    )
                )
            spoken = label or rid_name
            if spoken:
                key = (" ".join(spoken.lower().split()), str(element.get("role") or ""))
                by_name.setdefault(key, []).append(eid)
        for (spoken, _role), ids in by_name.items():
            if len(ids) > 1:
                # ONE finding per NAME, never per element: a list of ten "Delete"
                # rows is one ambiguity, and ten rows of it would spend the whole
                # cap on a single defect.
                findings.append(
                    _finding(
                        KIND_AMBIGUOUS_NAME,
                        ids,
                        spoken,
                        str(len(ids))
                        + " interactive controls answer to this one name, so a "
                        "user navigating by name cannot tell them apart",
                    )
                )
        result["capped"] = max(0, len(findings) - MAX_FINDINGS)
        result["findings"] = findings[:MAX_FINDINGS]
        result["note"] = _note(result)
        return result
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.screen_audit.audit failed")
        return _empty(False)


def _note(result: dict) -> str:
    """THE sentence every surface prints, composed once.

    Partiality is part of the sentence rather than a caveat a caller may forget:
    a count of findings over a truncated element set describes some of a screen,
    and saying so is not optional.
    """
    findings = list(result.get("findings") or [])
    capped = int(result.get("capped") or 0)
    audited = int(result.get("audited") or 0)
    dropped = int(result.get("dropped") or 0)
    if not result.get("auditable"):
        return NO_NODES_NOTE
    head = (
        (
            str(len(findings))
            + " accessibility finding"
            + ("" if len(findings) == 1 else "s")
            + " over "
            + str(audited)
            + " element"
            + ("" if audited == 1 else "s")
            + "."
        )
        if findings
        else CLEAN_NOTE
    )
    if capped:
        head += " " + str(capped) + " further finding(s) were not carried."
    if dropped or result.get("truncated"):
        head += (
            " THIS AUDIT IS PARTIAL: "
            + str(dropped)
            + " element(s) this screen offered were dropped before it ran"
            + (
                " and the screen exceeded the element cap"
                if result.get("truncated")
                else ""
            )
            + ", so it describes "
            + str(audited)
            + " of a larger screen and its silence is not evidence."
        )
    return head


def summary_text(result: object) -> str:
    """The audit as plain lines for a model surface. ``""`` when there is no screen.

    The CALLER wraps this in ``untrusted.wrap_untrusted`` -- the names quoted
    here are device text. The report gets the same facts through HTML escaping
    instead: two surfaces, two mechanisms.
    """
    try:
        body = result if isinstance(result, dict) else {}
        if not body.get("present"):
            return ""
        lines = ["accessibility: " + str(body.get("note") or "")]
        for finding in list(body.get("findings") or [])[:MAX_FINDINGS]:
            if not isinstance(finding, dict):
                continue
            kind = str(finding.get("kind") or "")
            lines.append(
                "- "
                + KIND_LABELS.get(kind, kind)
                + " ["
                + ",".join(str(value) for value in (finding.get("ids") or []))
                + "] "
                + (
                    str(finding.get("name") or "")
                    and ('"' + str(finding["name"]) + '" ')
                )
                + str(finding.get("detail") or "")
            )
        lines.append(
            "These are usability findings about the SCREEN. They are not this "
            "case's verdict and must not change it."
        )
        return "\n".join(lines)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.screen_audit.summary_text failed")
        return ""
