"""The per-run buckets of the work the app did outside every turn."""

from __future__ import annotations


def _entry_key(verb, url):
    base = (url or "").split("?", 1)[0].rstrip("/")
    scheme, sep, rest = base.partition("://")
    if sep:
        host, slash, tail = rest.partition("/")
        base = scheme.lower() + "://" + host.lower() + slash + tail
    return ((verb or "").upper(), base)


def _slot_counts(r):
    """The five per-run counts a slot opens with, in their report order."""
    keys = ("turns", "llm", "bindings", "tools", "errors")
    return {key: r.get(key) or 0 for key in keys}


def _session_slot(r, manifests):
    """The empty per-run bucket of :meth:`Evidence.session`, with its manifest."""
    run = r.get("appRunId")
    rec = {
        "appRunId": run,
        "runIndex": r.get("runIndex"),
        "from": r.get("firstTs"),
        "to": r.get("lastTs"),
        **_slot_counts(r),
        "manifest": None,
        "entry": [],
        "failed": [],
        "other": [],
    }
    for m in manifests or []:
        if m.get("appRunId") == run:
            rec["manifest"] = m
    return rec


def _net_log_call(slot, x):
    """A net-lane runlog line no binding drew, as a ``network log only`` call."""
    outcome = (x.get("outcome") or "").upper()
    ok = True if outcome == "SUCCESS" else (False if outcome == "ERROR" else None)
    key = _entry_key(x.get("verb"), x.get("url"))
    drawn = {
        _entry_key(b.get("verb"), b.get("url"))
        for b in slot["entry"]
        if b.get("binding") != "network log only"
    }
    if key in drawn:
        return
    call = dict(x, binding="network log only", ok=ok, statusKnown=False)
    slot["entry"].append(call)
    if ok is False:
        slot["failed"].append(call)


def _file_outside_turn(slot, stream, x):
    """Put one stamped record that belongs to no turn into its run's bucket."""
    if stream == "bindings":
        slot["entry"].append(x)
        if x.get("ok") is False:
            slot["failed"].append(x)
    elif stream == "runlog" and x.get("lane") == "net" and x.get("url"):
        _net_log_call(slot, x)
    elif stream == "errors":
        if x.get("kind") != "binding":
            slot["failed"].append(x)
    else:
        slot["other"].append(x)
