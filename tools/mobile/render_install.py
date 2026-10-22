"""The install-source menu, the App Tester hint and the ``apply`` refusal text.

Fifth layer of the ``render_*`` split of ``render.py``.

``install_menu_markdown`` -- the install-source menu, keyed for the same reason
the start menu is.

THREE HEADINGS, one per state the server can actually justify. This used to
head UNCONDITIONALLY with "The app under test is not on the emulator yet",
and its only caller runs the ``session.install_state`` probe only when a
``package`` was given -- so on the first call, with no package and no
source, nothing had been probed and the server asserted the app was absent
anyway. A tester agent repeated it back as fact (2026-09-08).

* no *package* -- ASK which app to test. Claim nothing about the device.
* a *package* that was probed -- today's line, and it is now earned.
* a *package* whose probe DID NOT ANSWER (``probed=False``) -- say the
  check failed. The caller reads that from ``install_state``'s
  ``content["probed"]``, which is the one witness of it. It is NOT derived
  from the envelope: ``install_state`` returns ``error: None`` with a
  populated ``content`` even when adb failed, so an envelope test asserted
  absence on exactly the failure it was written to catch.

``probed`` defaults to True so a caller that HAS confirmed absence reads
naturally, and the existing single-argument callers are unchanged.
"""

from __future__ import annotations

from tools.mobile.dump_latency import is_emulator
from tools.mobile.render_menu import INSTALL_SOURCES, _count_word, _keyed

_INSTALL_ASK = "Ask the user how they want it installed -- present EXACTLY these "

_INSTALL_MID = (
    " options as a multiple-choice question. Your UI may relabel or "
    "reorder them; the key shown on each line is what identifies it.\n\n"
)

_INSTALL_TAIL = (
    "\nThen call `qa_mobile_test` with `source` set to that option's key "
    "EXACTLY as printed above (never a number), the value in `app` (a path, "
    "a URL or a package name) and `apply=true`. Nothing is installed, "
    "downloaded or opened without `apply=true` -- ask the user first, on "
    "their turn, not on this one."
)

#: Why the menu came up with no package: the call named no app at all. A
#: screen-only check (is the device unlocked, what is showing) needs no app.
_NO_APP_NOTE = (
    "This call named no `app`, `package` or `source`, so there is no app to "
    "test yet. To only look at the device's current screen, call "
    "`qa_capture_screens(peek=true)` instead; it needs no app.\n\n"
)


def where_word(serial: str) -> str:
    """ "the emulator" for an emulator serial, else "this device"."""
    return "the emulator" if is_emulator(serial) else "this device"


def _install_head(package: str, probed: bool, where: str) -> str:
    head = "## Which app should this run test?\n\n" + _NO_APP_NOTE
    if package and probed:
        head = "## `" + str(package)[:80] + "` is not installed on " + where + "\n\n"
    elif package:
        head = (
            "## Could not check whether `"
            + str(package)[:80]
            + "` is on "
            + where
            + "\n\n"
        )
    return head


def install_menu_markdown(
    package: str = "", *, probed: bool = True, where: str = "the emulator"
) -> str:
    """The install-source menu, keyed like the start menu (see module doc)."""
    return (
        _install_head(package, probed, where)
        + _INSTALL_ASK
        + _count_word(len(INSTALL_SOURCES))
        + _INSTALL_MID
        + _keyed(INSTALL_SOURCES)
        + _INSTALL_TAIL
    )


#: Both package ids Firebase App Tester has shipped under. Matched EXACTLY: a
#: package name is device text, so a look-alike must not earn the hint.
APP_TESTER_PACKAGES = (
    "com.google.android.apps.firebase.appdistribution",
    "dev.firebase.appdistribution",
)

#: The id the Play Store lists App Tester under today (the first one is legacy).
APP_TESTER_STORE_PACKAGE = APP_TESTER_PACKAGES[1]

#: Static server text, not device text. It says nothing about HOW a build got onto
#: the device (the install source is not recorded), only how App Tester updates one.
APP_TESTER_HINT = (
    "App Tester (Firebase App Distribution) update flow: open the app's entry in "
    "its list, use its Download or Update button, then confirm the system "
    "installer. Signing in to App Tester is the tester's own step; never enter "
    "credentials for them."
)


def app_tester_hint(package: object) -> str:
    """The update-flow hint when *package* is App Tester under either id, else ``""``.

    Exact match on a ``str`` after ``strip()``: anything else (a look-alike, a
    non-string, an empty value) gets no hint. Never raises.
    """
    if isinstance(package, str) and package.strip() in APP_TESTER_PACKAGES:
        return APP_TESTER_HINT
    return ""


def apply_refusal(step: str, detail: str = "") -> str:
    """The preview a tester gets when ``apply`` is not sent."""
    return (
        "\U0001f50e **Preview — nothing was changed.** "
        + str(step or "That step")
        + " would run"
        + ((": " + str(detail)[:300]) if detail else "")
        + ".\n\nRe-call the same way with `apply=true` once the tester has said "
        "go, on their turn. If the tester already asked for this install, pass "
        "`apply=true` on the first call; no preview round is needed."
    )
