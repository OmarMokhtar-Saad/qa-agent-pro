"""Sidecar, ambiguity, image and duplicate wrappers (leaf of ``handlers.submit_suite``).

Tests patch or call these by name on the facade; the facade re-exports them and the
ports read them back through ``host()``.
"""

from __future__ import annotations

from typing import Any

from agents import (
    host_submit_ambiguity,
    host_submit_dup_notes,
    host_submit_images,
    host_submit_read,
    host_submit_sidecar,
)
from agents.host_submit_ambiguity import AmbRefusalIn
from agents.host_submit_types import SubRead, SubReq
from handlers.submit_suite_cports import _ports


# part A: moved to agents.host_submit_{load,sidecar,read} (C3a step 1). The
# wrappers below are patched or called by name in tests.
async def try_retain_sidecar(req: SubReq, sidecar_obj: Any) -> bool:
    return await host_submit_sidecar.try_retain(req, sidecar_obj)


async def _sidecar_no_rows_reply(req: SubReq, sidecar_obj: Any) -> str:
    return await host_submit_sidecar.no_rows_reply(req, sidecar_obj, _ports())


def sidecar_merge(rows: list, route: Any, meta: dict) -> tuple[Any, int]:
    args = host_submit_sidecar.MergeIn(rows, route, meta)
    return host_submit_sidecar.merge(args, _ports())


async def _read_via_sidecar(req: SubReq, route: Any) -> str | SubRead:
    return await host_submit_read.via_sidecar(req, route, _ports())


async def _read_full_suite(prep_id: str, suite_json: Any, meta: dict) -> str | SubRead:
    req = SubReq(prep_id, {}, meta, suite_json, True)
    return await host_submit_read.full_suite(req, _ports())


# part B
# moved to agents.host_submit_{intake,gates,ambiguity} (C3a step 2a). The
# wrapper is called directly by wording pins.
async def _ambiguity_refusal(args: AmbRefusalIn) -> str:
    return await host_submit_ambiguity.refusal(args, _ports())


# moved to agents.host_submit_images (C3a step 2c). Wording pins call it directly.
async def _image_gate_verdict(args: host_submit_images.GateIn) -> tuple[str, str]:
    return await host_submit_images.gate_verdict(args, _ports())


# moved to agents.host_submit_dup{,_notes} (C3a step 2d). Wording pins call it directly.
def _dup_status_note(args: host_submit_dup_notes.StatusIn) -> str:
    return host_submit_dup_notes.status_note(args)
