"""What this build ACTUALLY exposes -- enumerated from a booted server.

A feature can be correct, tested and documented and still not REGISTER in the
edition that ships. This repo has shipped that defect three times: v1.77.0
carried every ``tools/mobile`` module, the IME pin and a README promising three
tools, and registered none of them; a documented, correct preview was
unreachable because reachability lives at the registration site; and a guard
mirrored one conjunct of a two-conjunct registration condition. Nothing
compared what a release CLAIMED to expose against what it DID.

This module is that comparison's data model. It holds two things and nothing
else:

* the ONE canonical way to enumerate a FastMCP server's tools and prompts
  (fastmcp 2.x exposes ``get_tools()``, which is a *coroutine function* as of
  2.14.7; 3.x moves to ``list_tools()`` returning a list; the fakes in this
  repo's tests expose a plain ``.tools`` dict). Three test files each carried
  their own slightly different copy of that compatibility ladder, which is the
  drift hazard this repo keeps paying for;
* :func:`build_snapshot` / :func:`diff`, the stable artifact shape and the
  definition of a capability LOSS.

**It imports nothing internal, on purpose.** The Constitution forbids a
``tools/`` module from importing ``mcp_server`` (it would stop being unit
testable) and from importing ``agents/``. So the server *builder*, the
``settings`` object and the edition facts are all injected by the caller --
``scripts/build_dist.py``'s probe, which runs inside the BUILT tree. That also
makes every function here testable with a fake builder and a namespace, which
is what lets each assertion below be mutation-proved.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Iterable, Mapping

#: Bumped when the artifact shape changes incompatibly. A snapshot whose
#: ``schema`` differs from this is not diffed -- an unknown shape must not be
#: read as "everything was lost".
SCHEMA = 1

#: The settings fields that gate tool REGISTRATION, and nothing else. Verified
#: 2026-09-04 by reading every ``if`` inside ``mcp_server.build_server``: the
#: gates are ``_mobile_lane_enabled()`` (``qa_mobile_run_enabled`` AND
#: ``_mobile_modules_present()``), ``not _test_cases_only()``
#: (``qa_dist_mode`` OR NOT ``_FULL_EDITION``), the TestRail/Xray push pair,
#: and ``qa_api_test_enabled``. ``qa_dist_mode`` is deliberately NOT an axis:
#: it is a fact about the tree, recorded under ``edition``, not an operator
#: choice worth enumerating.
AXES: tuple = (
    ("api", ("qa_api_test_enabled",)),
    ("mobile", ("qa_mobile_run_enabled",)),
    ("push", ("qa_testrail_push_enabled", "qa_xray_push_enabled")),
)

#: The edition facts a snapshot records. Booleans only: a flip in any of them
#: changes which tools can exist at all, so a flip is a capability event even
#: when no tool name moved.
EDITION_FIELDS = ("full_edition", "test_cases_only", "mobile_modules_present")


# --------------------------------------------------------- enumeration ----- #


# The helper this replaced (``tests/test_api_test_agent._list_tools``) tried
# ``srv._tool_manager.list_tools()`` FIRST. That branch is deliberately absent
# here: it is a private attribute, no fastmcp version reached in this tree
# resolves through it, and the two public shapes below cover 2.x and 3.x. A
# private fallback that never fires is a third shape to keep in step for no
# gain -- and three hand-mirrored copies of this ladder is the drift this
# module exists to end.
async def _registry_async(server: Any, methods: Iterable[str], attr: str) -> dict:
    """``{name: object}`` for one registry, across every fastmcp shape.

    ``FastMCP.get_tools`` is a *coroutine function* in fastmcp 2.14.7
    (measured), so a caller that forgets to await it compares a coroutine
    object against a dict, enumerates nothing, and passes. 3.x replaces it with
    ``list_tools()`` returning a list. The fakes in this repo's tests expose a
    plain ``.tools`` dict and no method at all, which is the third shape.
    """
    for name in methods:
        fn = getattr(server, name, None)
        if fn is None:
            continue
        res = fn()
        if inspect.isawaitable(res):
            res = await res
        return _as_mapping(res)
    return _as_mapping(getattr(server, attr, {}))


def _as_mapping(res: Any) -> dict:
    """A registry return of any shape as ``{name: object}``."""
    if isinstance(res, Mapping):
        return dict(res)
    return {getattr(item, "name", str(item)): item for item in res or ()}


def _registry(server: Any, methods: Iterable[str], attr: str) -> dict:
    """Sync bridge. Raises if a loop is already running -- use the async form."""
    return asyncio.run(_registry_async(server, methods, attr))


async def tools_by_name(server: Any) -> dict:
    """``{tool_name: Tool}`` -- the canonical async enumeration."""
    return await _registry_async(server, ("get_tools", "list_tools"), "tools")


async def prompts_by_name(server: Any) -> dict:
    """``{prompt_name: Prompt}`` -- the canonical async enumeration."""
    return await _registry_async(server, ("get_prompts", "list_prompts"), "prompts")


def tool_names(server: Any) -> list:
    """Sorted registered tool names. Sync; not for use inside a running loop."""
    return sorted(_registry(server, ("get_tools", "list_tools"), "tools"))


def prompt_names(server: Any) -> list:
    """Sorted registered prompt names. Sync; not for use inside a running loop."""
    return sorted(_registry(server, ("get_prompts", "list_prompts"), "prompts"))


# ------------------------------------------------------------ snapshot ----- #


def edition_key(values: Mapping[str, bool]) -> str:
    """``"api=0,mobile=1,push=0"`` -- a stable, sortable edition name."""
    return ",".join(f"{axis}={int(bool(values.get(axis)))}" for axis, _ in AXES)


def edition_matrix() -> list:
    """Every combination of the registration axes, in a fixed order."""
    out: list = [{}]
    for axis, _fields in AXES:
        out = [dict(base, **{axis: flag}) for base in out for flag in (False, True)]
    return sorted(out, key=edition_key)


def module_files(manifest_text: str) -> list:
    """The Python modules a manifest says this release ships, sorted.

    Read from ``MANIFEST.sha256`` rather than by walking the tree: the manifest
    IS the definition of the release (it is what the client verifies and what
    self-heal restores from), and a walk would also pick up the derived
    bytecode the boot leaves behind.
    """
    out = set()
    for line in manifest_text.splitlines():
        line = line.strip()
        if not line or "  " not in line:
            continue
        rel = line.partition("  ")[2].strip()
        if rel.endswith(".py"):
            out.add(rel)
    return sorted(out)


def build_snapshot(
    *,
    version: str,
    build_server: Callable[[], Any],
    settings: Any,
    edition: Mapping[str, bool],
    modules: Iterable[str],
) -> dict:
    """The capability artifact for one built tree.

    Deterministic by construction: every collection is sorted, and nothing here
    records a timestamp, an absolute path, a host name or a duration. A snapshot
    that varied across two builds of the same commit would make the diff pure
    noise and the gate worthless.

    ``build_server`` is called once per edition, after the axis flags have been
    written onto ``settings`` -- the registration gates read those fields at
    call time, so one process can enumerate the whole matrix. Measured
    2026-09-04 against the published dist tree: 0.64s to import, 0.77s for all
    eight editions, well inside the Constitution's 60s build budget.

    A MATRIX rather than a single snapshot, because a single one would not have
    caught the defect this exists for. ``qa_mobile_run_enabled`` is a
    category-1 kill-switch and defaults OFF forever, so v1.77.0's dist and the
    release before it both registered zero mobile tools *as configured* -- an
    empty diff, a green gate, and a dead lane. The question worth asking is
    conditional: *if an operator turns this on, do the tools appear?*
    """
    matrix: dict = {}
    for values in edition_matrix():
        for axis, fields in AXES:
            for field in fields:
                setattr(settings, field, bool(values[axis]))
        server = build_server()
        matrix[edition_key(values)] = {
            "tools": tool_names(server),
            "prompts": prompt_names(server),
        }
    return {
        "schema": SCHEMA,
        "version": version,
        "edition": {f: bool(edition.get(f)) for f in EDITION_FIELDS},
        "matrix": matrix,
        "modules": sorted(modules),
    }


# ---------------------------------------------------------------- diff ----- #


def diff(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> dict:
    """What moved between two snapshots.

    Returns ``{"lost", "gained", "flips", "detail"}``.

    * ``lost`` -- bare tool/prompt names that disappeared from AT LEAST ONE
      edition. Bare names, not edition-qualified, because the opt-out flag has
      to name the capability and an operator should not have to spell an edition
      key to authorise a deliberate removal. Any-edition rather than
      every-edition: a tool that stops registering on ONE edition is exactly the
      v1.77.0 defect, and it would survive an intersection.
    * ``gained`` -- names that appeared. Never a failure.
    * ``flips`` -- ``"edition:<field>"`` for each edition boolean whose value
      changed. A loss-class event because it changes which tools can exist.
    * ``detail`` -- ``{name: [edition keys]}`` for the message, so the operator
      is told WHERE it disappeared rather than just that it did.

    An edition key present in ``current`` but absent from ``baseline`` (a NEW
    axis) contributes nothing: no capability was lost by asking a question the
    previous release was never asked.
    """
    lost: set = set()
    gained: set = set()
    detail: dict = {}
    base_matrix = baseline.get("matrix") or {}
    cur_matrix = current.get("matrix") or {}
    for key in sorted(set(base_matrix) & set(cur_matrix)):
        for registry in ("tools", "prompts"):
            before = set((base_matrix[key] or {}).get(registry) or ())
            after = set((cur_matrix[key] or {}).get(registry) or ())
            for name in sorted(before - after):
                lost.add(name)
                detail.setdefault(name, []).append(key)
            gained |= after - before
    flips = [
        f"edition:{field}"
        for field in EDITION_FIELDS
        if bool((baseline.get("edition") or {}).get(field))
        != bool((current.get("edition") or {}).get(field))
    ]
    return {
        "lost": sorted(lost),
        "gained": sorted(gained),
        "flips": sorted(flips),
        "detail": {name: sorted(keys) for name, keys in sorted(detail.items())},
    }


def losses(delta: Mapping[str, Any]) -> list:
    """The capability names a build must not lose silently, sorted."""
    return sorted(set(delta.get("lost") or ()) | set(delta.get("flips") or ()))
