"""Prompt builders for the mobile lane, in the ``agents/host_*.py`` style.

No model runs on this server, so every decision a mobile run needs -- what to
tap, whether the goal was reached, what to do with a screen the plan did not
anticipate -- is a PACKET handed to the tester's own chat model and answered
through a submit tool. This module builds those packets and nothing else: it
touches no device, opens no file and makes no network call.

Three rules it holds, each pinned by a test:

* **Nothing here imports ``handlers/``.** That edge is what lets a
  packet be built and asserted on without the MCP transport.
* **The RAW dump never enters a packet.** Packets carry ``perception``'s pruned
  screen block, already neutralised and length-capped. A packet containing
  ``<node`` or ``<hierarchy`` is a bug, and a test says so.
* **A credential never enters a packet.** The planner is told to ask for a field
  BY NAME through ``ask_tester`` and to reference it by name in a ``type``
  action; the value only ever exists in the tester's own chat turn and on the
  device's stdin. ``build_tester_request`` carries the field name and the
  prompt, never a value.
"""

from __future__ import annotations

import logging
from types import ModuleType  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import actions as actions_mod  # noqa: F401
from tools.mobile import charter as charter_mod  # noqa: F401
from tools.mobile import (  # noqa: F401
    knowledge_inject,
    perception,
    render,
    run_store,
    screen_audit,
)
from tools.mobile.run_packets_args import (  # noqa: F401
    CaseRef,
    ExploreRef,
    TesterAsk,
)
from tools.mobile.run_packets_base import (  # noqa: F401
    _PACKET_ERRORS,
    _observation_key,
    _packet_base,
    _screen_block,
)
from tools.mobile.run_packets_case import (  # noqa: F401
    MAX_CASE_STEPS,
    MAX_TRACE_ENTRIES,
    _case_block,
    _trace_block,
    build_case_job,
    build_escape_job,
)
from tools.mobile.run_packets_explore import build_explore_turn  # noqa: F401
from tools.mobile.run_packets_guard import (  # noqa: F401
    GUARD_ASK_SCREEN_GENERIC,
    _guard_ask,
    build_tester_request,
    screen_asks,
)
from tools.mobile.run_packets_intake import (
    CHARTER_INTAKE_KIND,  # noqa: F401
)
from tools.mobile.run_packets_intake import (
    _charter_intake_packet as charter_intake_packet,
)
from tools.mobile.run_packets_text import (  # noqa: F401
    _BUDGET_NOTE,
    _CASE_INSTRUCTION,
    _ESCAPE_INSTRUCTION,
    _EXPLORE_INSTRUCTION,
    _LAST_ESCAPE_NOTE,
    _RECOVERY_NOTE,
    _SYSTEM_PROMPT,
)
from tools.untrusted import _GUARD, wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)


# Ask the tester for the run's TERMS -- the server's questions, once.
#
# The SERVER owns the question set and the typed schema
# (``tools/mobile/charter.py``); the model owns only the wording it puts the
# questions in. A model that improvises the questions gives every run a
# different charter shape, so nothing is resumable and two runs of "the
# same" exploration are not comparable.
#
# At most ``charter.MAX_QUESTIONS`` questions reach this packet, because
# ``charter.questions_for`` slices to that cap -- the number is not enforced
# here as well, so the two cannot drift.
#
# *charter* must be the RAW charter the tester sent -- the schema-stripped
# body ``charter.parse`` returns under ``raw`` -- and never the normalised
# terms. Normalisation fills every field, so a normalised empty charter
# yields ONE question instead of five. See ``charter.questions_for``.
#
# THE CHARTER HAS NO FIELD FOR A SECRET, and that is deliberate rather
# than an omission to correct: the schema carried a ``credentials`` list for
# one revision, nothing consumed it, and its only observable effect was
# writing secret-adjacent free text into a report that lands on disk.
# ``never_do`` says so in the packet. A credential VALUE exists only in the
# tester's own chat turn and on the device's stdin -- see this module's
# header and ``tools/mobile/ime.py``.
def build_charter_intake(
    *, run_id: str = "", package: str = "", charter: object = None
) -> dict:
    try:
        return charter_intake_packet(run_id, package, charter)
    except _PACKET_ERRORS:  # pragma: no cover - defensive
        logger.warning("build_charter_intake failed", exc_info=True)
        return {}
