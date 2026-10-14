"""Learning stage locators: the ranked locator queue per element.

From each step's ``locator_used`` / ``locator_tried`` (the plug records them,
with the element's fingerprint as an ``fp:`` token) this creates or updates an
``elements`` row keyed by the fingerprint hash: per-locator hits and misses,
re-ranked best hit-ratio first (``LOCATOR_QUEUE_MAX``). The row is superseded
only when the ORDER changes; otherwise its counters are updated in place. A
heal (``heal:`` prefix) adds the new locator, so a renamed rid becomes a
superseding queue. A run already in a row's ``runs_json`` adds nothing.
"""

from __future__ import annotations

import json  # noqa: F401
import logging

from tools.mobile import knowledge_db, knowledge_limits, screen_key  # noqa: F401
from tools.mobile.knowledge_learn import StageResult
from tools.mobile.knowledge_plug_locators import (  # noqa: F401
    FP_PREFIX,
    HEAL_PREFIX,
    parse_queue,
)
from tools.mobile.kstg_loc_obs import collect, merge, rank  # noqa: F401
from tools.mobile.kstg_loc_write import (
    Item,
    create,
    replace_imported,
    squats,
    update,
)

logger = logging.getLogger(__name__)

_OPEN = "fp_hash = ? AND invalid_at IS NULL"


def _store(conn, found: list, item: Item) -> bool:
    if found and squats(found[0]):
        return replace_imported(conn, found[0], item)
    if found:
        return update(conn, found[0], item)
    return create(conn, item)


def run(conn, lc) -> StageResult:
    res = StageResult("locators")
    try:
        for key, obs in collect(list(lc.steps or [])).items():
            found = knowledge_db.rows(
                conn, "elements", knowledge_db.Where(_OPEN, (key[1],), 1)
            )
            res.wrote += 1 if _store(conn, found, Item(lc, key, obs)) else 0
    except Exception as exc:
        logger.exception("knowledge locators stage failed")
        res.error = (type(exc).__name__ + ": " + str(exc))[:200]
    return res
