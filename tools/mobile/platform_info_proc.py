"""Process helpers of platform_info: reply tuples, detach kwargs, PowerShell argv (split out).

Pure: every host fact (is it Windows, which ``shutil.which``) comes in as an
argument, so ``platform_info`` keeps each patchable lookup.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from typing import Any, NamedTuple, cast

from config.settings_guard import never_raise
from tools.mobile.platform_info_table import (
    BATCH_SUFFIXES,
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    WIN_BATCH_MARKERS,
)
from tools.process_env import get_env_str
from tools.process_runner import CREATE_NO_WINDOW


class RunJob(NamedTuple):
    """What ``reply_tuple`` needs to phrase a failure: the argv, the budget, the OSError describer."""

    cmd: list[str]
    timeout: int
    describe: Callable[[list[str], OSError], str]


def detach_kwargs_for(windows: bool) -> dict:
    """``Popen`` keyword arguments that fully detach a child, for the given host kind."""
    if windows:
        return {
            "creationflags": DETACHED_PROCESS
            | CREATE_NEW_PROCESS_GROUP
            | CREATE_NO_WINDOW
        }
    return {"start_new_session": True}


def detached_devnull(detach: dict) -> dict[str, Any]:
    """``Popen`` keyword arguments for a detached child with all three std streams closed."""
    return {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        **detach,
    }


def reply_tuple(r: Any, job: RunJob) -> tuple[int, str, str]:
    """``(rc, stdout, stderr)`` from a runner result; failures map to 127, 124 and 126."""
    if r.not_found:
        return 127, "", "not found: " + (job.cmd[0] if job.cmd else "")
    if r.timed_out:
        return 124, "", "timed out after " + str(job.timeout) + "s"
    if isinstance(r.exc, OSError):
        return 126, "", job.describe(job.cmd, r.exc)
    if r.exc is not None:
        raise r.exc
    out = (r.stdout or b"").decode(errors="replace")
    err = (r.stderr or b"").decode(errors="replace")
    return cast(int, r.rc), out, err


def safe_which(which: Callable[[str], str | None], name: str) -> str:
    """``which(name)`` or ``""``; a resolver that raises is treated as "not found"."""
    # `Exception`, not `OSError`, and found by RUNNING this rather than
    # reading it: `shutil.which` branches on `sys.platform == "win32"`
    # itself and then touches `_winapi`, which is None on POSIX -- so under
    # a monkeypatched platform it raises `AttributeError`. That is a test
    # artefact rather than a production path, but a resolver that raises is
    # a resolver that cannot be tested here at all, and falling through to
    # the %SystemRoot% lookup is the right answer in both worlds.
    with never_raise():
        return which(name) or ""
    return ""


def _systemroot_ps(exe_name: str) -> str:
    """The canonical ``%SystemRoot%`` PowerShell path when that file exists, else ``""``."""
    root = get_env_str("SystemRoot")
    if not root:
        return ""
    candidate = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", exe_name)
    try:
        return candidate if os.path.isfile(candidate) else ""
    except OSError:  # pragma: no cover - defensive
        return ""


def powershell_argv(script: str, found: str, exe_name: str) -> list[str]:
    """The PowerShell argv for *script*: PATH hit, else ``%SystemRoot%``, else the bare name."""
    found = found or _systemroot_ps(exe_name)
    return [
        found or "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        str(script),
    ]


def _batch_refusal(binary: str, raw: str) -> str:
    """The message that names the unproven Windows ``.bat`` wrapper failure."""
    return (
        "Windows refused to run "
        + binary
        + " directly: a .bat wrapper is not an executable image, so "
        "CreateProcess rejected it ("
        + raw
        + "). THIS PATH IS UNPROVEN -- no Windows machine has run this lane. "
        "Please report this line together with the full path above, and do "
        "not work around it by editing the file. To tell our invocation "
        "apart from a broken SDK, run the same command yourself in a Command "
        "Prompt: if it works there, the fault is ours."
    )


# Three clauses, and each is graded by a fixture where only IT can reject the
# impostor: the platform, the `.bat` suffix, and the Win32 marker text. A
# non-batch OSError on Windows and a batch-shaped OSError on POSIX both fall
# through to the raw string, because dressing up an error we have not
# identified is how a wrong diagnosis gets believed.
def os_error_detail(cmd: list[str], exc: OSError, windows: bool) -> str:
    """*exc* as a message, NAMING the Windows batch-wrapper case when it is one."""
    raw = str(exc)
    binary = str(cmd[0]) if cmd else ""
    lowered = (raw + " " + str(getattr(exc, "strerror", "") or "")).lower()
    looks_like_batch = binary.lower().endswith(BATCH_SUFFIXES)
    marked = any(marker in lowered for marker in WIN_BATCH_MARKERS)
    if windows and looks_like_batch and marked:
        return _batch_refusal(binary, raw)
    return raw
