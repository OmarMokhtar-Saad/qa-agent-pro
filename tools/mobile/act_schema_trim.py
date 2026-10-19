"""Shrink a pydantic JSON schema to what a model needs to emit a valid script.

Pydantic copies every class docstring into ``description`` and every field name
into ``title``. The docstrings are maintainer prose (the model-facing guidance is
in ``describe_vocabulary``, sent once per run), so shipping them in every packet
of every turn was pure cost. Only STRING ``title``/``description`` values are
dropped: a property that is itself NAMED ``title`` or ``description`` stays.
"""

from __future__ import annotations

_DROPPED = ("title", "description")


def trim_schema(node: object) -> object:
    """A copy of *node* without prose ``title``/``description`` strings."""
    if isinstance(node, list):
        return [trim_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _trim_value(key, value)
        for key, value in node.items()
        if not (key in _DROPPED and isinstance(value, str))
    }


def _trim_value(key: str, value: object) -> object:
    """Recurse; a ``properties`` map keeps its field NAMES whatever they are."""
    if key == "properties" and isinstance(value, dict):
        return {name: trim_schema(sub) for name, sub in value.items()}
    return trim_schema(value)


def trim_keeping(schema: dict, keep: tuple[str, ...]) -> dict:
    """:func:`trim_schema`, but each ``$defs`` entry named in *keep* keeps its
    ``description`` -- guidance the model has no other copy of."""
    out = trim_schema(schema)
    if not isinstance(out, dict):
        raise TypeError("trim_keeping needs a schema object, got " + type(out).__name__)
    defs = schema.get("$defs") or {}
    for name in keep:
        text = (defs.get(name) or {}).get("description")
        if isinstance(text, str):
            out["$defs"][name]["description"] = text
    return out
