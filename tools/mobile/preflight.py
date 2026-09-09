"""Nothing runs until every gate is green -- and the tester is told about ALL of them.

The single most important property of this module is that it does **not**
short-circuit. A tester whose emulator is not booted AND whose app is not
installed AND whose IME is not selected must learn all three from one call:
reporting them one per round trip turns a two-minute setup into three
conversations, and it is the reason the reference project's preflight exists at
all. Every check therefore runs in its own ``try`` and appends its own record,
and the summary ``ok`` is computed from the collected list afterwards.

Each record is ``{name, ok, detail, fix}``. ``fix`` is the sentence a
non-technical tester can act on; a check that cannot say how to fix itself is
not worth failing on.
"""

from __future__ import annotations

import logging

from config.settings import settings
from tools import host_privileges
from tools.device_manager import valid_package_name
from tools.mobile import (
    adb,
    downloader,
    emulator,
    ime,
    paths,
    platform_info,
    provisioner,
)

logger = logging.getLogger(__name__)


class _Answered(Exception):
    """Leave one check's block early without leaving the others unreported.

    Every check runs in its own ``try`` and appends its own record; the
    ``ime_selected`` branch needs to stop after appending an "it did not answer"
    record, and its ``try`` already catches ``Exception`` and would then append a
    SECOND, contradictory record for the same name. Caught by name, immediately,
    ahead of that handler.
    """


