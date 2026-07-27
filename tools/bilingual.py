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
import re

from tools.models import (
    AutomationStatus,
    Priority,
    TestCase,
    TestStep,
    TestType,
)

logger = logging.getLogger(__name__)

# --- Arabic script detection / bidi helpers ---------------------------------

_ARABIC_RANGES = (
    "؀-ۿ"  # Arabic
    "ݐ-ݿ"  # Arabic Supplement
    "ࢠ-ࣿ"  # Arabic Extended-A
    "ﭐ-﷿"  # Arabic Presentation Forms-A
    "ﹰ-﻿"  # Arabic Presentation Forms-B
)
_AR_CHAR_RE = re.compile("[" + _ARABIC_RANGES + "]")
# An Arabic "run": one or more Arabic words, allowing NEUTRAL characters
# (spaces, Arabic/Latin punctuation, digits) between two Arabic words, so a
# whole sentence is one directional run instead of many.
_AR_RUN_RE = re.compile(
    "["
    + _ARABIC_RANGES
    + "]+(?:[\\s0-9،؛؟٪-٭.,!?:;()\\[\\]/'\"\\-]+["
    + _ARABIC_RANGES
    + "]+)*"
)
_LATIN_RE = re.compile(r"[A-Za-z]")

RLM = "‏"  # RIGHT-TO-LEFT MARK
LRM = "‎"  # LEFT-TO-RIGHT MARK

# A cell whose letters are at least this fraction Arabic is treated as an
# Arabic-base cell (reading order right-to-left). Below it the cell keeps a
# left-to-right base and only the Arabic runs are isolated.
RTL_MAJORITY_RATIO = 0.5


def contains_arabic(text: str) -> bool:
    """True when *text* contains at least one Arabic-script character."""
    try:
        return bool(_AR_CHAR_RE.search(text or ""))
    except Exception:  # pragma: no cover - defensive
        return False


def arabic_ratio(text: str) -> float:
    """Share of the LETTERS in *text* that are Arabic (0.0 - 1.0).

    Digits, punctuation and whitespace are ignored so a mostly-Arabic sentence
    containing Latin product codes still scores high. Never raises.
    """
    try:
        s = text or ""
        arabic = len(_AR_CHAR_RE.findall(s))
        latin = len(_LATIN_RE.findall(s))
        total = arabic + latin
        return (arabic / total) if total else 0.0
    except Exception:  # pragma: no cover - defensive
        return 0.0


def is_rtl_cell(text: str) -> bool:
    """True when a spreadsheet cell should get reading_order=2 (right-to-left)."""
    return contains_arabic(text) and arabic_ratio(text) >= RTL_MAJORITY_RATIO


def bidi_isolate(text: str) -> str:
    """Wrap every Arabic run in RLM ... LRM.

    The Unicode Bidirectional Algorithm reorders neutral characters (quotes,
    parentheses, colons) according to the surrounding direction, so an Arabic
    string quoted inside an English sentence -- ``AR: "..."`` -- renders with its
    closing quote in the wrong place. Bracketing each Arabic run with
    RIGHT-TO-LEFT MARK / LEFT-TO-RIGHT MARK pins the boundaries.

    A no-op for text with no Arabic (returned unchanged, byte for byte) and
    idempotent (already-isolated text is returned unchanged). Never raises.
    """
    try:
        s = text or ""
        if not _AR_CHAR_RE.search(s):
            return s
        if RLM in s:
            return s  # already isolated - never double-wrap
        return _AR_RUN_RE.sub(lambda m: RLM + m.group(0) + LRM, s)
    except Exception:  # pragma: no cover - defensive
        logger.debug("bidi_isolate failed - returning text unchanged", exc_info=True)
        return text or ""


# --- Language-pair extraction -------------------------------------------------

# Deliberately a module constant, not a settings field: it is a safety cap on a
# parser, not an operator knob (same rubric-vs-knob reasoning as
# tools/atomic_checklist._SHORT_ITEM_WORDS).
MAX_PAIRS = 40
_MAX_VALUE_CHARS = 200

