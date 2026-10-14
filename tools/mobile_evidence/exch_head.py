"""The one-line head of an exchange row: verb, path, extra tags, duration and status badge."""

from __future__ import annotations

import re

from tools.mobile_evidence.exch_num import dur_ms
from tools.mobile_shared.scrub import e


def host_of(x: dict) -> str:
    """Origin of one exchange, or '' when the record has no full URL."""
    match = re.match(r"^(https?://[^/]+)", str(x.get("url") or ""))
    return match.group(1) if match else ""


def _ex_dur_html(x: dict) -> str:
    """The duration tag of one call, or '' when it has no duration."""
    dur = x.get("durationMs")
    return '<span class="exdur">' + dur_ms(dur) + "</span>" if dur is not None else ""


def _ex_path_html(x: dict, verb: str) -> str:
    """The verb badge and the host + path of one call."""
    host = host_of(x)
    host_html = '<span class="exhost">' + e(host) + "</span>" if host else ""
    return (
        '<span class="verb v-' + e(verb.lower()) + '">' + e(verb) + "</span>"
        '<span class="path">' + host_html + e(x.get("path", "")) + "</span>"
    )


# (one-line head, badge tone, badge text) for one call. ``ok`` may be None -- a call the
# capture never resolved either way -- and folding that into "bad" would invent a failure
# exactly as folding it into "ok" would invent a success. Unknown gets its own tone.
def _ex_head(x: dict) -> tuple[str, str, str]:
    """(one-line head, badge tone, badge text) for one call."""
    status = x.get("status", -1)
    ok = x["ok"] if "ok" in x else (status is not None and 200 <= status < 300)
    shown = x.get("statusLabel") or (
        status if status not in (-1, None) else "no response"
    )
    scls = "gap" if ok is None else "ok" if ok else "bad"
    verb = str(x.get("method") or "?").upper()
    head = (
        _ex_path_html(x, verb)
        + str(x.get("extra") or "")
        + _ex_dur_html(x)
        + '<span class="badge '
        + scls
        + '">'
        + e(str(shown))
        + "</span>"
    )
    return head, scls, str(shown)
