"""Diagnostics about section: how to read the page, and the page's own limits."""

from __future__ import annotations

from tools.mobile.report_media import CLIP_SCALE_NOTE
from tools.mobile.report_text import esc

#: The page's OWN limits, named. Each is a fact about what this report cannot show, and
#: each is here rather than absent because an absent limit reads as no limit.
#:
#: The first three are the gaps this change deliberately did NOT close -- see
#: docs/DECISIONS.md. Naming them on the page is the whole point: a tester who cannot see
#: a requirement id must be told no case record carries one, not left to conclude the run
#: had no requirements.
PAGE_LIMITS = (
    (
        "No requirement id per case",
        "No case record on disk carries one. Suite-lane intake would have to start carrying it; until then this page cannot trace a case to a requirement.",
    ),
    (
        "No per-step offset inside a clip",
        "media.finish_step writes one clip per submit and records no offset per action, so this page cannot point at the moment a step happened inside a recording.",
    ),
    (
        "No SDK, platform-tools, emulator or backend version",
        "The manifest stores none and this module reads none. The system image is the one provisioning fact that IS recorded.",
    ),
    (
        "This page cannot show that a case is CORRECT",
        "Every figure here is about what the emulator DID. Whether the app is right is the tester's question.",
    ),
    (
        "Calls the capture did not see are not here",
        "A request that never reached the proxy leaves no row, and an empty table is not evidence of an idle app.",
    ),
    (
        "No bodies below the decrypted tier",
        "Which tier a run reached is stated in the network section; below it, headers and bodies were never readable.",
    ),
)

_ABOUT_ROWS = [
    (
        "Results",
        "Every goal, turn and case reads as one of four words. Pass, fail and "
        "blocked are what the model that drove the run concluded after its last "
        "action; unfinished means it has not concluded yet. The report replays and "
        "records; it never judges a screen itself.",
    ),
    (
        "Pictures",
        "Each step shows the device's own screenshot before and after it. The "
        "outlined box and dot mark the element the step acted on. Where no "
        "picture was stored the step says so in its place. Pictures live in the "
        "run folder beside this page (../shots and media/), so keep the folder "
        "together when you share it.",
    ),
    ("Recordings", CLIP_SCALE_NOTE),
    (
        "LLM turns",
        "The planning turns the tester’s own chat model took: one "
        "plan per case, every escape-hatch re-plan, and every stop the server did "
        "not charge as an escape. Tokens and cost are not shown: no model call "
        "passes through this server, so it has nothing to meter. The figure can "
        "exceed the escape-hatch count beside it; the difference is the stops this "
        "run was given for free.",
    ),
    (
        "Private data",
        "Typed passwords and other marked values are written as *** in text. "
        "Screenshots and recordings cannot be masked: anything visible on the "
        "screen is in this folder, so treat it like the device itself.",
    ),
]


def _about_html() -> str:
    """How to read the page, said once."""
    rows = _ABOUT_ROWS + [(term, why) for term, why in PAGE_LIMITS]
    return (
        '<dl class="about">'
        + "".join(
            "<dt>" + esc(term, 120) + "</dt><dd>" + esc(why, 600) + "</dd>"
            for term, why in rows
        )
        + "</dl>"
    )
