"""Intake masking of a run's goal: its credentials become run secrets at once.

A goal the host wrote may carry an ID, a password or an OTP as prose. Registering
them with :mod:`tools.mobile.act_scrub` when the run is created makes every later
write (manifest, charter, cases) scrub them, even when the run never types.
"""

from __future__ import annotations

import re

from tools.mobile import act_scrub
from tools.mobile.run_mask_cue import GOAL_KEYS, cue_values

_ID_RUN = re.compile(r"(?<![\w.-])\d{8,}(?![\w-]|\.\d)")


def register_text(text: str, run_id: str) -> None:
    """Register each credential cue value and ID-shaped digit run in *text*."""
    for value in cue_values(text) + _ID_RUN.findall(text):
        act_scrub.register(value, run_id)


def register_goals(node: object, run_id: str) -> None:
    """Walk *node* and register the credentials in every goal-keyed string."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in GOAL_KEYS and isinstance(value, str):
                register_text(value, run_id)
            else:
                register_goals(value, run_id)
    elif isinstance(node, (list, tuple)):
        for item in node:
            register_goals(item, run_id)
