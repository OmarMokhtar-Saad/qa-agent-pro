"""EN/AR bilingual rule pack (Batch 3 / rule pack 1).

Lean products document every user-facing message with an English AND an Arabic
value, usually in a ``DM##`` / ``MSG##`` table on the ticket. Every documented
pair must reach the tester as ONE test case whose Expected Results quote BOTH
strings verbatim.

Three hard design constraints drive this module:

1. **The LLM never re-types the strings.** Verbatim reproduction hallucinates,
   so the generator is told to emit opaque placeholder tokens
   (``{{EN:DM01}}`` / ``{{AR:DM01}}``) and this module substitutes the real
   values in code AFTER generation. That is also a prompt-injection win: the
   untrusted Arabic/English literals never enter the system prompt at all --
   only the opaque keys do.
2. **No token may ever reach a tester.** Substitution can fail three ways: the
   model writes a single-brace ``{EN:DM01}``, the substituted title overflows
   ``TestCase.title``'s 250-char cap so re-validation raises and the ORIGINAL
   (token-bearing) case is kept, or the whole pack raises. ``substitute_placeholders``
   clamps over-long fields and ``sweep_residual_placeholders`` is an unconditional
   backstop that rewrites any surviving token to ``UNRESOLVED_TEXT`` using
   ``model_copy`` (which does NOT re-run validators, so the sweep itself can
   never fail on a length constraint).
3. **Arabic must survive the XLSX path.** ``tools/xlsx_generator`` uses the
   directional helpers here (``contains_arabic`` / ``arabic_ratio`` /
   ``is_rtl_cell`` / ``bidi_isolate``) together with xlsxwriter's documented
   ``reading_order`` format property, which emits ``readingOrder="2"`` into the
   cell alignment element of ``xl/styles.xml`` (verified against xlsxwriter
   3.2.9 -- no monkeypatch or OOXML post-patching is required).

Every public helper is never-raise: on any failure it degrades to "no pairs" /
"cases unchanged" so generation is never broken.
"""

from __future__ import annotations

import logging
import re  # noqa: F401

from tools.bil_baked import _case_text, detect_baked_literals  # noqa: F401
from tools.bil_cells import (  # noqa: F401
    _SEP_CELL_RE,
    _cells,
    _find_en_cell,
    _find_key_cell,
    _pair_from_cells,
)
from tools.bil_consts import _MAX_VALUE_CHARS, MAX_PAIRS  # noqa: F401
from tools.bil_extract import (  # noqa: F401
    _AR_LABEL_RE,
    _EN_LABEL_RE,
    _pair_from_line,
    extract_language_pairs,
)
from tools.bil_key import (  # noqa: F401
    _KEY_RE,
    _TRIM_CHARS,
    _clean_value,
    normalize_key,
)
from tools.bil_manual import (  # noqa: F401
    _manual_validation_steps,
    build_manual_validation_case,
)
from tools.bil_pair import LanguagePair, PairSpec  # noqa: F401
from tools.bil_prompt import (  # noqa: F401
    checklist_lines,
    format_bilingual_prompt_block,
)
from tools.bil_report import (  # noqa: F401
    _fill_coverage_report,
    new_report,
)
from tools.bil_script import (  # noqa: F401
    _AR_CHAR_RE,
    _AR_RUN_RE,
    _ARABIC_RANGES,
    _LATIN_RE,
    LRM,
    RLM,
    RTL_MAJORITY_RATIO,
    arabic_ratio,
    bidi_isolate,
    contains_arabic,
    is_rtl_cell,
)
from tools.bil_subst import (  # noqa: F401
    _lookup,
    _sub_text,
    _substitute_case,
    substitute_placeholders,
)
from tools.bil_sweep import (  # noqa: F401
    _case_sweep_update,
    _step_sweep_update,
    sweep_residual_placeholders,
)
from tools.bil_tokens import (  # noqa: F401
    _FIELD_CAPS,
    PLACEHOLDER_RE,
    RESIDUAL_TOKEN_RE,
    UNRESOLVED_TEXT,
    _clamp,
)
from tools.bil_warning import (  # noqa: F401
    _WARNING_SPECS,
    _warning_findings,
    bilingual_warning_section,
)
from tools.models import (  # noqa: F401
    AutomationStatus,
    Priority,
    TestCase,
    TestStep,
    TestType,
)

logger = logging.getLogger(__name__)
