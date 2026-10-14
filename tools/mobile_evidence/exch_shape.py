"""Body-policy shapes and URL paths: the two readers every row builder shares."""

from __future__ import annotations

# ── the row shapes ─────────────────────────────────────────────────────────────


def body_text(shape: dict | None) -> str:
    """A body-policy-shaped field as text -- honestly, because a hash is not a payload."""
    if not shape:
        return ""
    if shape.get("policy") == "NONE":
        return (
            "[not captured \u2014 body policy NONE, "
            + str(shape.get("length"))
            + " chars, hash "
            + str(shape.get("hash"))
            + "]"
        )
    text = str(shape.get("text") or "")
    if shape.get("policy") == "TRUNCATED" and shape.get("omitted"):
        text += (
            "\n\n[+"
            + str(shape["omitted"])
            + " chars omitted by body policy TRUNCATED]"
        )
    return text


def _path_of(url: str) -> str:
    rest = url.split("://", 1)[1] if "://" in url else ""
    return ("/" + rest.split("/", 1)[1]) if "/" in rest else (url if not rest else "/")
