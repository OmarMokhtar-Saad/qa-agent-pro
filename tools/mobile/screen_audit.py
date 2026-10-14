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

from tools.mobile.saud_consts import (  # noqa: F401
    ASSUMED_DENSITY_CAVEAT,
    ASSUMED_DENSITY_DPI,
    CLEAN_NOTE,
    DENSITY_ASSUMED,
    DENSITY_BASELINE_DPI,
    DENSITY_DEVICE,
    KIND_AMBIGUOUS_NAME,
    KIND_LABELS,
    KIND_SECURE_LABEL_LEAK,
    KIND_SMALL_TARGET,
    KIND_UNLABELLED_FIELD,
    KIND_UNNAMED_CONTROL,
    MAX_FINDING_CHARS,
    MAX_FINDINGS,
    MIN_TOUCH_TARGET_DP,
    MIN_TOUCH_TARGET_PX,
    NO_NODES_NOTE,
)
from tools.mobile.saud_density import _empty
from tools.mobile.saud_elements import _audit_result
from tools.mobile.saud_names import _rid_name, _unwrap  # noqa: F401
from tools.mobile.saud_note import _finding_line

logger = logging.getLogger(__name__)


# Findings for one pruned, annotated screen. Never raises.
#
# ``density_dpi`` is what ``adb.display_density`` read off the DEVICE. It is
# DPI, never a scale factor -- the two differ by 160x and would be silently
# interchangeable if the parameter were called ``density``, which is the
# one-name-two-meanings defect this package has paid for before. ``None`` (a
# device that could not answer, or a caller with no device) means the floor is
# the assumed one and EVERY finding it produces says so.
def audit(screen: object, density_dpi: object = None) -> dict:
    """Findings for one pruned, annotated screen. Never raises."""
    try:
        content = _unwrap(screen)
        present = bool(
            content.get("screen_id") or content.get("hash") or "elements" in content
        )
        if not present:
            # No screen at all -- a different answer from a screen with no nodes,
            # and the surfaces render nothing rather than a false reassurance.
            return _empty(False)
        return _audit_result(content, density_dpi)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.screen_audit.audit failed")
        return _empty(False)


# The audit as plain lines for a model surface. ``""`` when there is no screen.
#
# The CALLER wraps this in ``untrusted.wrap_untrusted`` -- the names quoted
# here are device text. The report gets the same facts through HTML escaping
# instead: two surfaces, two mechanisms.
def summary_text(result: object) -> str:
    """The audit as plain lines for a model surface."""
    try:
        body = result if isinstance(result, dict) else {}
        if not body.get("present"):
            return ""
        lines = ["accessibility: " + str(body.get("note") or "")]
        for finding in list(body.get("findings") or [])[:MAX_FINDINGS]:
            if isinstance(finding, dict):
                lines.append(_finding_line(finding))
        lines.append(
            "These are usability findings about the SCREEN. They are not this "
            "case's verdict and must not change it."
        )
        return "\n".join(lines)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.screen_audit.summary_text failed")
        return ""
