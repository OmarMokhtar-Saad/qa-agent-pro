"""The redaction nets, and the ONE escape that applies them.

Ported from the reference report shell. Two nets ride here, and they differ in an
important way. The VALUE net masks what the run presented as a credential -- or
what the tester typed into a device field -- so it does nothing until
:func:`learn_sensitive` or :func:`arm` has run. The PAIR net (:func:`scrub_pairs`)
masks a sensitive key's value where only the text of the pair survives, and a key
names itself, so it needs no arming and runs always.

:func:`e` is the choke point: ``html.escape(scrub_text(value), quote=True)``.
Enumerating render paths does not converge -- the reference added the value net at
one call site, then another, then another, and text kept reaching the page through
the next one -- so every path ends here. Everything that formats evidence text for
the page goes through :func:`e`, and everything written to DISK goes through
:func:`scrub_text` or :func:`scrub_json` first (the lane's two secret tests grep
every file under the cache root, so redaction at render alone is not enough).

This module knows no app. Its key list is the generic vocabulary of identifying
fields; the per-app vocabulary lives in the profile.
"""

from __future__ import annotations

import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
from contextlib import contextmanager  # noqa: F401
from contextvars import ContextVar  # noqa: F401
from typing import Iterable  # noqa: F401

from tools.mobile_shared.scrub_arm import (  # noqa: F401
    _SENSITIVE_VALUES,
    arm,
    armed,
    armed_scope,
    forget_sensitive,
    learn_sensitive,
)
from tools.mobile_shared.scrub_keys import (  # noqa: F401
    _KEY_AUTH,
    _KEY_CONTACT,
    _KEY_DEMOGRAPHICS,
    _KEY_IDENTITY,
    _KEY_NAMES,
    FIXTURE_SECRETS,
    MARKED_HEADERS,
    MIN_SENSITIVE_LEN,
    SECRET_HEADERS,
    SENSITIVE_KEY_RE,
)
from tools.mobile_shared.scrub_mask import mask_value, redact_header  # noqa: F401
from tools.mobile_shared.scrub_nets import (  # noqa: F401
    QUERY_PAIR_RE,
    TEXT_PAIR_RE,
    _mask_pair,
    _mask_query_pair,
    scrub_pairs,
    scrub_query,
)
from tools.mobile_shared.scrub_prose import (  # noqa: F401
    _PROSE_AGE_RE,
    _PROSE_BLOOD_RE,
    _PROSE_KEEP_RE,
    _PROSE_LABELLED_RE,
    _PROSE_PERSON_RE,
    _PROSE_PHONE_RE,
    _mask_list,
    scrub_prose,
)
from tools.mobile_shared.scrub_render import (  # noqa: F401
    _mask_keyed,
    _scrub_scalar,
    e,
    scrub_json,
    scrub_text,
)