_KEY_RE = re.compile(
    r"\b((?:DM|MSG|ERR|TXT|LBL|SCR|NOTIF)[\s_-]?\d{1,3})\b", re.IGNORECASE
)
_EN_LABEL_RE = re.compile(r"\b(?:EN|ENG|ENGLISH)\b\s*[:=]", re.IGNORECASE)
_AR_LABEL_RE = re.compile(
    "(?:\\b(?:AR|ARA|ARABIC)\\b|العربية)\\s*[:=]",
    re.IGNORECASE,
)
_SEP_CELL_RE = re.compile(r"^[\s:.\-=_|]*$")
_TRIM_CHARS = " \t\"'“”«»‘’|-–—:•*/,;"


class LanguagePair:
    """One documented English/Arabic message pair from the ticket.

    A plain class (not a dataclass) so ``key`` is normalised on construction
    without a __post_init__ dance; every caller treats instances as immutable.
    """

    __slots__ = ("key", "en", "ar", "source_line")

    def __init__(self, key: str, en: str, ar: str, source_line: str = "") -> None:
        self.key = normalize_key(key)
        self.en = en
        self.ar = ar
        self.source_line = source_line

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"LanguagePair(key={self.key!r}, en={self.en!r}, ar={self.ar!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LanguagePair):
            return NotImplemented
        return (self.key, self.en, self.ar) == (other.key, other.en, other.ar)

    def __hash__(self) -> int:
        return hash((self.key, self.en, self.ar))


def normalize_key(raw: str) -> str:
    """Canonicalise a message key so ``DM 1`` / ``dm-01`` / ``DM001`` all match.

    Returns "" for empty or unparseable input. Never raises.
    """
    try:
        s = re.sub(r"[\s_-]+", "", str(raw or "")).upper()
        m = re.match(r"^([A-Z]+)0*(\d+)$", s)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"
        return s
    except Exception:  # pragma: no cover - defensive
        return ""


def _clean_value(raw: str) -> str:
    """Strip the key token, surrounding quotes/bullets and collapse whitespace."""
    try:
        s = (raw or "").strip()
        s = _KEY_RE.sub(" ", s)
        s = s.strip(_TRIM_CHARS).strip()
        s = re.sub(r"\s{2,}", " ", s)
        return s[:_MAX_VALUE_CHARS]
    except Exception:  # pragma: no cover - defensive
        return ""


def _cells(line: str) -> list[str]:
    """Split a markdown-pipe or tab-delimited table row into cells."""
    if "|" in line:
        return [c.strip() for c in line.strip().strip("|").split("|")]
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    return []


def _pair_from_cells(cells: list[str]) -> tuple[str, str, str] | None:
    """(key, en, ar) from a table row, or None when the row is not a pair row."""
    if len(cells) < 2 or all(_SEP_CELL_RE.match(c) for c in cells):
        return None
    key = ""
    key_idx = -1
    for i, cell in enumerate(cells):
        m = _KEY_RE.search(cell)
        if m:
            key, key_idx = normalize_key(m.group(1)), i
            break
    ar_idx = -1
    for i, cell in enumerate(cells):
        if i != key_idx and _AR_CHAR_RE.search(cell):
            ar_idx = i
            break
    if ar_idx < 0:
        return None
    en_idx, best = -1, 0
    for i, cell in enumerate(cells):
        if i in (key_idx, ar_idx):
            continue
        n = len(_LATIN_RE.findall(cell))
        if n > best:
            best, en_idx = n, i
    if en_idx < 0:
        return None
    return key, _clean_value(cells[en_idx]), _clean_value(cells[ar_idx])


def _pair_from_line(line: str) -> tuple[str, str, str] | None:
    """(key, en, ar) from a free-text line, or None."""
    if not _AR_CHAR_RE.search(line):
        return None
    key = ""
    m = _KEY_RE.search(line)
    if m:
        key = normalize_key(m.group(1))
    en_m = _EN_LABEL_RE.search(line)
    ar_m = _AR_LABEL_RE.search(line)
    if en_m and ar_m:
        if en_m.start() < ar_m.start():
            en_raw, ar_raw = line[en_m.end() : ar_m.start()], line[ar_m.end() :]
        else:
            ar_raw, en_raw = line[ar_m.end() : en_m.start()], line[en_m.end() :]
    else:
        run = _AR_RUN_RE.search(line)
        if not run:
            return None
        ar_raw = run.group(0)
        en_raw = line[: run.start()] or line[run.end() :]
    en, ar = _clean_value(en_raw), _clean_value(ar_raw)
    if not en or not ar:
        return None
    return key, en, ar


