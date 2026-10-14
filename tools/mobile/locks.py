"""One emulator, one run at a time -- and the KERNEL decides who is alive.

Deliberately NOT a threading or asyncio lock. The thing being serialised is a
physical emulator shared by every chat on the machine, and the contenders are
separate OS processes (the MCP server, a second editor). A lock that only existed inside one interpreter would not see
them.

THE INVARIANT, and the reason this module was rewritten on 2026-09-04:

    A lock is released only by its HOLDER, or by the KERNEL.

Nothing else may release it -- not a contender, not a reaper, not a
force-release, not an ``unlink``. What this replaced asked the lock FILE whether
its holder was still alive, via a pid probe and a body timestamp, and then stole
the lock when it guessed "no". Every variant of that produced two simultaneous
holders: the forked race harness reproduced 25 overlapping holders in 40 trials
against the old module.

An advisory ``flock`` is dropped by the OS when the holding process exits,
however it exits -- cleanly, killed, or crashed. So there is no such thing as a
stale lock here, nothing to detect, nothing to steal, and no check-then-act
window. The whole staleness apparatus is DELETED rather than corrected.

WHY THERE IS NO ``unlink``. ``flock`` is bound to an INODE, not to a path.
Removing the file does not revoke a live holder's lock on its still-open fd; it
only lets the "force-releaser" create a NEW file at the same path and lock a
DIFFERENT inode, at which point two processes hold independent exclusive locks
and both drive the device -- deterministically, not as a race. Measured on this
machine before the rewrite. The lock file is therefore IMMORTAL: it is created
if absent and never removed, it carries no meaning, and an empty one costs
nothing.

WHAT THE BODY IS FOR. ``{pid, owner, time}`` is stamped in AFTER the lock is
held, for a human reading the file or a refusal message. **No code path reads it
back to make a decision.** That is the sharpest single difference from what this
replaced.

OWNERSHIP LIVES IN MEMORY, in :data:`_HELD`, guarded by :data:`_MUTEX`. The
mutex is not decoration: the heartbeat writer is a real background thread
(``tools/mobile/heartbeat.py``) that releases a run's lock when it loses the
run's lease, while the event-loop thread may be inside :func:`acquire`.
``heartbeat.py`` guards its own ``_WRITERS`` dict the same way, for the same
reason.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import errno  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import logging
import os  # noqa: F401
import re  # noqa: F401
import secrets  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import paths  # noqa: F401
from tools.mobile.locks_acquire import (  # noqa: F401
    _ACQUIRED_REASONS,
    _acquire_file,
    _acquire_in_process,
    _acquire_result,
    _contended_result,
    acquire,
)
from tools.mobile.locks_body import (  # noqa: F401
    _PLAUSIBLE_FLOOR_S,
    _body_age,
    _read,
    _stamp,
    lock_path,
)
from tools.mobile.locks_devices import (  # noqa: F401
    _unknown_device_row,
    device_holders,
    device_lock_name,
    held_names,
    names_held_by,
)
from tools.mobile.locks_fd import (  # noqa: F401
    _CONTENTION_ERRNOS,
    _is_contention,
    _lock_fd,
    _probe_free,
    _unlock_fd,
)
from tools.mobile.locks_holder import (  # noqa: F401
    HELD_TOO_LONG_S,
    _holder_result,
    holder,
)
from tools.mobile.locks_relabel import _relabel_refusal, relabel  # noqa: F401
from tools.mobile.locks_release import (  # noqa: F401
    _drop_held,
    _release_refusal,
    release,
)
from tools.mobile.locks_state import (  # noqa: F401
    _HELD,
    _MUTEX,
    _NAME_RE,
    DEVICE_LOCK_PREFIX,
    EMULATOR_LOCK,
    Claim,
    LockUnsupported,
    Release,
    _Held,
    _refuse_name,
    new_provisioning_owner,
)

logger = logging.getLogger("tools.mobile.locks")
