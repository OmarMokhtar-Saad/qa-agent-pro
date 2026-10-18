"""Constants of the preflight: step errors, check names, advisory sets, the free-space floor."""

from __future__ import annotations

import asyncio

#: What a probe step can raise: a process, file or socket failure, a timeout,
#: or a malformed answer from adb/emulator tooling. A step that raises one of
#: these becomes a failing row; any other error is a bug and propagates to
#: ``check``'s catch-all.
_STEP_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    asyncio.TimeoutError,
)


#: Check names, in report order. Also the set the tests assert against, so a
#: check that silently stops being emitted fails the suite.
CHECK_NAMES: tuple[str, ...] = (
    "virtualization",
    "adb_responds",
    "adb_first_on_path",
    "emulator_booted",
    "device_screen",
    "device_dns",
    "package_installed",
    "ime_pinned",
    "ime_installed",
    "ime_selected",
    "ime_oracle",
    "free_disk",
    "cache_ownership",
    "host_privileges",
)

#: Free space the lane wants available before a RUN (not a provision): room for
#: dumps, checkpoints and a report.
RUN_FREE_BYTES = 2 * 1024 * 1024 * 1024

#: Checks whose failure disables a CAPABILITY rather than the run. The four IME
#: checks are advisory because a lane with no pinned keyboard can still tap,
#: swipe, scroll, assert and produce a report -- which is exactly what
#: qa-doctor tells the tester. Before this split, `ok` was `not failing` over
#: every check, so an unpinned keyboard refused the whole run and the product
#: contradicted its own advice.
#:
#: Named here rather than passed at each of the ten `_record` call sites: a flag
#: threaded through ten places is a flag someone forgets at the eleventh, and
#: the SET is the thing a reviewer needs to see in one glance.
#: ``cache_ownership`` is advisory for a DIFFERENT reason than the IME four, and
#: the reason is a considered trade rather than a softening: ``paths.ownership``
#: answers ``ok=False`` when the owner cannot be DETERMINED at all, so a blocking
#: check would newly refuse runs on POSIX installs that proceed today. The gate
#: that blocked was the SDK provisioner, retired 2026-09-18, so no SDK is written
#: into the cache any more and nothing needs to block. What was missing was telling the TESTER, which is what
#: this check does. ``test_the_ownership_check_cannot_block_a_run`` pins it.
#: ``host_privileges`` is advisory for the SAME reason as ``cache_ownership``,
#: and the reason is a considered trade rather than a softening:
#: ``host_privileges.probe`` answers ``undetermined`` when elevation cannot be
#: DETERMINED at all, so a blocking check would newly refuse runs that proceed
#: today -- on exactly the locked-down machines this check exists to help. And
#: elevation is not a precondition of a RUN in the first place: once
#: virtualization is on, a non-administrator account taps, swipes, asserts and
#: gets a report. No SDK download is left to gate (auto-provisioning was
#: retired 2026-09-18). What was missing was telling the TESTER, which is what
#: this check does. ``test_the_privilege_check_cannot_block_a_run`` pins it the
#: way ``test_the_ownership_check_cannot_block_a_run`` pins the one above.
ADVISORY_CHECKS: frozenset = frozenset(
    {
        "ime_pinned",
        "ime_installed",
        "ime_selected",
        "ime_oracle",
        "cache_ownership",
        # ADVISORY, and the trade is the same one the two rows around it make: an
        # app under test may be deliberately offline, and a run that only taps
        # and asserts on local screens needs no resolver at all. Blocking here
        # would refuse runs that work today in order to warn about a case that
        # may not apply. What was missing was TELLING the tester, which is what
        # this check does -- `test_the_dns_check_cannot_block_a_run` pins it.
        "device_dns",
        # WITHOUT this row the check is BLOCKING: `_record` computes
        # `blocking = name not in ADVISORY_CHECKS` and `ok` is derived from the
        # blocking failures, so a `can_elevate=False` -- or an UNDETERMINED --
        # verdict would refuse every mobile run on exactly the locked-down
        # machines this feature exists to help. The membership, not the
        # docstring, is the mechanism; `test_the_privilege_check_cannot_block_a_run`
        # pins it and mutant 9 reverts this line.
        "host_privileges",
    }
)


#: The IME check that BLOCKS when the caller says the script types
#: (``check(needs_typing=True)``). Only ``ime_pinned``: without a pinned APK
#: nothing can install the keyboard. ``ime_installed``/``ime_selected`` stay
#: advisory because ``executor.replay`` installs and selects on demand, so
#: before the first replay they are legitimately false; ``ime_oracle`` is
#: checked by that replay's own probe.
TYPING_BLOCKING_CHECKS: frozenset = frozenset({"ime_pinned"})