def extract_language_pairs(text: str, limit: int = MAX_PAIRS) -> list[LanguagePair]:
    """Parse documented EN/AR message pairs out of ticket text.

    Recognises markdown/tab tables (``| DM01 | Login failed | ... |``), labelled
    lines (``DM01 - EN: "Login failed" / AR: "..."``) and bare
    ``English text - Arabic text`` lines. A pair with no ``DM##``-style key gets
    a synthetic ``PAIR01`` key so it is still enforceable.

    Returns [] for text with no Arabic. Never raises -- a parse failure returns
    whatever was parsed so far.
    """
    pairs: list[LanguagePair] = []
    try:
        seen: set[str] = set()
        auto = 0
        cap = max(1, int(limit or MAX_PAIRS))
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if len(line) < 3 or not _AR_CHAR_RE.search(line):
                continue
            parsed = _pair_from_cells(_cells(line)) or _pair_from_line(line)
            if not parsed:
                continue
            key, en, ar = parsed
            if not en or not ar:
                continue
            if not key:
                auto += 1
                key = f"PAIR{auto:02d}"
            if key in seen:
                continue
            seen.add(key)
            pairs.append(LanguagePair(key=key, en=en, ar=ar, source_line=line[:300]))
            if len(pairs) >= cap:
                logger.info("extract_language_pairs: hit the %d-pair cap", cap)
                break
    except Exception:
        logger.exception("extract_language_pairs failed - returning partial result")
    return pairs


# --- Checklist decomposition + prompt block -----------------------------------


