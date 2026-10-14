"""Pass 2 prompt blocks: clustering, interleaving and the budgeted checklist block."""

from __future__ import annotations

import logging

from tools import atomic_checklist_core as _core
from tools.atomic_checklist_budget import (
    _format_checklist_prompt_block_cap,
    _format_checklist_prompt_block_note,
    _too_small,
    _wrapped,
)
from tools.atomic_checklist_core import (
    _CLUSTER_JOIN_SIM,
    _DEFAULT_CLUSTER_SIZE,
    ChecklistItem,
)

logger = logging.getLogger("tools.atomic_checklist")


class _Clusters:
    def __init__(self, size: int) -> None:
        self.size = size
        self.clusters: list[list[ChecklistItem]] = []
        self.centroids: list[set[str]] = []

    def _slot(self, toks: set[str]) -> int | None:
        for idx, cl in enumerate(self.clusters):
            if len(cl) >= self.size:
                continue
            if _core._jaccard(toks, self.centroids[idx]) >= _CLUSTER_JOIN_SIM:
                return idx
        return None

    def add(self, it: ChecklistItem) -> None:
        toks = set(_core._tokens(it.text))
        idx = self._slot(toks)
        if idx is None:
            self.clusters.append([it])
            self.centroids.append(set(toks))
        else:
            self.clusters[idx].append(it)
            self.centroids[idx] |= toks


# Group the checklist into deterministic semantic clusters of <= ``size``.
#
# Greedy, order-stable, pure-lexical (no LLM, no embeddings): each item joins
# the first non-full cluster whose token centroid it overlaps by at least
# ``_CLUSTER_JOIN_SIM``, else it starts a new cluster. The point is NOT perfect
# topic modelling — it is keeping each block of the prompt (and each
# remediation round) down to 5-8 coherent constraints instead of one flat 40+
# item wall. Never raises; degrades to fixed-size chunks.
def cluster_items(
    items: list[ChecklistItem], size: int = _DEFAULT_CLUSTER_SIZE
) -> list[list[ChecklistItem]]:
    """Group the checklist into deterministic semantic clusters of <= ``size``."""
    try:
        if not items:
            return []
        size = max(1, int(size))
        packer = _Clusters(size)
        for it in items:
            packer.add(it)
        return packer.clusters
    except Exception:
        logger.exception("cluster_items failed — falling back to fixed chunks")
        step = max(1, int(size))
        return [items[i : i + step] for i in range(0, len(items), step)]


def _interleave(base: list, extra: list) -> list:
    out: list = []
    stride = len(base) / (len(extra) + 1)
    next_at = stride
    ei = 0
    for i, item in enumerate(base):
        while ei < len(extra) and i >= next_at:
            out.append(extra[ei])
            ei += 1
            next_at += stride
        out.append(item)
    out.extend(extra[ei:])
    return out


# Spread ``extra`` evenly through ``base``, order-stable and deterministic.
#
# Used when both lists carry DOCUMENTED requirements, so neither may be
# systematically starved by the prompt budget. The seam used to APPEND, which
# put every ``extra`` item behind all of ``base``: because
# ``format_checklist_prompt_block`` spends its budget in order, a 200-item
# ticket presented 0 of 40 mandated bilingual lines at any realistic
# requirement length (measured: 145+ chars per item at the 32000 default). The
# bilingual pack therefore generated no bilingual cases on exactly the tickets
# big enough to need it.
#
# Interleaving makes truncation hit both sets in proportion instead. It is NOT
# used for implied/policy lines -- those must stay last, because assumed
# coverage displacing a documented requirement is the wrong trade.
#
# Never raises; degrades to plain concatenation.
def interleave_by_share(base: list, extra: list) -> list:
    """Spread ``extra`` evenly through ``base``, order-stable and deterministic."""
    try:
        if not extra:
            return list(base)
        if not base:
            return list(extra)
        return _interleave(base, extra)
    except Exception:
        logger.exception("interleave_by_share failed — concatenating instead")
        return list(base) + list(extra)


# The clustered checklist as its OWN untrusted block for the user message.
#
# Returns ``(block, presented_item_ids)``.
#
# WHY THE SECOND RETURN VALUE EXISTS. The block is capped at
# ``QA_CHECKLIST_MAX_PROMPT_CHARS``. If the cap were applied by handing the
# whole body to ``wrap_untrusted(limit=...)``, the tail of the checklist would
# be silently cut and never reach the generator, with no way to tell which
# items were dropped. Instead the budget is spent ITEM BY ITEM, and the ids
# that actually fitted are returned alongside the block, so a caller can
# always distinguish a presented requirement from a truncated one.
#
# Returns ``("", [])`` for an empty checklist so a flag-OFF run's prompt is
# byte-identical to today's. Never raises.
def format_checklist_prompt_block(
    items: list[ChecklistItem], limit: int | None = None
) -> tuple[str, list[str]]:
    """The clustered checklist as its OWN untrusted block for the user message."""
    try:
        if not items:
            return "", []
        cap = _format_checklist_prompt_block_cap(limit)
        lines, presented = _format_checklist_prompt_block_lines(items, cap)
        body = "\n".join(lines).strip()
        if not body:
            return _too_small(cap)
        note = _format_checklist_prompt_block_note(items, presented, cap)
        return _wrapped(body, cap, note), presented
    except Exception:
        logger.exception("format_checklist_prompt_block failed — omitting the block")
        return "", []


# Skip THIS item and keep going. This used to set a `stopped`
# flag and break out of BOTH loops, so a single over-long
# requirement ended the entire presentation with budget still
# unspent and every later (possibly short) item dropped.
def _fit_cluster(n: int, cluster: list, room: int) -> tuple[list, list, int]:
    head = f"Group {n}:"
    pending = [head]
    pending_ids: list[str] = []
    cost = len(head) + 1
    for it in cluster:
        line = f"- {it.item_id} [{it.ears_pattern}] {it.text}"
        if cost + len(line) + 2 > room:
            continue
        pending.append(line)
        pending_ids.append(it.item_id)
        cost += len(line) + 1
    return pending, pending_ids, cost


def _format_checklist_prompt_block_lines(
    items: list[ChecklistItem], cap: int
) -> tuple[list[str], list[str]]:
    """Spend ``cap`` item by item; return the lines and the ids that fitted."""
    lines: list[str] = []
    presented: list[str] = []
    used = 0
    for n, cluster in enumerate(cluster_items(items), 1):
        pending, pending_ids, cost = _fit_cluster(n, cluster, cap - used)
        if pending_ids:
            lines.extend(pending)
            lines.append("")
            used += cost + 1
            presented.extend(pending_ids)
    return lines, presented
