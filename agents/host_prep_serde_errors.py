"""Host mode: the Prep*Error contract of prep (de)serialization and suite parsing."""

from __future__ import annotations


class PrepSerdeError(Exception):
    """Base for prep (de)serialization failures. The ops-3d tool wraps it and
    returns a plain tool error -- it never propagates to the MCP client."""


class PrepSerializeError(PrepSerdeError):
    """A PreparedGeneration field could not be represented losslessly. Raised
    LOUDLY rather than dropping fidelity, so the caller returns an error instead
    of persisting a corrupt prep record."""


class PrepDeserializeError(PrepSerdeError):
    """A stored payload is malformed, tampered, or a wrong/unknown schema
    version. The caller treats this exactly like a stale/unknown prep_id."""


class PrepParseError(PrepSerdeError):
    """Host-submitted suite JSON could not be extracted/validated. A sibling of
    the (de)serialize errors so ops-3d catches the whole PrepSerdeError family and
    turns a bad submission into a tester-readable "your JSON did not parse" reply
    rather than a stack trace."""
