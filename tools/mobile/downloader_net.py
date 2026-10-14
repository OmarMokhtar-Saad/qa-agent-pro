"""The network side of the downloader: the host allowlist, redirect guard and openers."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from config.settings_guard import never_raise
from tools.mobile import ime_pin
from tools.mobile.downloader_host import host
from tools.mobile.ime_pin import valid_sha256

#: Hosts a mobile download may come FROM and be redirected TO. Suffix matched:
#: an entry matches that host exactly, or any sub-domain of it.
#:
#: ``github.com`` / ``githubusercontent.com`` are LOAD BEARING, not a courtesy:
#: the QA IME is a GitHub release asset and a release download answers 302 to
#: ``objects.githubusercontent.com``, so an allowlist of only Google's and
#: Adoptium's hosts would refuse the one download this lane can make today.
#: Removing them turns
#: ``test_the_github_release_chain_the_ime_really_uses_is_allowed`` red, which is
#: the intended alarm rather than a puzzle.
ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    "dl.google.com",
    "adoptium.net",
    "github.com",
    "githubusercontent.com",
    # tools/mobile_capture/mitm_provision.py -- the canonical mitmproxy
    # distribution domain, independent of the github.com/githubusercontent.com
    # entries above (added for the unrelated QA IME asset).
    "mitmproxy.org",
)


class RedirectRefused(ValueError):
    """A redirect hop left HTTPS or left the allowlist. Raised PER HOP."""


def host_allowed(host: object) -> bool:
    """True when *host* is in :data:`ALLOWED_HOST_SUFFIXES` or below one.

    Sub-domain matching is ``endswith("." + suffix)`` and never
    ``endswith(suffix)``: the second accepts ``evilgithub.com``, which is the
    classic allowlist bypass and the reason this is a function rather than a
    one-line comprehension at the call site.
    """
    name = str(host or "").strip().lower().rstrip(".")
    if not name:
        return False
    for suffix in ALLOWED_HOST_SUFFIXES:
        if name == suffix or name.endswith("." + suffix):
            return True
    return False


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Validates EVERY redirect hop before urllib follows it.

    The check this joins reads ``response.url`` AFTER the whole chain has been
    followed, so a chain that passed through ``http://evil`` and ENDED on an
    allowed HTTPS host satisfied it -- by which point the bytes had already
    travelled over the hostile hop. ``redirect_request`` is the one method
    urllib calls for each 30x, so a refusal here is a refusal per hop.
    """

    # Refuse the hop, or hand it to the base implementation. *hop* is urllib's
    # ``(req, fp, code, msg, headers, newurl)``.
    def redirect_request(self, *hop):
        parts = urlsplit(str(hop[5] or ""))
        if parts.scheme != "https":
            raise RedirectRefused(
                "Refusing to follow a redirect off HTTPS (hop to "
                + (parts.scheme or "an empty scheme")
                + "://"
                + (parts.hostname or "an empty host")
                + "). Nothing was written."
            )
        if not host_allowed(parts.hostname):
            raise RedirectRefused(
                "Refusing to follow a redirect to "
                + (parts.hostname or "an empty host")
                + ": the mobile lane downloads only from "
                + ", ".join(ALLOWED_HOST_SUFFIXES)
                + ". Nothing was written."
            )
        return super().redirect_request(*hop)


def open_url(url: str, start: int):
    """Open *url* with every redirect hop validated by :class:`_GuardedRedirects`.

    An OPENER rather than ``urlopen``: ``urlopen`` uses the default handler
    chain, which follows redirects without asking anybody, and offers no hook to
    inspect a hop. The origin's scheme and host are checked in :func:`download`
    before this is called, so nothing here ever opens an unvalidated URL.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "qa-agents-mobile"})
    if start > 0:
        request.add_header("Range", "bytes=" + str(start) + "-")
    opener = urllib.request.build_opener(_GuardedRedirects())
    return opener.open(request, timeout=host().SOCKET_TIMEOUT_S)


# True only for the pinned QA keyboard: its exact asset URL AND its SHA.
#
# The ONE fetch that runs with the kill-switch unset -- the keyboard is on
# by default, with no flag. Both values must EQUAL what ``ime.manifest()``
# pins; never a prefix, a host or a pattern, so every other URL still needs
# the flag. The fetch itself verifies the bytes against that same SHA.
# Never raises.
def _is_pinned_ime(url: object, sha256: object) -> bool:
    with never_raise():
        pinned = ime_pin.manifest()
        if pinned.get("error"):
            return False
        info = pinned.get("content") or {}
        want_url = str(info.get("url") or "")
        want_sha = str(info.get("sha256") or "").lower()
        if not (want_url and want_sha):
            return False
        return str(url) == want_url and str(sha256).lower() == want_sha
    return False


def _scheme_refusal(parts: SplitResult) -> str:
    """The refusal for a URL that is not an allowlisted HTTPS one, or ``""``."""
    if parts.scheme != "https":
        return (
            "Refusing to download over "
            + (parts.scheme or "an empty scheme")
            + ": the mobile lane fetches over HTTPS only."
        )
    if not parts.hostname:
        return "Refusing to download from a URL with no host."
    if not host_allowed(parts.hostname):
        return (
            "Refusing to download from "
            + str(parts.hostname)
            + ": the mobile lane downloads only from "
            + ", ".join(ALLOWED_HOST_SUFFIXES)
            + ". Nothing was fetched."
        )
    return ""


def _sha_refusal(parts: SplitResult, sha256: str) -> str:
    """The refusal for a download with no valid pinned SHA-256, or ``""``."""
    if valid_sha256(sha256):
        return ""
    name = (parts.path or "").rsplit("/", 1)[-1] or "the requested file"
    return (
        "Refusing to download "
        + name
        + ": no valid SHA-256 is pinned for it (got "
        + repr(str(sha256 or "")[:16])
        + "). An unverifiable download is not attempted."
    )


def url_refusal(url: str, sha256: str) -> str:
    """The reason *url* may not be fetched (scheme, host, pin), or ``""``."""
    parts = urlsplit(str(url))
    return _scheme_refusal(parts) or _sha_refusal(parts, sha256)


def _open_resuming(url: str, part: Path, start: int):
    """Open *url* from byte *start*; restart from 0 if the server won't resume.

    Returns ``(response, start)`` with the offset actually in force.
    """
    try:
        return host().open_url(url, start), start
    except urllib.error.HTTPError as exc:
        if start and exc.code in (400, 416, 501):
            # The server will not resume. Start over rather than fail: a
            # stale .part from an aborted run must not wedge the lane.
            part.unlink(missing_ok=True)
            return host().open_url(url, 0), 0
        raise
