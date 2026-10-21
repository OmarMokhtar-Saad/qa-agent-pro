"""Is a process with this pid running? One non-killing probe for every platform.

POSIX asks ``os.kill(pid, 0)``. Windows must never use that call: it is
``TerminateProcess`` there and would kill the very process being asked about. So
Windows opens the process read-only and reads its exit code (``STILL_ACTIVE``).
A reused pid reads as alive on both; an unknown failure reads as alive too, so a
caller keeps what it would otherwise delete or close.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
_STILL_ACTIVE = 259
_ACCESS_DENIED = 5


def is_windows() -> bool:
    """One seam so a test can exercise the Windows branch without patching ``os.name``."""
    return os.name == "nt"


def kernel32():  # pragma: no cover - Windows only
    """kernel32 with typed signatures: a HANDLE is pointer-sized, never a C int."""
    api = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _windows_alive(pid: int) -> bool:
    """Open the process read-only and read its exit code; never signals it."""
    api = kernel32()
    handle = api.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return (
            getattr(ctypes, "get_last_error")() == _ACCESS_DENIED
        )  # exists, not ours to query
    try:
        code = wintypes.DWORD(0)
        if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        api.CloseHandle(handle)


def pid_is_alive(pid: int) -> bool:
    """True when *pid* is a running process (or its state cannot be told)."""
    try:
        if is_windows():
            return _windows_alive(pid)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, OverflowError, TypeError, ValueError, AttributeError):
        return True  # PermissionError (not ours) and anything odd: keep it