def checklist_lines(pairs: list[LanguagePair]) -> list[str]:
    """One mandatory checklist line per documented pair (decomposition rule).

    The line carries the PLACEHOLDER TOKENS, never the literal strings, so the
    same text is safe on the checklist that is rendered into the prompt, into the
    XLSX 'Requirements Checklist' sheet and into the coverage tally. Written in
    EARS event_driven shape to match the rest of the Batch-2 checklist.
    """
    out: list[str] = []
    try:
        for p in pairs:
            out.append(
                f"When the {p.key} message is displayed, the system shall show it "
                "in BOTH documented languages, verified inside ONE test case: step "
                "1 with the application locale set to English, step 2 with the "
                "locale set to Arabic. The Expected Results shall contain, "
                f'literally: EN: "{{{{EN:{p.key}}}}}" and AR: "{{{{AR:{p.key}}}}}".'
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("checklist_lines failed - returning partial result")
    return out


def format_bilingual_prompt_block(
    pairs: list[LanguagePair], checklist_mode: bool = False
) -> str:
    """System-prompt clause for the EN/AR rule pack. "" when there are no pairs.

    Contains ONLY the opaque keys -- never the untrusted message text, which is
    what keeps this block outside the prompt-injection surface.

    ``checklist_mode`` is True when the mandated lines were injected onto the
    Batch-2 atomic checklist. The wording then points at the checklist ids and
    repeats Batch 2's "your requirement_id tag is advisory, coverage is
    recomputed externally" contract, so the two prompts cannot give the model
    contradictory instructions about who scores coverage.
    """
    try:
        if not pairs:
            return ""
        keys = ", ".join(p.key for p in pairs)
        head = (
            "\n\n## EN/AR BILINGUAL RULE (mandatory)\n"
            f"The source ticket documents {len(pairs)} English/Arabic message "
            f"pair(s), keyed: {keys}.\n"
        )
        if checklist_mode:
            head += (
                "Each one is already on the Atomic Requirements Checklist as a line "
                "with an id of the form RP-I18N-<KEY>. Cover the ones in YOUR "
                "category's scope; as with every other checklist line, coverage is "
                "recomputed afterwards by the same INDEPENDENT matcher, so your "
                "`requirement_id` tag can neither inflate nor deflate the score.\n"
            )
        return head + (
            "- Produce EXACTLY ONE test case per key, with TWO steps: step 1 with "
            "the application locale set to English, step 2 with the locale set to "
            "Arabic. NEVER split a key across two test cases.\n"
            "- NEVER retype, translate or paraphrase the message text. Write the "
            "placeholder token EXACTLY as shown and nothing else: the English "
            "expected result must contain {{EN:KEY}} and the Arabic expected "
            "result must contain {{AR:KEY}}, substituting the real key (e.g. "
            "{{EN:DM01}} / {{AR:DM01}}). Use DOUBLE braces. The system replaces "
            "the tokens with the documented strings after generation.\n"
            '- Quote the tokens, e.g. Expected: the banner shows EN: "{{EN:DM01}}" '
            'in English and AR: "{{AR:DM01}}" in Arabic.\n'
            "- Do NOT invent keys that are not in the list above.\n"
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("format_bilingual_prompt_block failed - returning ''")
        return ""


# --- Deterministic placeholder substitution -----------------------------------

# Accepts ONE or TWO braces on each side. The prompt asks for double braces, but
# a model that emits `{EN:DM01}` used to slip straight through a `"{{" in value`
# guard and ship a raw token to the tester; matching both shapes turns that
# failure mode into a successful substitution instead.
PLACEHOLDER_RE = re.compile(
    r"\{\{?\s*(EN|AR)\s*:\s*([A-Za-z0-9_ -]{1,24}?)\s*\}\}?", re.IGNORECASE
)

# The unconditional backstop. Anything still brace-wrapped after substitution is
# a bug on OUR side, and a tester must never be handed `{{EN:DM01}}` in an
# Expected Results cell.
#
# SCOPE IS DELIBERATELY NARROW. The double-brace branch is unambiguous -- `{{x}}`
# is never legitimate test content. The single-brace branch is restricted to the
# `{EN:...}` / `{AR:...}` shape ON PURPOSE: a blanket `\{[^{}]*\}` also matches
# a JSON request body (`{"amount": 500}`) or a parameterised value (`{userId}`)
# in a Test Data cell, which the standing-rules API pack makes MORE likely, not
# less. Neutralising those would silently destroy real test data to fix a
# cosmetic leak -- a worse bug than the one being fixed.
RESIDUAL_TOKEN_RE = re.compile(
    r"\{\{[^{}\n]{0,120}\}\}|\{\s*(?:EN|AR)\s*:[^{}\n]{0,60}\}",
    re.IGNORECASE,
)

UNRESOLVED_TEXT = "(value not documented in the ticket - confirm with the BA)"

# TestCase.title is max_length=250 (tools/models.py:155) and module is
# max_length=100. A 200-char substituted message pushed into a title used to make
# TestCase.model_validate raise, which made the code keep the ORIGINAL,
# token-bearing case. Clamp instead.
_FIELD_CAPS = {"title": 250, "module": 100}


def _lookup(pairs: list[LanguagePair]) -> dict[str, LanguagePair]:
    table: dict[str, LanguagePair] = {}
    for p in pairs:
        k = normalize_key(p.key)
        if k:
            table[k] = p
    return table


def _sub_text(
    text: str,
    table: dict[str, LanguagePair],
    used: set[str],
    unresolved: set[str],
) -> str:
    def _repl(m: re.Match) -> str:
        lang = m.group(1).upper()
        key = normalize_key(m.group(2))
        pair = table.get(key)
        if pair is None:
            unresolved.add(f"{lang}:{key}")
            return UNRESOLVED_TEXT
        used.add(f"{lang}:{key}")
        return pair.en if lang == "EN" else pair.ar

    return PLACEHOLDER_RE.sub(_repl, text or "")


def _clamp(value: str, field_name: str) -> str:
    cap = _FIELD_CAPS.get(field_name)
    if cap and len(value) > cap:
        logger.info(
            "bilingual substitution overflowed %s (%d > %d chars) - clamping",
            field_name,
            len(value),
            cap,
        )
        return value[: cap - 1].rstrip() + "…"
    return value


def new_report(documented_pairs: int = 0) -> dict:
    """An empty substitution report (also the degraded / never-raise result)."""
    return {
        "documented_pairs": documented_pairs,
        "substitutions": 0,
        "unresolved": [],
        "covered_keys": [],
        "missing_keys": [],
        "partial_keys": [],
        "split_keys": [],
        "cases_touched": [],
        "baked_keys": [],
        "residual_tokens": [],
        # stable_ids of the cases that actually carry a documented pair. Handed to
        # _semantic_dedupe_cases as protected_stable_ids: a mandated per-key case
        # must never be merged away as a near-duplicate, no matter how similar two
        # locale-switch cases look to an embedding model.
        "protected_stable_ids": [],
        "placeholders_seen": False,
    }


def substitute_placeholders(
    cases: list[TestCase], pairs: list[LanguagePair]
) -> tuple[list[TestCase], dict]:
    """Replace ``{{EN:KEY}}`` / ``{{AR:KEY}}`` with the documented strings.

    100% string fidelity: the values come from the parsed ticket, never from the
    model. Over-long substituted fields are CLAMPED (not rejected), so a case is
    only kept in its original token-bearing form when re-validation fails for
    some other reason -- and ``sweep_residual_placeholders`` then cleans that up.
    Returns ``(cases, report)``. Never raises.
    """
    report = new_report(len(pairs))
    if not cases:
        return list(cases or []), report
    try:
        table = _lookup(pairs)
        unresolved: set[str] = set()
        per_case_keys: dict[str, set[str]] = {}
        out: list[TestCase] = []
        for tc in cases:
            used: set[str] = set()
            local_unresolved: set[str] = set()
            data = tc.model_dump()
            touched = False
            for fname in ("title", "preconditions", "postconditions", "module"):
                value = data.get(fname)
                if isinstance(value, str) and PLACEHOLDER_RE.search(value):
                    data[fname] = _clamp(
                        _sub_text(value, table, used, local_unresolved), fname
                    )
                    touched = True
            for step in data.get("steps") or []:
                for fname in ("action", "test_data", "expected_result"):
                    value = step.get(fname)
                    if isinstance(value, str) and PLACEHOLDER_RE.search(value):
                        step[fname] = _sub_text(value, table, used, local_unresolved)
                        touched = True
            unresolved |= local_unresolved
            if not touched:
                out.append(tc)
                continue
            report["substitutions"] += len(used)
            try:
                rebuilt = TestCase.model_validate(data)
            except Exception:
                logger.warning(
                    "bilingual substitution produced an invalid case (%s) - keeping "
                    "the original; the residual-token sweep will neutralise any "
                    "token left in it",
                    getattr(tc, "tc_id", "?"),
                    exc_info=True,
                )
                out.append(tc)
                continue
            out.append(rebuilt)
            if used:
                report["cases_touched"].append(rebuilt.tc_id)
                per_case_keys[rebuilt.tc_id] = used

        holder: dict[str, set[str]] = {}
        langs: dict[str, set[str]] = {}
        for tc_id, keys in per_case_keys.items():
            for entry in keys:
                lang, key = entry.split(":", 1)
                holder.setdefault(key, set()).add(tc_id)
                langs.setdefault(key, set()).add(lang)
        covered = set(holder)
        report["covered_keys"] = sorted(covered)
        report["missing_keys"] = sorted(
            k for k in (normalize_key(p.key) for p in pairs) if k and k not in covered
        )
        report["partial_keys"] = sorted(k for k, ls in langs.items() if len(ls) < 2)
        report["split_keys"] = sorted(k for k, ids in holder.items() if len(ids) > 1)
        report["unresolved"] = sorted(unresolved)
        # PROTECTION LIST, not a nice-to-have. Two bilingual cases differ only by
        # which documented message they quote; real sentence embeddings can score
        # that pair above QA_SEMANTIC_DEDUP_THRESHOLD (default 0.9) even AFTER
        # substitution, so ordering substitution before dedup is necessary but not
        # sufficient. stable_id is derived from (title, steps) and the only later
        # mutation before dedup is the sweep's model_copy, which does NOT re-run
        # that validator -- so these ids still match at the dedup call site.
        report["protected_stable_ids"] = sorted(
            {
                sid
                for sid in (
                    (tc.stable_id or "") for tc in out if tc.tc_id in per_case_keys
                )
                if sid
            }
        )
        report["placeholders_seen"] = bool(per_case_keys or unresolved)
        report["baked_keys"] = detect_baked_literals(out, pairs, covered)
        return out, report
    except Exception:
        logger.exception("substitute_placeholders failed - cases returned unchanged")
        return list(cases), report


def sweep_residual_placeholders(
    cases: list[TestCase],
) -> tuple[list[TestCase], list[str]]:
    """Replace ANY surviving ``{{...}}`` / ``{EN:KEY}`` token with UNRESOLVED_TEXT.

    The unconditional backstop for the three ways a token can survive
    substitution: a re-validation failure that kept the original case, a token
    shape the substituter did not recognise, and an exception inside the pack.
    Rewrites via ``model_copy(update=...)``, which does NOT re-run pydantic
    validators (the same property the pipeline's TC renumber relies on --
    agents/test_scenario_agent.py:2153-2157), so the sweep can never itself fail
    on a length or pattern constraint.

    Returns ``(cases, [tc_id, ...])`` -- the ids that needed cleaning. Never
    raises; on failure the input list comes back untouched.
    """
    try:
        if not cases:
            return list(cases or []), []
        out: list[TestCase] = []
        dirty: list[str] = []
        for tc in cases:
            update: dict = {}
            for fname in ("title", "preconditions", "postconditions", "module"):
                value = getattr(tc, fname, None)
                if isinstance(value, str) and RESIDUAL_TOKEN_RE.search(value):
                    update[fname] = _clamp(
                        RESIDUAL_TOKEN_RE.sub(UNRESOLVED_TEXT, value), fname
                    )
            new_steps = []
            steps_dirty = False
            for step in getattr(tc, "steps", None) or []:
                s_update: dict = {}
                for fname in ("action", "test_data", "expected_result"):
                    value = getattr(step, fname, None)
                    if isinstance(value, str) and RESIDUAL_TOKEN_RE.search(value):
                        s_update[fname] = RESIDUAL_TOKEN_RE.sub(UNRESOLVED_TEXT, value)
                if s_update:
                    steps_dirty = True
                    new_steps.append(step.model_copy(update=s_update))
                else:
                    new_steps.append(step)
            if steps_dirty:
                update["steps"] = new_steps
            if update:
                dirty.append(tc.tc_id)
                out.append(tc.model_copy(update=update))
            else:
                out.append(tc)
        if dirty:
            logger.warning(
                "residual placeholder sweep neutralised token(s) in %d case(s): %s "
                "- the bilingual substitution did not fully resolve",
                len(dirty),
                ", ".join(dirty[:10]),
            )
        return out, dirty
    except Exception:
        logger.exception("sweep_residual_placeholders failed - cases unchanged")
        return list(cases), []


def _case_text(tc: TestCase) -> str:
    chunks = [getattr(tc, "title", "") or ""]
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "action", "") or "")
        chunks.append(getattr(step, "expected_result", "") or "")
        chunks.append(getattr(step, "test_data", "") or "")
    return "\n".join(chunks)


