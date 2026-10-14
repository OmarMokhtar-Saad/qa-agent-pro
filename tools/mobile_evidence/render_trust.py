"""The trust block: the recorder's account of how far its capture can be believed."""

from __future__ import annotations

from tools.mobile_evidence import render_esc as esc
from tools.mobile_evidence.render_block import SecHead, sec_block
from tools.mobile_evidence.render_html import check, pill
from tools.mobile_evidence.render_trust_rows import add_join_rows, add_provenance_rows
from tools.mobile_evidence.render_view import content_of, has_evidence


def trust_block(loaded: object) -> str:
    """Can this capture be trusted: the recorder's account of itself."""
    content = content_of(loaded)
    if not has_evidence(loaded):
        return ""
    integrity = content.get("integrity") or {}
    rows: list = []
    _add_env_row(rows, integrity)
    _add_return_rows(rows, integrity)
    _add_load_rows(rows, content)
    add_provenance_rows(rows, content, integrity)
    add_join_rows(rows, integrity)
    return sec_block(
        "Can this capture be trusted",
        '<ul class="checks">' + "".join(rows) + "</ul>",
        SecHead(
            total=len(rows),
            note="the recorder’s account of itself — none of this is about the app under test",
        ),
    )


def _env_disagree_row(integrity: dict) -> str:
    return check(
        pill("p-def", "env"),
        "the manifest and the traffic disagree",
        "the manifest says "
        + str(integrity.get("envClaimed"))
        + "; the traffic went to "
        + str(integrity.get("envFromTraffic"))
        + ". The traffic is the authority.",
    )


def _add_env_row(rows: list, integrity: dict) -> None:
    """Append the manifest-versus-traffic backend row, if either names a backend."""
    if integrity.get("envDisagrees"):
        rows.append(_env_disagree_row(integrity))
    elif integrity.get("envFromTraffic") or integrity.get("envClaimed"):
        env = str(integrity.get("envFromTraffic") or integrity.get("envClaimed"))
        rows.append(check(pill("p-ok", "env"), "manifest and traffic agree", env))


def _never_row(never: list) -> str:
    return check(
        pill("p-ok" if not never else "p-gap", "calls"),
        "requests that never came back",
        "none — every request has a logged response"
        if not never
        else str(len(never))
        + ": "
        + ", ".join(str(b.get("binding") or "?") for b in never[:6]),
    )


def _add_return_rows(rows: list, integrity: dict) -> None:
    """Append the unanswered-request row and the unparsed-line row."""
    rows.append(_never_row(integrity.get("neverReturned") or []))
    malformed = int(integrity.get("malformed") or 0)
    rows.append(
        check(
            pill("p-ok" if not malformed else "p-gap", "lines"),
            "lines the reader could not parse",
            str(malformed),
        )
    )


def _capped_row(content: dict) -> str:
    skipped = list(content.get("skipped_slices") or [])
    return check(
        pill("p-gap", "load"),
        "evidence load capped at " + str(esc.MAX_LOAD_BYTES) + " bytes",
        str(len(skipped))
        + " slice(s) not read: "
        + ", ".join(skipped[:6])
        + (" …" if len(skipped) > 6 else ""),
    )


def _streams_row(disabled: list) -> str:
    return check(
        pill("p-gap", "streams"),
        "streams this profile does not describe",
        "; ".join(
            str(d[0]) + " (" + str(d[1]) + ")"
            for d in disabled[:8]
            if isinstance(d, (list, tuple)) and len(d) == 2
        ),
    )


def _add_load_rows(rows: list, content: dict) -> None:
    """Append the capped-load row and the undescribed-streams row."""
    if content.get("truncated_load"):
        rows.append(_capped_row(content))
    disabled = content.get("disabled") or []
    if disabled:
        rows.append(_streams_row(disabled))
