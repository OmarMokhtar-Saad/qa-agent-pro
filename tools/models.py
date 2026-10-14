from __future__ import annotations

import hashlib  # noqa: F401
import logging
import re  # noqa: F401
import uuid  # noqa: F401
from enum import Enum  # noqa: F401
from typing import Literal, Optional  # noqa: F401

from pydantic import (  # noqa: F401
    BaseModel,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from tools.models_case import TestCase  # noqa: F401
from tools.models_core import (  # noqa: F401
    AutomationStatus,
    Priority,
    TestDataItem,
    TestStep,
    TestType,
    _compute_stable_id,
    format_test_data_lines,
)
from tools.models_ids import (  # noqa: F401
    _CANONICAL_AC_RE,
    display_requirement_id,
    normalize_ac_id,
)
from tools.models_suite import TestSuite  # noqa: F401

logger = logging.getLogger(__name__)