def detect_baked_literals(
    cases: list[TestCase], pairs: list[LanguagePair], covered: set[str]
) -> list[str]:
    """Keys whose Arabic literal was typed straight into a case (no placeholder).

    This is the documented LLM-compliance failure mode: the model ignores the
    placeholder instruction and reproduces the string itself, which is exactly
    what the placeholder mechanism exists to prevent. Flag only -- the text may
    still be correct, but it was NOT mechanically carried through, so a human
    has to diff it against the ticket. Never raises.
    """
    try:
        if not pairs or not cases:
            return []
        blob = "\n".join(_case_text(tc) for tc in cases)
        out: list[str] = []
        for p in pairs:
            key = normalize_key(p.key)
            if not key or key in covered:
                continue
            if p.ar and len(p.ar) >= 4 and p.ar in blob:
                out.append(key)
        return sorted(set(out))
    except Exception:  # pragma: no cover - defensive
        logger.exception("detect_baked_literals failed - flagging nothing")
        return []


# --- Templated manual (native-speaker) validation case ------------------------


def build_manual_validation_case(
    pairs: list[LanguagePair], tc_id: str = "TC-999"
) -> TestCase | None:
    """A hand-authored, NON-generated linguistic-validation case.

    One automated bilingual case per key proves the strings are wired up; it
    cannot prove the Arabic is grammatical, natural, or laid out correctly.
    Industry practice pairs automated multi-locale checks with a native-speaker
    review pass, so the pack appends this fixed template (never LLM-written, so
    it cannot hallucinate) and marks it Cannot Be Automated. Lists KEYS only --
    no untrusted message text is embedded.

    Returns None when there are no pairs, or on any failure. Never raises.
    """
    try:
        if not pairs:
            return None
        keys = ", ".join(p.key for p in pairs[:30])
        more = " and the remaining documented keys" if len(pairs) > 30 else ""
        steps = [
            TestStep(
                step_number=1,
                action=(
                    "Switch the application locale to Arabic (Settings > Language > "
                    "Arabic) and open every screen that shows a documented message: "
                    f"{keys}{more}."
                ),
                test_data=None,
                expected_result=(
                    "Every documented Arabic string is rendered, right-aligned and "
                    "fully visible (no truncation, no clipped diacritics) and is not "
                    "replaced by its English fallback."
                ),
            ),
            TestStep(
                step_number=2,
                action=(
                    "Have a NATIVE ARABIC SPEAKER read each rendered Arabic message "
                    "and compare it word for word against the wording documented on "
                    "the ticket."
                ),
                test_data=None,
                expected_result=(
                    "Each Arabic message matches the documented wording, is "
                    "grammatically correct and reads naturally against its English "
                    "counterpart. Any mismatch is raised as a defect on the ticket."
                ),
            ),
            TestStep(
                step_number=3,
                action=(
                    "With the locale still Arabic, check numbers, dates, currency "
                    "amounts and mixed-direction lines (a Latin product code inside "
                    "an Arabic sentence)."
                ),
                test_data=None,
                expected_result=(
                    "Numerals, dates and currency read in the correct order and "
                    "mixed-direction lines are not visually reordered, split or "
                    "mirrored."
                ),
            ),
        ]
        return TestCase(
            tc_id=tc_id,
            module="Localization (EN/AR)",
            title=(
                "Native-speaker linguistic validation of every documented Arabic "
                "message"
            ),
            priority=Priority.HIGH,
            type=TestType.FUNCTIONAL,
            preconditions=(
                "A build with the Arabic locale enabled and a native Arabic speaker "
                "available to review the wording."
            ),
            steps=steps,
            postconditions=None,
            automation_status=AutomationStatus.CANNOT_BE_AUTOMATED,
            requirement_id=None,
        )
    except Exception:
        logger.exception("build_manual_validation_case failed - returning None")
        return None


