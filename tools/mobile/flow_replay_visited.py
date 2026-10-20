"""Saved-flow lookup across the packages a run has visited (B13).

A run pinned to the installer, or to App Tester, finds no flows under that
package although install and consent flows were learned elsewhere. The lookup
therefore scans the pinned package, the package of the live screen, and every
package this run was seen in. Knows no app: it only collects package names.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from tools.mobile import flow_replay_match, flow_replay_note, host_flow_goal

_KEEP_RUNS = 64
_KEEP_PACKAGES = 12
#: run id -> packages seen in that run, oldest first.
_SEEN: OrderedDict = OrderedDict()


def note(run_id: str, package: object) -> None:
    """Remember that run *run_id* stood in *package*."""
    name = str(package or "").strip()
    if not name or not run_id:
        return
    seen = _SEEN.setdefault(run_id, [])
    if name not in seen:
        seen.append(name)
        del seen[:-_KEEP_PACKAGES]
    _SEEN.move_to_end(run_id)
    while len(_SEEN) > _KEEP_RUNS:
        _SEEN.popitem(last=False)


def packages(run_id: str, ctx: Any, screen: Any) -> list:
    """The pinned package, the live screen's, then those seen this run (no repeats)."""
    body = screen if isinstance(screen, dict) else {}
    note(run_id, getattr(ctx, "package", ""))
    note(run_id, body.get("package"))
    live = [getattr(ctx, "package", ""), body.get("package")]
    rest = list(reversed(_SEEN.get(run_id, [])))
    return list(dict.fromkeys(str(p) for p in live + rest if p))


def resume(body: dict, ctx: Any, key: Any) -> tuple:
    """The flow a replay paused for a tester input (an OTP), when its next step fits *key*.

    B14: the host lane continues that flow where it stopped instead of starting over,
    so a flow runs as one pass with a stop only at the input it cannot know.
    """
    last = body.get("auto_replay") or {}
    name = str(last.get("name") or "")
    package = str(last.get("package") or getattr(ctx, "package", "") or "")
    if name and last.get("retry"):
        got = flow_replay_match.template(package, name)
        pos = int(last.get("pos") or 0)
        if got and flow_replay_match.starts(got, key, pos):
            return package, name, got, pos
    return "", "", {}, 0


def pick_first(ref: tuple, key: Any, body: dict) -> tuple:
    """``(package, name, template, at)`` of the first package holding a fitting flow.

    *ref* is ``(run_id, ctx, screen)``. The host lane's rule: only a flow whose FIRST step
    starts on *key*. Else ``("", "", {}, 0)``.
    """
    run_id, ctx, screen = ref
    paused = resume(body, ctx, key)
    if paused[2]:
        return paused
    for package in packages(run_id, ctx, screen):
        skip = host_flow_goal.skip(package, body)
        name, template, at = flow_replay_match.pick_first(package, key, skip)
        if template:
            return package, name, template, at
    return "", "", {}, 0


def alternatives(ref: tuple, key: Any, body: dict) -> list:
    """Names (with goal text) of the other saved flows that also start on *key* (B17: the host may pick one).

    Best first; the flow ``pick_first`` replays is left out.
    """
    run_id, ctx, screen = ref
    chosen = tuple(pick_first(ref, key, body)[:2])
    rows = []
    for package in packages(run_id, ctx, screen):
        skip = host_flow_goal.skip(package, body)
        for name, template, _at in flow_replay_match.first_fits(package, key, skip):
            rows.append((package, name, template))
    rows.sort(key=lambda r: flow_replay_match.rank((r[1], r[2], 0)), reverse=True)
    return [flow_replay_note.labelled(n, t) for p, n, t in rows if (p, n) != chosen]
