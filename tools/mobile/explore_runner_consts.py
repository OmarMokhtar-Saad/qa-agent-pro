"""Constants of the exploratory lane: extension and hand-over limits.

Cut from ``tools.mobile.explore_runner``; the facade re-exports every name here (identity).
"""

from __future__ import annotations

import re

MAX_EXTENSIONS = 1
EXTENSION_TURNS = 15
EXTENSION_S = 10 * 60
MAX_WATCH_ITEMS = 10
#: Fix round 3, item 1: the step of a multi-app goal the model is on now. It
#: rides every packet in place of the full goal, so it is short by design.
MAX_SUB_GOAL_CHARS = 200
#: A hand-over package longer than this is refused, never trimmed.
MAX_PACKAGE_CHARS = 200
#: Surfaces a hand-over may NEVER name: the system UI, settings, the dialer,
#: the browsers, the store and Play services. Handing the guard to one of them
#: would let the model drive the device's own chrome as "the app under test".
#: Matched as a PREFIX, so a sibling package of the same surface
#: (``com.android.settings.intelligence``,
#: ``com.google.android.gms.policy_sidecar_aps``) is refused with it. The
#: cost, stated: a run whose app under test IS one of these cannot be handed
#: over to it -- it must be the run's own package instead.
HANDOVER_REFUSED_PREFIXES = (
    "com.android.settings",
    "com.google.android.settings",
    "com.android.systemui",
    "com.android.dialer",
    "com.google.android.dialer",
    "com.android.chrome",
    "com.chrome.",
    "org.chromium.",
    "com.android.browser",
    "org.mozilla.",
    "com.sec.android.app.sbrowser",
    "com.android.vending",
    "com.google.android.gms",
    "com.google.android.gsf",
)
#: ... and any package whose name CONTAINS one of these words, whoever ships
#: it: every launcher, package installer and permission dialog.
HANDOVER_REFUSED_WORDS = ("launcher", "packageinstaller", "permissioncontroller")
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")


#: What the tester is TOLD when that happens, in the SAME reply. The lane's
#: rule: a site that refuses says so BY NAME and leaves no success-shaped
#: message behind it.
GUARD_REFUSED_NOTICE = (
    "This run's charter says `destructive: none`, so the guard's stop ENDED "
    "the attempt rather than pausing for you. Nothing was tapped and the run "
    "is over. Start a run with `destructive: reversible` if a stop should "
    "pause for a confirmation instead."
)
RUNNING = "running"
