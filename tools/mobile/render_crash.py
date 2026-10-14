"""The crash disclosure line a step reply carries.

Eighth layer of the ``render_*`` split of ``render.py``.

``crash_note`` -- the line that tells the tester's model the app itself died.
Never raises.

Nobody should have to open the HTML report to learn that the app under test
crashed, so the disclosure goes in the reply the model already gets.

**The marker and the excerpt are DEVICE OUTPUT reaching a model**, so they go
through ``tools/untrusted.wrap_untrusted`` -- the hard rule, and the same
treatment ``perception`` gives a screen dump. The HTML report deliberately
does NOT wrap them: it is not a model surface, and
``report_selfcheck``'s ``no_untrusted_markers`` pin forbids the string on the
page. Two surfaces, two treatments. The wrapped body needs no cap of its own:
it is bounded at its producer by ``crash_detector.MAX_MARKER_CHARS`` and
``MAX_EXCERPT_CHARS``, both of which carry a CEILINGS row.
"""

from __future__ import annotations

from tools.mobile.render_field import _field
from tools.mobile_evidence import crash_detector
from tools.untrusted import wrap_untrusted


def crash_note(case: object) -> str:
    """The line that tells the tester's model the app itself died. Never raises."""
    crash = crash_detector.crash_of_case(case)
    if not crash:
        return ""
    head = (
        "\U0001f6d1 **The app under test died during this case** — "
        + _field(crash.get("label"), "it stopped running")
        + ". The SERVER set this case to `fail` on the run's own logcat; that is "
        "not your judgement of the case, and re-submitting the same script will "
        "not change it."
    )
    block = wrap_untrusted(
        "device logcat",
        str(crash.get("marker") or "") + "\n" + str(crash.get("excerpt") or ""),
    )
    return head + (("\n\n" + block) if block else "")
