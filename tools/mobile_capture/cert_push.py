"""Write the root CA PEM to the device and confirm it landed."""

from __future__ import annotations

from tools.mobile import adb
from tools.mobile_capture import ca, cert_const
from tools.mobile_capture.cert_const import (
    REASON_PUSH_FAILED,
    REASON_VERIFY_FAILED,
)
from tools.mobile_capture.cert_marker import _rc
from tools.mobile_capture.cert_route import _no_ca


def _push_failed(detail: str) -> dict:
    return {"error": REASON_PUSH_FAILED, "content": {"detail": detail}}


async def _mkdir(serial: str, device_dir: str) -> dict | None:
    mk = await adb.shell(
        serial, ["mkdir", "-p", device_dir], timeout=cert_const.DEVICE_CALL_TIMEOUT_S
    )
    if mk.get("error") or _rc(mk.get("content") or {}) != 0:
        return _push_failed(mk.get("error") or "mkdir failed")
    return None


async def _write(serial: str, device_path: str, pem_bytes: bytes) -> dict | None:
    feed = adb.StdinFeed(pem_bytes, cert_const.DEVICE_CALL_TIMEOUT_S)
    write = await adb.shell_in(serial, ["sh", "-c", "cat > " + device_path], feed)
    if write.get("error") or _rc(write.get("content") or {}) != 0:
        detail = (
            write.get("error")
            or str((write.get("content") or {}).get("err") or "")[:200]
        )
        return _push_failed(detail)
    return None


async def _chmod(serial: str, device_path: str) -> dict | None:
    chmod = await adb.shell(
        serial,
        ["chmod", "644", device_path],
        timeout=cert_const.DEVICE_CALL_TIMEOUT_S,
    )
    if chmod.get("error") or _rc(chmod.get("content") or {}) != 0:
        return _push_failed("chmod failed on the device")
    return None


async def _verify(serial: str, device_path: str) -> dict | None:
    verify = await adb.shell(
        serial, ["ls", device_path], timeout=cert_const.DEVICE_CALL_TIMEOUT_S
    )
    vbody = (verify.get("content") or {}) if not verify.get("error") else {}
    if _rc(vbody) == 0 and device_path in str(vbody.get("out") or ""):
        return None
    return {
        "error": REASON_VERIFY_FAILED,
        "content": {
            "detail": (
                "the certificate could not be confirmed on the device after writing it"
            )
        },
    }


async def _push_pem(serial: str, device_dir: str, leaf: str) -> tuple[dict | None, str]:
    """Write the PEM to the device and confirm it landed: (refusal, path)."""
    pem = ca.read_pem()
    if pem.get("error"):
        return _no_ca(pem["error"]), ""
    pem_bytes = pem["content"]["pem"].encode("utf-8")
    device_path = device_dir + "/" + leaf

    failed = (
        await _mkdir(serial, device_dir)
        or await _write(serial, device_path, pem_bytes)
        or await _chmod(serial, device_path)
        or await _verify(serial, device_path)
    )
    return failed, device_path