#: Check names, in report order. Also the set the tests assert against, so a
#: check that silently stops being emitted fails the suite.
CHECK_NAMES: tuple[str, ...] = (
    "virtualization",
    "adb_responds",
    "adb_first_on_path",
    "emulator_booted",
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

#: The name the DNS probe asks for. The platform's own connectivity check uses
#: this host, so the probe adds no lookup the device was not already making.
DNS_PROBE_HOST = "connectivitycheck.gstatic.com"

#: The address the probe pings once a NAME has failed, to tell a broken resolver
#: apart from a device with no route at all. A literal address is the whole
#: point: resolving anything here would beg the question being asked.
DNS_PROBE_IP = "8.8.8.8"

#: Seconds the ADB CALL is given for ONE probe. This is the whole budget for a
#: probe, and deliberately NOT a `ping -W`: `-W` bounds the wait for a REPLY,
#: and a lookup that never resolves never gets as far as waiting for one.
#: MEASURED on this lane's API 35 emulator against a stale resolver: a failing
#: lookup costs 20.06-20.12s across 16 samples, and does not move at `-W 1` or
#: `-W 10` -- what bounds it is Android's own resolver retry schedule, which
#: `-W` cannot reach. The budget must therefore clear ~20s with room for a
#: slower host. Under it the check reports its own timeout instead of the
#: diagnosis it exists to give, which is exactly what a tester was left with.
DNS_PROBE_TIMEOUT_S = 30

#: Seconds `ping -W` waits for a REPLY. It bounds the ADDRESS probe, whose host
#: needs no resolving, and is inert on the name probe as measured above. Kept
#: small for that reason: an address that does not answer promptly is not
#: routing, and waiting longer tells the tester nothing more.
DNS_PING_WAIT_S = 3

#: What the tester is told when names fail and packets still flow. The remedy is
#: the emulator's own behaviour, not a guess: it reads the host's resolvers ONCE,
#: at boot, so a host that changed network leaves the guest pointing at servers
#: that are no longer there.
DNS_EMULATOR_FIX = (
    "The emulator reads the host's DNS servers once, when it starts, so "
    "changing network on this machine leaves it pointing at resolvers that are "
    "gone. Restart the emulator, or start it with `-dns-server 8.8.8.8,1.1.1.1`."
)
DNS_DEVICE_FIX = (
    "The device answers pings but resolves no name. Check its own Wi-Fi or "
    "mobile data, and any VPN or private-DNS setting on it."
)
DNS_NO_ROUTE_FIX = (
    "The device reached nothing at all, by name or by address. Check that it is "
    "on a network before running cases that need one."
)

#: What the tester is told when the lookup OUTLASTS the budget rather than
#: failing outright. Reporting "adb did not answer within 30s" would be true and
#: useless: the thing that did not answer is the RESOLVER, and no longer wait
#: separates a resolver this slow from a broken one -- an app under test stalls
#: on it the same way either way. So the check says that in its own words, and
#: still sends the address probe, so the REMEDY is measured rather than guessed.
DNS_SLOW_NOTE = (
    "a resolver this slow cannot be told apart from a broken one by waiting "
    "longer, and an app under test stalls on it the same way"
)

_FLAG_FIX = (
    "Add `QA_MOBILE_RUN_ENABLED=true` to `.env` and restart the MCP server "
    "(quit and reopen the editor)."
)


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
#: that must block already blocks -- ``provisioner.run`` and
#: ``provisioner.start_detached`` both refuse on ``ok=False`` before anything is
#: written into the cache. What was missing was telling the TESTER, which is what
#: this check does. ``test_the_ownership_check_cannot_block_a_run`` pins it.
#: ``host_privileges`` is advisory for the SAME reason as ``cache_ownership``,
#: and the reason is a considered trade rather than a softening:
#: ``host_privileges.probe`` answers ``undetermined`` when elevation cannot be
#: DETERMINED at all, so a blocking check would newly refuse runs that proceed
#: today -- on exactly the locked-down machines this check exists to help. And
#: elevation is not a precondition of a RUN in the first place: once
#: virtualization is on, a non-administrator account taps, swipes, asserts and
#: gets a report. The gate that must block already blocks -- ``provisioner.run``
#: and ``provisioner.start_detached`` refuse on their own preconditions before
#: anything is written. What was missing was telling the TESTER, which is what
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


def _exit_code(body: object, default: int = 1) -> int:
    """A probe's exit code, with ZERO read as zero.

    ``int(body.get("rc") or 1)`` is the trap this exists to close: a successful
    command exits 0, 0 is falsy, and the fallback then turns every success into
    a failure. Written once because the same idiom was about to appear twice.
    """
    value = (body or {}).get("rc") if isinstance(body, dict) else None
    if value is None or isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _record(name: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {
        "name": str(name),
        "ok": bool(ok),
        "detail": str(detail),
        "fix": str(fix),
        "blocking": str(name) not in ADVISORY_CHECKS,
    }


def _unanswered(name: str, result: object, fix: str = "") -> dict | None:
    """The record for a probe that DID NOT ANSWER, or None when it did.

    ONE SHAPE, because this file has now produced the same defect three times:
    ``package_installed`` read ``adb.installed_packages(...).get("content")``
    with no look at ``error`` and printed "`pkg` is NOT installed" off a probe
    that failed, and ``ime_installed`` and ``ime_selected`` did the identical
    thing with ``ime.installed`` and ``ime.current_ime``. The same class had
    already been fixed at ``session.install_state`` (44c2be95) and
    ``render.install_menu_markdown`` (d7e0fff2), so this is the third and the
    fix is a SHAPE rather than three more patches.

    ``ok`` is False -- a probe that did not answer has not passed -- but the
    DETAIL says the check could not be made, so a tester is never told to
    install an app that may well be there. ``blocking`` follows the check's own
    name via :func:`_record`, unchanged: this says nothing new about severity.

    ``tests/mobile/test_mobile_probe_envelopes.py`` is the other half. This
    removes the duplication inside this file; the scanner is what stops a
    FOURTH site somewhere else.
    """
    body = result if isinstance(result, dict) else {}
    problem = str(body.get("error") or "")
    if not problem:
        return None
    return _record(
        name,
        False,
        "could not be checked -- the device did not answer: " + problem[:200],
        fix
        or (
            "Check the emulator is still up (`qa_list_devices`) and run the "
            "preflight again. Nothing is known about this check either way."
        ),
    )


async def check(target_package: str = "", serial: str = "") -> dict:
    """Run EVERY check and return all of them.

    ``{"error", "content": {"ok", "serial", "checks": [...], "failing": [...]}}``.
    ``error`` is reserved for this function itself failing; a failed CHECK is a
    normal result with ``ok=False``, because a refusal a tester can act on is
    not an error condition.
    """
    checks: list[dict] = []
    resolved_serial = str(serial or "")
    try:
        # 1. virtualization -----------------------------------------------
        try:
            virt = (platform_info.virtualization() or {}).get("content") or {}
            checks.append(
                _record(
                    "virtualization",
                    bool(virt.get("ok")),
                    str(virt.get("detail") or ""),
                    str(virt.get("fix") or ""),
                )
            )
        except Exception as exc:
            checks.append(_record("virtualization", False, "check failed: " + str(exc)))

        # 2. adb responds --------------------------------------------------
        serials: list[str] = []
        try:
            listed = await adb.devices()
            if listed.get("error"):
                checks.append(
                    _record(
                        "adb_responds",
                        False,
                        str(listed["error"]),
                        "Install Android Studio, or run the mobile provisioner, "
                        "then try again.",
                    )
                )
            else:
                serials = list(listed.get("content") or [])
                checks.append(
                    _record(
                        "adb_responds",
                        True,
                        "adb reports " + str(len(serials)) + " device(s)",
                    )
                )
        except Exception as exc:
            checks.append(_record("adb_responds", False, "check failed: " + str(exc)))

        # 3. adb first on PATH ---------------------------------------------
        try:
            path_state = (emulator.ensure_adb_first_on_path() or {}).get(
                "content"
            ) or {}
            shadowed = str(path_state.get("shadowed_by") or "")
            checks.append(
                _record(
                    "adb_first_on_path",
                    True,
                    (
                        "using "
                        + str(path_state.get("adb") or "adb")
                        + (
                            "; a different adb (" + shadowed + ") was earlier on "
                            "PATH and has been moved behind it for this process"
                            if shadowed
                            else ""
                        )
                    ),
                    (
                        "Two adb versions on one machine fight over the adb "
                        "server. Consider removing " + shadowed + " from PATH."
                        if shadowed
                        else ""
                    ),
                )
            )
        except Exception as exc:
            checks.append(
                _record("adb_first_on_path", False, "check failed: " + str(exc))
            )

        # 4. emulator booted -----------------------------------------------
        try:
            if not resolved_serial:
                running = (await emulator.find_running(provisioner.AVD_NAME)).get(
                    "content"
                ) or {}
                resolved_serial = str(running.get("serial") or "")
            if not resolved_serial and serials:
                # NEVER serials[0]. adb's order is not stable, and this lane
                # installs, types, taps and force-stops -- so picking the
                # first attached device can drive a tester's own phone.
                #
                # The rule has TWO halves and both are pinned, because each one
                # alone reads like a bug to whoever meets it next:
                #
                #  * AMBIGUOUS set -> an emulator is the only device this lane
                #    may choose for itself, and two of them refuse by name
                #    rather than guess (`test_a_phone_is_never_chosen_over_an_
                #    emulator`, `test_two_emulators_refuse_by_name_rather_than_
                #    guess`).
                #  * EXACTLY ONE attached device -> it is chosen, emulator or
                #    not. Nothing is ambiguous about one device, and a tester
                #    who runs the lane against a single physical handset on
                #    purpose is a supported case; refusing it would be a guard
                #    that stops the supported flow
                #    (`test_a_single_attached_device_is_still_allowed`).
                #
                # Reaching either branch at all requires a caller that resolved
                # no serial of its own; every production caller resolves one
                # first, so this is the fallback, not the usual path.
                emulators = [s for s in serials if str(s).startswith("emulator-")]
                if len(emulators) == 1:
                    resolved_serial = emulators[0]
                elif len(serials) == 1:
                    resolved_serial = serials[0]
                else:
                    ambiguous = ", ".join(str(s) for s in serials[:6])
                    checks.append(
                        _record(
                            "device_choice",
                            False,
                            "more than one device is attached and none could be "
                            "chosen safely: " + ambiguous,
                            "Name the device explicitly, because this lane "
                            "installs apps and taps the screen and must never "
                            "guess which one. Pass the emulator's serial, or "
                            "detach the others.",
                        )
                    )
            if not resolved_serial:
                checks.append(
                    _record(
                        "emulator_booted",
                        False,
                        "no running emulator or device was found",
                        "Start the emulator from the mobile lane (or from "
                        "Android Studio) and try again.",
                    )
                )
            else:
                prop = await adb.getprop(resolved_serial, emulator.BOOT_PROP)
                booted = (
                    not prop.get("error")
                    and str(prop.get("content") or "").strip() == "1"
                )
                checks.append(
                    _record(
                        "emulator_booted",
                        booted,
                        resolved_serial
                        + ": "
                        + emulator.BOOT_PROP
                        + "="
                        + str(prop.get("content") or prop.get("error") or "?"),
                        (
                            ""
                            if booted
                            else "The device is visible but still booting. Wait "
                            "for the launcher to appear, then retry."
                        ),
                    )
                )
        except Exception as exc:
            checks.append(
                _record("emulator_booted", False, "check failed: " + str(exc))
            )

        # 4b. the device can resolve a name ---------------------------------
        try:
            if not resolved_serial:
                checks.append(
                    _record(
                        "device_dns",
                        False,
                        "no device answered, so its resolver could not be asked",
                        "Start the emulator (or attach a device) and try again; "
                        "the checks above say which step is missing.",
                    )
                )
            else:
                # The ADB CALL carries the budget and `-W` is left to do the
                # one job it can do -- see DNS_PROBE_TIMEOUT_S for the numbers
                # that decide which is which.
                named = await adb.shell(
                    resolved_serial,
                    [
                        "ping",
                        "-c",
                        "1",
                        "-W",
                        str(DNS_PING_WAIT_S),
                        DNS_PROBE_HOST,
                    ],
                    timeout=DNS_PROBE_TIMEOUT_S,
                )
                # A probe that OUTLASTED the budget is a FINDING, not a check
                # that could not be made: adb was reachable enough to be left
                # waiting on the resolver. So it does NOT go to `_unanswered`,
                # whose "the device did not answer" is true and tells a tester
                # nothing they can act on. It falls through to the same route
                # probe as an outright failure, and only the DETAIL differs.
                overran = bool(named.get("timed_out"))
                unanswered = None if overran else _unanswered("device_dns", named)
                if unanswered is not None:
                    checks.append(unanswered)
                else:
                    body = named.get("content") or {}
                    resolved = not named.get("error") and _exit_code(body) == 0
                    if resolved:
                        checks.append(
                            _record(
                                "device_dns",
                                True,
                                resolved_serial + " resolves " + DNS_PROBE_HOST,
                            )
                        )
                    else:
                        # WHICH failure this is decides the remedy, so it is
                        # measured rather than assumed: a device with no route at
                        # all is a different problem from one whose resolver is
                        # stale, and telling a tester to restart the emulator
                        # when the machine is simply offline wastes their time.
                        routed = await adb.shell(
                            resolved_serial,
                            [
                                "ping",
                                "-c",
                                "1",
                                "-W",
                                str(DNS_PING_WAIT_S),
                                DNS_PROBE_IP,
                            ],
                            timeout=DNS_PROBE_TIMEOUT_S,
                        )
                        route_body = routed.get("content") or {}
                        can_route = (
                            not routed.get("error") and _exit_code(route_body) == 0
                        )
                        facts = await adb.device_facts(resolved_serial)
                        kind = str((facts.get("content") or {}).get("kind") or "")
                        # The REMEDY follows the route probe alone -- what
                        # the tester must change is the same whether the name
                        # failed fast or hung. The DETAIL is what carries the
                        # difference, because a tester who is told "cannot
                        # resolve" about a lookup that actually took 30s will
                        # not recognise the stall they are about to sit through.
                        if not can_route:
                            fix = DNS_NO_ROUTE_FIX
                        else:
                            fix = (
                                DNS_EMULATOR_FIX
                                if kind == "emulator"
                                else DNS_DEVICE_FIX
                            )
                        if overran and can_route:
                            detail = (
                                resolved_serial
                                + " did not resolve "
                                + DNS_PROBE_HOST
                                + " within "
                                + str(DNS_PROBE_TIMEOUT_S)
                                + "s, while "
                                + DNS_PROBE_IP
                                + " answered: "
                                + DNS_SLOW_NOTE
                            )
                        elif overran:
                            detail = (
                                resolved_serial
                                + " did not resolve "
                                + DNS_PROBE_HOST
                                + " within "
                                + str(DNS_PROBE_TIMEOUT_S)
                                + "s and did not reach "
                                + DNS_PROBE_IP
                                + " either"
                            )
                        elif can_route:
                            detail = (
                                resolved_serial
                                + " pings "
                                + DNS_PROBE_IP
                                + " but cannot resolve "
                                + DNS_PROBE_HOST
                                + ": packets route and names do not"
                            )
                        else:
                            detail = (
                                resolved_serial
                                + " reached neither "
                                + DNS_PROBE_HOST
                                + " nor "
                                + DNS_PROBE_IP
                            )
                        checks.append(_record("device_dns", False, detail, fix))
        except Exception as exc:
            checks.append(_record("device_dns", False, "check failed: " + str(exc)))

        # 5. target package installed --------------------------------------
        try:
            package = str(target_package or "").strip()
            if not package:
                checks.append(
                    _record(
                        "package_installed",
                        False,
                        "no target app package was given",
                        "Tell the mobile lane which app to test: a local APK "
                        "path, a download URL, a Play Store listing, or the "
                        "package name of an app already on the emulator.",
                    )
                )
            elif not valid_package_name(package):
                checks.append(
                    _record(
                        "package_installed",
                        False,
                        repr(package[:60]) + " is not a valid Android package name",
                        "Use the app's package id, for example com.example.app.",
                    )
                )
            elif not resolved_serial:
                checks.append(
                    _record(
                        "package_installed",
                        False,
                        "cannot check " + package + " without a device",
                        "Start the emulator first.",
                    )
                )
            else:
                installed = await adb.installed_packages(resolved_serial)
                unanswered = _unanswered("package_installed", installed)
                if unanswered:
                    checks.append(unanswered)
                else:
                    present = package in (installed.get("content") or [])
                    checks.append(
                        _record(
                            "package_installed",
                            present,
                            package
                            + (" is installed" if present else " is NOT installed"),
                            (
                                ""
                                if present
                                else "Install the app first: give the mobile lane an "
                                "APK path or a download URL, or open its Play Store "
                                "listing on the emulator."
                            ),
                        )
                    )
        except Exception as exc:
            checks.append(
                _record("package_installed", False, "check failed: " + str(exc))
            )

        # 6. IME pinned (Phase 0 Part B) -----------------------------------
        pinned = False
        try:
            status = (ime.manifest_status() or {}).get("content") or {}
            pinned = bool(status.get("ok"))
            checks.append(
                _record(
                    "ime_pinned",
                    pinned,
                    str(status.get("detail") or ""),
                    str(status.get("fix") or ""),
                )
            )
        except Exception as exc:
            checks.append(_record("ime_pinned", False, "check failed: " + str(exc)))

        # 7/8/9. IME installed, selected, oracle ---------------------------
        # Each is reported even when 6 failed, with the SAME reason, because a
        # tester reading "1 of 10 failed" and a tester reading "4 of 10 failed,
        # all for one missing release" take the same action -- and hiding three
        # of them would make the count a lie.
        for name in ("ime_installed", "ime_selected", "ime_oracle"):
            if not pinned:
                checks.append(
                    _record(
                        name,
                        False,
                        "not checked: " + ime.NOT_PINNED_DETAIL,
                        ime.NOT_PINNED_FIX,
                    )
                )
        if pinned:
            manifest = (ime.manifest() or {}).get("content") or {}
            ime_id = str(manifest.get("ime_id") or "")
            try:
                if not resolved_serial:
                    checks.append(
                        _record(
                            "ime_installed",
                            False,
                            "cannot check without a device",
                            "Start the emulator first.",
                        )
                    )
                else:
                    present = (await ime.installed(resolved_serial)) or {}
                    # `ime.installed` propagates `adb.installed_packages`'s
                    # envelope verbatim (ime.py:197-198), so the evidence is
                    # here and used to be dropped: a failed probe rendered
                    # "is NOT installed" with a fix line about the next run.
                    unanswered = _unanswered("ime_installed", present)
                    if unanswered:
                        checks.append(unanswered)
                    else:
                        ok = bool((present.get("content") or {}).get("installed"))
                        checks.append(
                            _record(
                                "ime_installed",
                                ok,
                                str(manifest.get("package"))
                                + (" is installed" if ok else " is NOT installed"),
                                (
                                    ""
                                    if ok
                                    else "The mobile lane installs the QA keyboard "
                                    "itself on the next run with apply=true."
                                ),
                            )
                        )
            except Exception as exc:
                checks.append(
                    _record("ime_installed", False, "check failed: " + str(exc))
                )
            try:
                if not resolved_serial:
                    checks.append(
                        _record("ime_selected", False, "cannot check without a device")
                    )
                else:
                    current = await ime.current_ime(resolved_serial)
                    # THE SAME HOLE AS THE TWO ABOVE, found while checking the
                    # neighbour: `ime.current_ime` returns its adb envelope
                    # verbatim (ime.py:264-265), and reading only `content` made
                    # a failed probe render "active input method: (none)".
                    unanswered = _unanswered("ime_selected", current)
                    if unanswered:
                        checks.append(unanswered)
                        raise _Answered
                    active = str(current.get("content") or "")
                    # By component identity: Android reports `pkg/.Class` and
                    # the manifest pins `pkg/pkg.Class`. `==` refused every run
                    # on a correctly configured device (2026-09-04, live).
                    ok = ime.same_component(active, ime_id)
                    checks.append(
                        _record(
                            "ime_selected",
                            ok,
                            "active input method: " + (active or "(none)"),
                            (
                                ""
                                if ok
                                else "The mobile lane selects the QA keyboard "
                                "itself, and restores yours when the run ends."
                            ),
                        )
                    )
            except _Answered:
                pass
            except Exception as exc:
                checks.append(
                    _record("ime_selected", False, "check failed: " + str(exc))
                )
            try:
                if not resolved_serial:
                    checks.append(
                        _record("ime_oracle", False, "cannot check without a device")
                    )
                else:
                    probed = await ime.probe(resolved_serial)
                    if probed.get("error"):
                        checks.append(
                            _record(
                                "ime_oracle",
                                False,
                                str(probed["error"]),
                                "Reinstall the QA keyboard and select it.",
                            )
                        )
                    else:
                        body = probed["content"] or {}
                        checks.append(
                            _record(
                                "ime_oracle",
                                bool(body.get("ok")),
                                "probe result=" + str(body.get("result")),
                                (
                                    ""
                                    if body.get("ok")
                                    else "The QA keyboard did not answer a probe. "
                                    "Reinstall it and select it again."
                                ),
                            )
                        )
            except Exception as exc:
                checks.append(_record("ime_oracle", False, "check failed: " + str(exc)))

        # 10. free disk -----------------------------------------------------
        try:
            disk = (downloader.check_disk(RUN_FREE_BYTES, paths.sub("runs")) or {}).get(
                "content"
            ) or {}
            checks.append(
                _record(
                    "free_disk",
                    bool(disk.get("ok")),
                    str(disk.get("detail") or ""),
                    (
                        ""
                        if disk.get("ok")
                        else "Free some space on the volume holding "
                        + str(paths.cache_root())
                        + " and try again."
                    ),
                )
            )
        except Exception as exc:
            checks.append(_record("free_disk", False, "check failed: " + str(exc)))

        # 11. cache ownership ------------------------------------------------
        # ONE PRODUCER, ONE MEANING, EVERY CONSUMER. `paths.ownership()` has
        # produced a `checked` field since Phase 5 and NOTHING read it: both
        # consumers branch on `ok` alone, so on Windows -- where a POSIX uid
        # comparison carries no meaning, and the function therefore answers
        # `ok=True, checked=False` -- the disclosure that distinguishes "checked
        # and fine" from "not checked at all" reached no tester, while
        # `paths.ownership`'s own docstring claimed the docs repeated it. This is
        # the consumer that renders it, and the bracketed suffix is what makes
        # the two states distinguishable in the tester-facing line.
        try:
            owned = (paths.ownership() or {}).get("content") or {}
            checks.append(
                _record(
                    "cache_ownership",
                    bool(owned.get("ok")),
                    str(owned.get("detail") or "")
                    + (
                        ""
                        if bool(owned.get("checked"))
                        else " [not verified on this host]"
                    ),
                    str(owned.get("fix") or ""),
                )
            )
        except Exception as exc:
            checks.append(
                _record("cache_ownership", False, "check failed: " + str(exc))
            )

        # 12. host privileges -------------------------------------------------
        # ONE PRODUCER, ONE MEANING, EVERY CONSUMER: the same verdict the
        # qa_host_check tool and the virtualization fix text render. ADVISORY by
        # construction (see ADVISORY_CHECKS above), so `ok` cannot see it.
        # `undetermined` is reported as ok=True with the disclosure in `detail`:
        # a probe that could not read the machine is not a finding about the
        # machine, and rendering it as a failure is how a tester who CAN elevate
        # gets sent to IT.
        try:
            priv = (host_privileges.probe() or {}).get("content") or {}
            determined = not priv.get("undetermined", True)
            elevatable = bool(priv.get("elevated") or priv.get("can_elevate"))
            checks.append(
                _record(
                    "host_privileges",
                    (not determined) or elevatable,
                    str(priv.get("summary") or "no verdict")
                    + ("" if determined else " [not verified on this host]"),
                    ""
                    if elevatable
                    else "Nothing here is blocked. Prefer the admin-free "
                    "install route, and call qa_host_check for the per-step "
                    "list and the official download references.",
                )
            )
        except Exception as exc:
            checks.append(
                _record("host_privileges", False, "check failed: " + str(exc))
            )

        failing = [record["name"] for record in checks if not record["ok"]]
        # `ok` is "nothing that BLOCKS a run failed". `failing` stays every
        # failure, because the renderer still shows an advisory one with its fix
        # -- a tester should read that typing is unavailable, and then be allowed
        # to run anyway.
        blocking = [
            record["name"]
            for record in checks
            if not record["ok"] and record.get("blocking", True)
        ]
        advisory = [name for name in failing if name not in blocking]
        return {
            "error": None,
            "content": {
                "ok": not blocking,
                "blocking": blocking,
                "advisory": advisory,
                "serial": resolved_serial,
                "checks": checks,
                "failing": failing,
            },
        }
    except Exception as exc:
        logger.exception("mobile.preflight.check failed")
        return {"error": str(exc), "content": None}


def flag_state() -> dict:
    """``{enabled, fix}`` for the lane's kill-switch.

    Separate from :func:`check` on purpose: the flag gates tool REGISTRATION in
    Phase 3, so by the time a check runs it is already true. This exists so
    ``qa-doctor`` and the Phase-3 handler can say the same thing in one place.
    """
    enabled = bool(settings.qa_mobile_run_enabled)
    return {
        "error": None,
        "content": {"enabled": enabled, "fix": "" if enabled else _FLAG_FIX},
    }


def render(content: dict) -> str:
    """The checks as tester-facing markdown, failures first with their fixes."""
    lines: list[str] = []
    body = dict(content or {})
    ordered = sorted(
        body.get("checks") or [], key=lambda record: (bool(record.get("ok")),)
    )
    for record in ordered:
        mark = "✅" if record.get("ok") else "❌"
        lines.append(
            mark + " **" + str(record.get("name")) + "** — " + str(record.get("detail"))
        )
        if not record.get("ok") and record.get("fix"):
            lines.append("   ↳ " + str(record["fix"]))
    return "\n".join(lines)
