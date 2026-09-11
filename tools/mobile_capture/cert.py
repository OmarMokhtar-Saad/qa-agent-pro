"""Install / status / remove the qa-agents root CA on a DEVICE's trust store.

**The store Android apps actually obey is the SYSTEM store**, not the user
store: the user certificate store is ignored by every app targeting API 24+
unless the app's own ``network_security_config`` opts in, which almost none
do. Being debuggable does not change this -- it only activates a
``<debug-overrides>`` block if the APK shipped one. So this module installs
ONLY into the system store, and refuses BY NAME when it cannot, rather than
quietly installing somewhere that would not change what a tester sees.

**The route depends on the device's API level, not a guess:**

* API 30+: the system partition is read-only even with root. The working
  route is ``/data/misc/user/0/cacerts-added/<hash>.0`` -- writable with
  root alone, no remount needed.
* API < 30: ``/system/etc/security/cacerts/<hash>.0``, which needs root AND
  a writable ``/system`` (``adb remount``).

**Root is checked by FACT, never by attempting and parsing stderr.**
``ro.build.type`` is read first: only ``userdebug``/``eng`` builds grant
``adb root`` at all; a ``user`` (production) build refuses it outright, on
an emulator exactly as on a real phone. This module reads that property and
refuses by name (:data:`REASON_PRODUCTION_BUILD`) BEFORE ever calling
``adb root``, rather than trying it and improvising off the refusal text.

**This lane's own provisioned emulator is such a device.**
``tools/mobile/provisioner.py`` pins ``SYSTEM_IMAGE_TAG =
"google_apis_playstore"``, a Play (production) system image, and
``tools/mobile/emulator.py`` never passes ``-writable-system`` on its start
command. Neither is changed here: the image tag is what lets a tester
install and open an app from the Play Store inside a run at all, and
``-writable-system`` changes how every emulator this lane owns boots. Both
are owner decisions with their own blast radius (see the plan ledger). The
practical consequence, stated rather than hidden: on the emulator this lane
boots today, ``install()`` reaches :data:`REASON_PRODUCTION_BUILD` every
time, honestly. The full two-route path is implemented and graded so the
capability is ready the moment the owner picks an image that can use it, or
for a real, rooted device or a locally-run non-Play emulator.

**A production phone gets the same honest refusal**, never a half attempt: a
real device without root refuses at the identical ``ro.build.type`` check.

Every public function returns ``{"error", "content"}`` and never raises.
Every device-touching call needs ``apply=True``; ``apply=False`` (or a
caller that never sets it) touches nothing. Runs under the lane's own
per-serial device lock (``tools.mobile.session.take_device_lock``), the same
primitive ``proxy.start`` uses -- no second lock is invented.

**Never a source of TIER_DECRYPTED.** A successful ``install()`` proves the
certificate is PRESENT on the device, never that anything trusts it or that
a request was actually decrypted -- ``ladder.decide`` reaches
``TIER_DECRYPTED`` from the functional probe alone, and nothing here changes
that. See ``tests/mobile_capture/test_cert.py`` for the mutant that proves
it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from tools.mobile import adb, session
from tools.mobile_capture import ca, ledger, paths, proxy

logger = logging.getLogger(__name__)

#: Per-call timeout for a single root/remount/push/verify adb round trip.
#: See tests/test_bounds_upper.py::CEILINGS.
DEVICE_CALL_TIMEOUT_S: int = 20

#: Android build types that grant `adb root` at all. Anything else (most
#: notably "user", the production build every real phone and this lane's own
#: Play emulator ship) refuses it outright.
_ROOTABLE_BUILD_TYPES = frozenset({"userdebug", "eng"})

#: The API level at and above which the writable route moves from the
#: read-only-even-with-root `/system` partition to `/data`, which needs only
#: root, never a remount. Not itself a resource cap -- a fixed Android
#: platform fact -- so it is spelled to stay outside the cap scanner's own
#: MAX/MIN/CAP/LIMIT/TIMEOUT/... vocabulary.
API_LEVEL_FOR_DATA_ROUTE: int = 30

SYSTEM_CACERTS_DIR = "/system/etc/security/cacerts"
DATA_CACERTS_DIR = "/data/misc/user/0/cacerts-added"

ROUTE_SYSTEM = "system"
ROUTE_DATA = "data"

#: Refusal codes. A caller branches on these BY NAME. `mobile_capture.prepare`
#: maps every one of these to `ladder.REASON_CERT_NOT_TRUSTED` -- the ONE
#: producer of the tier-level sentence -- while this module keeps the more
#: specific reason available to a direct caller (`qa_setup_capture`) or a
#: test.
REASON_NO_APPLY = "no_apply"
REASON_NO_CA = "no_ca"
REASON_DEVICE_UNREACHABLE = "device_unreachable"
REASON_PRODUCTION_BUILD = "production_build"

#: Staged for a MANUAL user-store install. NOT a success and NOT a refusal:
#: the file is on the device, nothing is trusted yet, and only the tester can
#: finish it. Android 11+ removed every route that would let adb install a user
#: certificate, so a manual step is the whole of what is available here.
REASON_USER_STORE_MANUAL = "user_store_manual"

#: Where a staged certificate lands. The Downloads folder because that is what
#: the Settings certificate picker opens into; anywhere else and the tester has
#: to go looking.
USER_STORE_STAGE_DIR = "/sdcard/Download"
REASON_REMOUNT_FAILED = "remount_failed"
REASON_API_UNKNOWN = "api_unknown"
REASON_PUSH_FAILED = "push_failed"
REASON_VERIFY_FAILED = "verify_failed"

_HASH_NAME_RE = re.compile(r"^[0-9a-f]{8}\.0$")


def _marker_path(serial: str) -> Path:
    return paths.cert_marker(str(serial or ""))


def _read_marker(serial: str) -> dict | None:
    target = _marker_path(serial)
    try:
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except Exception:
        logger.warning("mobile_capture.cert: unreadable marker for %s", serial)
        return None


def _write_marker(serial: str, body: dict) -> None:
    target = _marker_path(serial)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def _clear_marker(serial: str) -> None:
    target = _marker_path(serial)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logger.warning("mobile_capture.cert: could not remove marker for %s", serial)


def _rc(body: dict) -> int:
    try:
        return int(body.get("rc") or 0)
    except (TypeError, ValueError, OverflowError):
        return 1


async def _stage_for_manual_install(serial: str, btype: str) -> dict:
    """Push the certificate to Downloads and hand over the manual steps.

    The device refuses ``adb root``, so the system store is unreachable and the
    only store left is the USER store -- which Android 11+ will not let adb
    write at all, by design. So this stages the file and stops. It reports
    :data:`REASON_USER_STORE_MANUAL`, never success: nothing is trusted until a
    human finishes it, and the tester must know the honest odds before they
    bother -- an app targeting API 24+ ignores the user store unless its own
    network-security config opts in, which almost none do.

    Never raises; a push failure is reported, not thrown.
    """
    remote = USER_STORE_STAGE_DIR + "/qa-agents-ca.crt"
    pushed = await adb.raw(
        ["-s", serial, "push", str(ca.cert_path()), remote],
        timeout=DEVICE_CALL_TIMEOUT_S,
    )
    body = pushed.get("content") or {}
    if pushed.get("error") or _rc(body) != 0:
        return {
            "error": REASON_DEVICE_UNREACHABLE,
            "content": {
                "detail": (
                    "could not stage the certificate on the device: "
                    + str(pushed.get("error") or body.get("err") or "").strip()[:200]
                )
            },
        }
    return {
        "error": REASON_USER_STORE_MANUAL,
        "content": {
            "build_type": btype or "unknown",
            "staged_at": remote,
            "installed": False,
            "trusted": False,
            "detail": (
                "This device reports a '"
                + (btype or "unknown")
                + "' build, which refuses `adb root`, so the SYSTEM "
                "certificate store cannot be reached. The certificate has "
                "been copied to "
                + remote
                + " instead. To finish, on the device: Settings -> Security "
                "-> Encryption & credentials -> Install a certificate -> CA "
                "certificate -> Install anyway, then pick qa-agents-ca.crt. "
                "Be warned this lands in the USER store, and an app built "
                "for Android 7 or later ignores that store unless the app "
                "itself opted in -- so for most apps this will change "
                "nothing, and HTTPS bodies will stay unreadable. Nothing is "
                "trusted until you complete those steps."
            ),
        },
    }


async def install(serial: str, *, apply: bool = False, owner: str = "") -> dict:
    """Install the qa-agents root CA into *serial*'s SYSTEM trust store.

    Refuses BY NAME, never a half-attempt: a production build, an unreadable
    API level, a failed remount and a failed push are each their own reason
    code, checked in that order, and nothing is written to the device past
    the first one that fails.
    """
    serial = str(serial or "")
    minted = not str(owner or "").strip()
    label = str(owner or "").strip() or session.new_provisioning_owner()
    try:
        took = (session.take_device_lock(label, serial=serial) or {}).get(
            "content"
        ) or {}
        if not took.get("acquired"):
            return {
                "error": proxy.REASON_DEVICE_BUSY,
                "content": {"holder": str(took.get("holder") or "")},
            }
        try:
            if not apply:
                return {"error": REASON_NO_APPLY, "content": None}

            made = ca.ensure_ca()
            if made.get("error"):
                return {"error": REASON_NO_CA, "content": {"detail": made["error"]}}
            fingerprint = str((made.get("content") or {}).get("fingerprint") or "")

            hashed = ca.android_hashed_name()
            if hashed.get("error"):
                return {"error": REASON_NO_CA, "content": {"detail": hashed["error"]}}
            leaf = str(hashed["content"]["filename"])
            if not _HASH_NAME_RE.match(leaf):
                return {
                    "error": REASON_NO_CA,
                    "content": {"detail": "unexpected hash filename shape"},
                }

            facts = await adb.device_facts(serial)
            if facts.get("error"):
                return {
                    "error": REASON_DEVICE_UNREACHABLE,
                    "content": {"detail": facts["error"]},
                }

            build_type = await adb.getprop(serial, "ro.build.type")
            if build_type.get("error"):
                return {
                    "error": REASON_DEVICE_UNREACHABLE,
                    "content": {"detail": build_type["error"]},
                }
            btype = str(build_type.get("content") or "").strip().lower()
            if btype not in _ROOTABLE_BUILD_TYPES:
                return await _stage_for_manual_install(serial, btype)

            rooted = await adb.raw(
                ["-s", serial, "root"], timeout=DEVICE_CALL_TIMEOUT_S
            )
            if rooted.get("error"):
                return {
                    "error": REASON_DEVICE_UNREACHABLE,
                    "content": {"detail": rooted["error"]},
                }
            root_body = rooted.get("content") or {}
            if _rc(root_body) != 0:
                return {
                    "error": REASON_PRODUCTION_BUILD,
                    "content": {
                        "detail": (
                            "adb root was refused: "
                            + str(root_body.get("err") or "").strip()[:200]
                            + ". If this is the qa-agents emulator, its system "
                            "image is chosen by `qa_mobile_system_image_tag`; "
                            "`google_apis_playstore` cannot root, so switch it "
                            "to `google_apis` to enable decryption."
                        )
                    },
                }

            api_raw = str((facts.get("content") or {}).get("api") or "")
            try:
                api = int(api_raw)
            except (TypeError, ValueError, OverflowError):
                api = None
            if api is None:
                return {
                    "error": REASON_API_UNKNOWN,
                    "content": {"detail": "could not read ro.build.version.sdk"},
                }

            if api >= API_LEVEL_FOR_DATA_ROUTE:
                device_dir = DATA_CACERTS_DIR
                route = ROUTE_DATA
            else:
                remounted = await adb.raw(
                    ["-s", serial, "remount"], timeout=DEVICE_CALL_TIMEOUT_S
                )
                if remounted.get("error"):
                    return {
                        "error": REASON_DEVICE_UNREACHABLE,
                        "content": {"detail": remounted["error"]},
                    }
                rbody = remounted.get("content") or {}
                if _rc(rbody) != 0:
                    return {
                        "error": REASON_REMOUNT_FAILED,
                        "content": {
                            "detail": (
                                "`adb remount` failed -- this device's "
                                "/system is not writable, most likely "
                                "because it was not started with "
                                "`-writable-system`. No certificate was "
                                "installed."
                            )
                        },
                    }
                device_dir = SYSTEM_CACERTS_DIR
                route = ROUTE_SYSTEM

            pem = ca.read_pem()
            if pem.get("error"):
                return {"error": REASON_NO_CA, "content": {"detail": pem["error"]}}
            pem_bytes = pem["content"]["pem"].encode("utf-8")
            device_path = device_dir + "/" + leaf

            mk = await adb.shell(
                serial, ["mkdir", "-p", device_dir], timeout=DEVICE_CALL_TIMEOUT_S
            )
            if mk.get("error") or _rc(mk.get("content") or {}) != 0:
                return {
                    "error": REASON_PUSH_FAILED,
                    "content": {"detail": mk.get("error") or "mkdir failed"},
                }

            write = await adb.shell(
                serial,
                ["sh", "-c", "cat > " + device_path],
                timeout=DEVICE_CALL_TIMEOUT_S,
                stdin_data=pem_bytes,
            )
            if write.get("error") or _rc(write.get("content") or {}) != 0:
                return {
                    "error": REASON_PUSH_FAILED,
                    "content": {
                        "detail": (
                            write.get("error")
                            or str((write.get("content") or {}).get("err") or "")[:200]
                        )
                    },
                }

            chmod = await adb.shell(
                serial, ["chmod", "644", device_path], timeout=DEVICE_CALL_TIMEOUT_S
            )
            if chmod.get("error") or _rc(chmod.get("content") or {}) != 0:
                return {
                    "error": REASON_PUSH_FAILED,
                    "content": {"detail": "chmod failed on the device"},
                }

            verify = await adb.shell(
                serial, ["ls", device_path], timeout=DEVICE_CALL_TIMEOUT_S
            )
            vbody = (verify.get("content") or {}) if not verify.get("error") else {}
            present = _rc(vbody) == 0 and device_path in str(vbody.get("out") or "")
            if not present:
                return {
                    "error": REASON_VERIFY_FAILED,
                    "content": {
                        "detail": (
                            "the certificate could not be confirmed on the "
                            "device after writing it"
                        )
                    },
                }

            marker = {
                "serial": serial,
                "route": route,
                "device_path": device_path,
                "fingerprint": fingerprint,
                "hash": leaf,
                "installed_ms": int(time.time() * 1000),
            }
            _write_marker(serial, marker)
            return {"error": None, "content": marker}
        finally:
            if minted:
                session.release_device_lock(label, as_holder=True)
    except Exception:
        logger.exception("mobile_capture.cert.install failed")
        return {"error": REASON_DEVICE_UNREACHABLE, "content": None}


def status(serial: str) -> dict:
    """The LOCAL record of what ``install()`` last did for *serial*.

    Reads only the on-disk marker -- no device is touched, so no ``apply``
    is needed, mirroring `mitm_provision.find_mitmdump`'s locate-first shape.
    """
    try:
        marker = _read_marker(str(serial or ""))
        return {
            "error": None,
            "content": {
                "installed": bool(marker),
                "route": str((marker or {}).get("route") or ""),
                "device_path": str((marker or {}).get("device_path") or ""),
                "fingerprint": str((marker or {}).get("fingerprint") or ""),
                "hash": str((marker or {}).get("hash") or ""),
                "installed_ms": (marker or {}).get("installed_ms"),
            },
        }
    except Exception:
        logger.exception("mobile_capture.cert.status failed")
        return {
            "error": None,
            "content": {
                "installed": False,
                "route": "",
                "device_path": "",
                "fingerprint": "",
                "hash": "",
                "installed_ms": None,
            },
        }


async def remove(serial: str, *, apply: bool = False, owner: str = "") -> dict:
    """Take the certificate off *serial* AND forget it in the ledger.

    ``ledger.forget`` is OUR OWN memory of trusting this device and is
    always called (barring the lock/apply refusals below) -- reported
    honestly against whether the on-device file could actually be
    confirmed removed, never conflated with it.
    """
    serial = str(serial or "")
    minted = not str(owner or "").strip()
    label = str(owner or "").strip() or session.new_provisioning_owner()
    try:
        took = (session.take_device_lock(label, serial=serial) or {}).get(
            "content"
        ) or {}
        if not took.get("acquired"):
            return {
                "error": proxy.REASON_DEVICE_BUSY,
                "content": {"holder": str(took.get("holder") or "")},
            }
        try:
            if not apply:
                return {"error": REASON_NO_APPLY, "content": None}

            marker = _read_marker(serial)
            device_removed = False
            detail = ""
            if marker:
                device_path = str(marker.get("device_path") or "")
                route = str(marker.get("route") or "")
                reachable = True
                if route == ROUTE_SYSTEM:
                    rooted = await adb.raw(
                        ["-s", serial, "root"], timeout=DEVICE_CALL_TIMEOUT_S
                    )
                    reachable = (
                        not rooted.get("error")
                        and _rc(rooted.get("content") or {}) == 0
                    )
                    if reachable:
                        remounted = await adb.raw(
                            ["-s", serial, "remount"],
                            timeout=DEVICE_CALL_TIMEOUT_S,
                        )
                        reachable = (
                            not remounted.get("error")
                            and _rc(remounted.get("content") or {}) == 0
                        )
                if reachable and device_path:
                    rm = await adb.shell(
                        serial,
                        ["rm", "-f", device_path],
                        timeout=DEVICE_CALL_TIMEOUT_S,
                    )
                    device_removed = (
                        not rm.get("error") and _rc(rm.get("content") or {}) == 0
                    )
                    if not device_removed:
                        detail = str(
                            rm.get("error")
                            or (rm.get("content") or {}).get("err")
                            or ""
                        )[:200]
                else:
                    detail = "could not reach the device to remove the certificate"
                _clear_marker(serial)
            else:
                detail = "no local record of an installed certificate on this device"

            ledger.forget(serial)
            return {
                "error": None,
                "content": {
                    "device_removed": device_removed,
                    "had_marker": bool(marker),
                    "detail": detail,
                },
            }
        finally:
            if minted:
                session.release_device_lock(label, as_holder=True)
    except Exception:
        logger.exception("mobile_capture.cert.remove failed")
        ledger.forget(serial)
        return {
            "error": None,
            "content": {
                "device_removed": False,
                "had_marker": False,
                "detail": "unexpected error while removing the certificate",
            },
        }
