"""Host mode: prepared-generation (de)serialization and the Prep*Error contract. Facade over agents/host_prep_serde_* (C5 w14)."""

from __future__ import annotations

from agents.host_prep_serde_consts import _KNOWN_FIELDS  # noqa: F401
from agents.host_prep_serde_dump import (  # noqa: F401
    serialize_adopted_state,
    serialize_prepared,
)
from agents.host_prep_serde_errors import (  # noqa: F401
    PrepDeserializeError,
    PrepParseError,
    PrepSerdeError,
    PrepSerializeError,
)
from agents.host_prep_serde_load import deserialize_prepared  # noqa: F401
