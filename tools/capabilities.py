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

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from tools.capabilities_diff import (  # noqa: F401
    EDITION_FIELDS,
    diff,
    losses,
)
from tools.capabilities_registry import (  # noqa: F401
    _as_mapping,
    _registry,
    _registry_async,
    prompt_names,
    prompts_by_name,
    tool_names,
    tools_by_name,
)

#: Bumped when the artifact shape changes incompatibly. A snapshot whose
#: ``schema`` differs from this is not diffed -- an unknown shape must not be
#: read as "everything was lost".
SCHEMA = 1

#: The settings fields that gate tool REGISTRATION, and nothing else. Verified
#: 2026-09-04 by reading every ``if`` inside ``mcp_server.build_server``: the
#: gates are ``mobile_lane_enabled()`` (``mobile_modules_present()``
#: alone), ``not test_cases_only()``
#: (``qa_dist_mode`` OR NOT ``_FULL_EDITION``), the TestRail/Xray push pair,
#: and ``qa_api_test_enabled``. ``qa_dist_mode`` is deliberately NOT an axis:
#: it is a fact about the tree, recorded under ``edition``, not an operator
#: choice worth enumerating.
AXES: tuple = (
    ("api", ("qa_api_test_enabled",)),
    # Registration no longer reads this field, so both halves of this axis
    # register the same tools. It is KEPT because the committed baselines under
    # operations/capabilities/ key every edition by it: dropping the axis is a
    # baseline migration with release-gate effect, not a prose edit.
    ("mobile", ("qa_mobile_https_capture_enabled",)),
    ("push", ("qa_testrail_push_enabled", "qa_xray_push_enabled")),
)


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


@dataclass(frozen=True)
class SnapshotInputs:
    """The injected facts ``build_snapshot`` enumerates one built tree from."""

    version: str
    build_server: Callable[[], Any]
    settings: Any
    edition: Mapping[str, bool]
    modules: Iterable[str]


def _enumerate(inputs: SnapshotInputs) -> dict:
    """The edition matrix: ``{edition_key: {"tools": ..., "prompts": ...}}``."""
    matrix: dict = {}
    for values in edition_matrix():
        for axis, fields in AXES:
            for field in fields:
                setattr(inputs.settings, field, bool(values[axis]))
        server = inputs.build_server()
        matrix[edition_key(values)] = {
            "tools": tool_names(server),
            "prompts": prompt_names(server),
        }
    return matrix


# Deterministic by construction: every collection is sorted, and nothing here
# records a timestamp, an absolute path, a host name or a duration. A snapshot
# that varied across two builds of the same commit would make the diff pure
# noise and the gate worthless.
#
# ``build_server`` is called once per edition, after the axis flags have been
# written onto ``settings`` -- the registration gates read those fields at
# call time, so one process can enumerate the whole matrix. Measured
# 2026-09-04 against the published dist tree: 0.64s to import, 0.77s for all
# eight editions, well inside the Constitution's 60s build budget.
#
# A MATRIX rather than a single snapshot, because a single one would not have
# caught the defect this exists for. The mobile kill-switch gated
# registration then and defaulted OFF, so v1.77.0's dist and the
# release before it both registered zero mobile tools *as configured* -- an
# empty diff, a green gate, and a dead lane. The question worth asking is
# conditional: *if an operator turns this on, do the tools appear?*
def build_snapshot(inputs: SnapshotInputs) -> dict:
    """The capability artifact for one built tree."""
    return {
        "schema": SCHEMA,
        "version": inputs.version,
        "edition": {f: bool(inputs.edition.get(f)) for f in EDITION_FIELDS},
        "matrix": _enumerate(inputs),
        "modules": sorted(inputs.modules),
    }
