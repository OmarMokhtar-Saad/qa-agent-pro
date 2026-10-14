"""The emulator console, proxied by adb: ``emu`` and its verdict helpers (moved from adb.py).

THE ONE SURFACE that can record a device's traffic without root: measured on
emulator-5554 on 2026-09-09, ``adb root`` is refused on a production build,
``tcpdump`` is not on the system image and a release APK refuses ``run-as``.
The emulator console CAN, because it runs in the emulator process on the host
-- and ``adb ... emu <cmd>`` reaches it, carrying the console auth token
itself, so nothing in this tree reads, holds or logs that credential.
"""

from __future__ import annotations

from tools.mobile.adb_host import host


def valid_capture_name(name: object) -> bool:
    """True for a name the emulator console accepts as a bare file name.

    Checked HERE, before adb is called, even though the console checks it too:
    a name built from a run id must never be able to become a path this process
    asks a co-process to write, and the console's own refusal EXITS ZERO.
    """
    text = str(name or "")
    return bool(host()._BARE_PCAP_RE.match(text)) and ".." not in text


# ``{"error", "content": [<reply line>, ...]}`` -- the reply with its trailing
# ``OK`` stripped, so a caller reads what the console SAID and not its
# punctuation.
#
# **THE EXIT CODE IS ALWAYS ZERO**, and this is the whole reason the function
# exists rather than a bare ``raw`` call at each site. Measured on
# emulator-5554 on 2026-09-09: ``emu network capture start ../x.pcap`` answers
# ``KO: <file> must be a bare filename ...`` and STILL exits 0. So the verdict
# is read from the TEXT and from nothing else -- a reply whose last non-empty
# line is exactly ``OK`` succeeded; any line beginning ``KO`` is a failure
# carrying its own reason. Branching on ``rc`` would report every refusal as a
# success, which is the one failure shape a capture must not have: a run that
# believes it is recording and is not.
#
# A reply cut by :data:`MAX_DUMP_BYTES` is an ERROR, not a truncated success.
# The terminator is the only evidence the command worked, and a reply whose
# end was cut cannot carry it.
#
# **The device must be an EMULATOR, and this is the ONE place that question is
# asked on this path.** ``device_facts`` confirms it against ``ro.kernel.qemu``
# rather than trusting the serial prefix, because an emulator reached over TCP
# arrives as ``host:port``; a second check at a caller would be a second
# derivation of one question, and mirrored conditions drift.
async def emu(serial: str, *args: object) -> dict:
    """``adb -s <serial> emu <args>``: the emulator console. Never raises."""
    words = [str(word) for word in args]
    refusal = host()._emu_words_refusal(words)
    if refusal is not None:
        return {"error": refusal, "content": None}
    facts = await host().device_facts(serial)
    kind = str(((facts or {}).get("content") or {}).get("kind") or "")
    if kind != "emulator":
        return {"error": host().NOT_AN_EMULATOR, "content": None}
    result = await host().device(serial, ["emu"] + words)
    if result.get("error"):
        return result
    body = result["content"] or {}
    joined = str(body.get("out") or "")
    stderr = str(body.get("err") or "").strip()
    if stderr:
        joined = joined + "\n" + stderr
    return host()._emu_reply(words, joined)


def _emu_words_refusal(words: list[str]) -> str | None:
    """Why the console command line is refused, or ``None`` when it is fine."""
    if not words:
        return "Refusing an empty emulator console command."
    for word in words:
        if not host()._EMU_WORD_RE.match(word):
            return "Refusing emulator console argument " + repr(word[:40]) + "."
    return None


def _emu_error(message: str) -> dict:
    return {"error": message, "content": None}


def _too_long(words: list[str]) -> str:
    return (
        "The emulator console answered with more than "
        + str(host().MAX_DUMP_BYTES)
        + " bytes, so its reply could not be read to the end and `"
        + " ".join(words)
        + "` cannot be reported as having worked."
    )


def _emu_reply(words: list[str], joined: str) -> dict:
    """Read the console's verdict from its TEXT (the exit code is always zero)."""
    text, truncated = host()._capped(joined, host().MAX_DUMP_BYTES)
    if truncated:
        return _emu_error(_too_long(words))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line == "KO" or line.startswith("KO:"):
            said = line[3:].strip()
            return _emu_error("The emulator refused `" + " ".join(words) + "`: " + said)
    if not lines or lines[-1] != "OK":
        return _emu_error(
            "The emulator console did not confirm `"
            + " ".join(words)
            + "`; its reply did not end in OK."
        )
    return {"error": None, "content": lines[:-1]}
