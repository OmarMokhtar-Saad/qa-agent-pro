"""Ownership reply builders for the mobile cache (split out of ``paths``).

Pure: each function returns the ``{error, content}`` reply and reads nothing but
its arguments, so ``paths.ownership`` keeps every patchable lookup.
"""

from __future__ import annotations

from pathlib import Path


def _existing_ancestor(root: Path) -> Path:
    """The nearest existing path at or above *root* (at most 8 levels up)."""
    probe = root
    for _ in range(8):
        if probe.exists():
            break
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return probe


def _ownership_ok(probe: Path, mine: int) -> dict:
    """The reply when the cache path is owned by the current user."""
    return {
        "error": None,
        "content": {
            "ok": True,
            "checked": True,
            "detail": str(probe) + " is owned by uid " + str(mine),
            "fix": "",
        },
    }


def _ownership_unknown(exc: BaseException | None) -> dict:
    """The reply when the owner could not be read: treated as NOT ours."""
    return {
        "error": None,
        "content": {
            "ok": False,
            "checked": False,
            "detail": (
                "The owner of the mobile cache path could not be determined "
                "(" + str(exc)[:120] + "), so it is treated as NOT ours."
            ),
            "fix": (
                "Check that the path in QA_MOBILE_CACHE_DIR exists and is "
                "readable, or unset it to use ~/.qa-agents/mobile."
            ),
        },
    }


def _ownership_windows(root: Path) -> dict:
    """The reply for Windows, where the ownership check does not run."""
    return {
        "error": None,
        "content": {
            "ok": True,
            "checked": False,
            "detail": (
                "Ownership is not checked on Windows: a POSIX owner id "
                "carries no meaning there. The cache at "
                + str(root)
                + " sits under the user profile, whose ACL restricts it."
            ),
            "fix": "",
        },
    }


def _reply(ok: bool, detail: str, fix: str) -> dict:
    """The shared reply shape of a checked ownership verdict."""
    content = {"ok": ok, "checked": True, "detail": detail, "fix": fix}
    return {"error": None, "content": content}


def _ownership_refused(probe: Path, owner: int, mine: int) -> dict:
    """The reply when the cache path is owned by somebody else."""
    detail = (
        "The mobile cache path "
        + str(probe)
        + " is owned by uid "
        + str(owner)
        + ", not by you (uid "
        + str(mine)
        + "), so nothing was downloaded, installed or created there."
    )
    fix = (
        "Take ownership of that directory, or point the lane at a "
        "path you own with QA_MOBILE_CACHE_DIR in .env and restart "
        "the MCP server."
    )
    return _reply(False, detail, fix)