# --- Advisory section ----------------------------------------------------------


def bilingual_warning_section(pairs: list[LanguagePair], report: dict) -> str:
    """Advisory markdown for the EN/AR pack. "" when there is nothing to report.

    Never raises.
    """
    try:
        if not pairs:
            return ""
        report = report or {}
        lines = [
            "\n\n## EN/AR Bilingual Coverage",
            "",
            f"The ticket documents **{len(pairs)}** English/Arabic message pair(s). "
            f"**{len(report.get('covered_keys') or [])}** were carried into the suite "
            "verbatim (substituted in code, not retyped by the model).",
            "",
            "> AUTOMATED coverage only. One bilingual case per key proves the strings "
            "are wired up; it does NOT prove the Arabic is grammatical, natural or "
            "correctly laid out. The templated _Native-speaker linguistic "
            "validation_ case in this suite is the MANUAL half and must be executed "
            "by an Arabic speaker.",
        ]
        flagged = False
        missing = report.get("missing_keys") or []
        if missing:
            flagged = True
            lines.append(
                "- **No test case covers these keys:** "
                + ", ".join(missing[:20])
                + (" ..." if len(missing) > 20 else "")
            )
        partial = report.get("partial_keys") or []
        if partial:
            flagged = True
            lines.append(
                "- **Only ONE language was quoted (the pair is incomplete):** "
                + ", ".join(partial[:20])
            )
        split = report.get("split_keys") or []
        if split:
            flagged = True
            lines.append(
                "- **EN and AR landed in DIFFERENT test cases (should be one case, "
                "two steps):** " + ", ".join(split[:20])
            )
        baked = report.get("baked_keys") or []
        if baked:
            flagged = True
            lines.append(
                "- **The model typed the string itself instead of using the "
                "placeholder - re-check it character by character against the "
                "ticket:** " + ", ".join(baked[:20])
            )
        unresolved = report.get("unresolved") or []
        if unresolved:
            flagged = True
            lines.append(
                "- **Placeholders referenced a key the ticket does not document:** "
                + ", ".join(unresolved[:20])
            )
        residual = report.get("residual_tokens") or []
        if residual:
            flagged = True
            lines.append(
                "- **Unresolved placeholder tokens were neutralised in these cases - "
                "they now read the 'not documented' text instead of a message:** "
                + ", ".join(residual[:20])
            )
        if not flagged:
            lines.append("- All documented pairs are covered in a single case each.")
        return "\n".join(lines)
    except Exception:
        logger.exception("bilingual_warning_section failed - returning ''")
        return ""
