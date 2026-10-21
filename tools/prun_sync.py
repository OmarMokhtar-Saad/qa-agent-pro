"""The synchronous runner leaf: ``subprocess.run`` that never raises (stdlib only)."""

from __future__ import annotations

import subprocess
from typing import Any

from config.settings_guard import never_raise
from tools.prun_types import RunOpts, RunResult, no_window_kwargs

_NO_OPTS = RunOpts()


def _run_kwargs(timeout: Any, opts: RunOpts) -> dict[str, Any]:
    """The ``subprocess.run`` keywords, only where the legacy site passed them."""
    kwargs: dict[str, Any] = {}
    if opts.capture:
        kwargs["capture_output"] = True
    kwargs["timeout"] = timeout
    kwargs["check"] = False
    if opts.shell is not None:
        kwargs["shell"] = opts.shell
    if opts.cwd is not None:
        kwargs["cwd"] = opts.cwd
    if opts.env is not None:
        kwargs["env"] = opts.env
    if opts.text:
        kwargs["text"] = True
    if opts.input is not None:
        kwargs["input"] = opts.input
    if opts.no_window:
        kwargs.update(no_window_kwargs())
    return kwargs


def _coerce_rc(proc: Any, mode: str) -> Any:
    """``returncode`` coerced the way *mode* (``rc_coerce``) says."""
    if mode == "int_or_zero":
        return int(proc.returncode or 0)
    if mode == "none":
        return proc.returncode
    return int(proc.returncode)


# ``check=False`` and ``timeout`` are always forwarded; every other keyword
# only when given (``None`` = not passed; ``input`` is tested ``is not None``
# so ``b""`` is still sent). ``rc_coerce`` is applied inside the guarded
# block: ``"int"`` is ``int(returncode)``, ``"int_or_zero"`` is
# ``int(returncode or 0)``, ``"none"`` leaves it as the object gave it (for a
# caller that returns ``completed`` untouched).
def run(args: Any, *, timeout: Any, opts: RunOpts = _NO_OPTS) -> RunResult:
    """Run *args* synchronously through ``subprocess.run``; never raises."""
    kwargs = _run_kwargs(timeout, opts)
    if opts.input is None:  # never inherit the MCP pipe (a reader would eat requests)
        kwargs["stdin"] = subprocess.DEVNULL
    with never_raise() as guard:
        proc = subprocess.run(args, **kwargs)  # noqa: S603 - caller's argv
        rc = _coerce_rc(proc, opts.rc_coerce)
        return RunResult(rc, proc.stdout, proc.stderr, completed=proc)
    exc = guard.exc
    return RunResult(
        None,
        timed_out=isinstance(exc, subprocess.TimeoutExpired),
        not_found=isinstance(exc, FileNotFoundError),
        exc=exc,
    )
