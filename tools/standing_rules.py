"""Standing rules trigger detector (Batch 3 / rule pack 3).

Two domain rules that fire on TICKET CONTENT rather than on a fan-out category:

* ANY mention of an API -- even a passing "API Failure" error flow with no
  contract attached -- mandates status-code, request-design and
  response-structure coverage (plus error handling when the ticket names a
  failure flow). Today Integration/Security exist only as fan-out CATEGORIES,
  which the model is free to fill with UI cases.
* EVERY user-facing story gets one baseline "the UI is built properly" case.

TRIGGER PRECISION IS A REAL RISK, so the API detector is TWO-TIERED:

  STRONG signals (``_API_STRONG``) each fire on their own: "API", "endpoint",
  "REST", "GraphQL", "webhook", "microservice", "request body", "response
  structure", "status code", "GET /path", "swagger", "openapi", "backend
  service".

  WEAK signals (``_API_WEAK``) -- "HTTP" (which also matches a bare
  ``http://`` URL), "JSON", "integration", "payload", "service call" -- do NOT
  fire alone. TWO DISTINCT weak signals are required, and the result is recorded
  as ``api_weak_only`` so both the prompt block and the advisory section say the
  trigger was circumstantial and name the exact words that fired it. Without
  this, a pure-UI ticket saying "integration with the wallet screen" forced
  three or four mandatory backend API cases onto the generator.

  Bare 3-digit numbers are NOT an error-flow signal either: ``\\b(?:4\\d\\d|5\\d\\d)\\b``
  matched "SAR 500". The status-code pattern now requires adjacent HTTP/status
  vocabulary.

Where the ticket documents no contract, the resulting cases are written against
standard REST convention and MECHANICALLY labelled as assumed. The label is a
fixed constant plus the sanitised source reference -- never an LLM-written
citation, so it can never turn into "per RFC 9110" for an RFC nobody cited. When
a real OpenAPI/Swagger spec was fetched (``tools/swagger_fetcher``), the
assumption disappears and the cases are documented instead.

Never raises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from tools.models import TestCase
from tools.swagger_fetcher import looks_like_openapi_url

logger = logging.getLogger(__name__)

# --- API triggers: strong (fire alone) vs weak (need a corroborating signal) ---

_API_STRONG_PATTERNS = (
    r"\bAPIs?\b",
    r"\bendpoints?\b",
    r"\bREST(?:ful)?\b",
    r"\bGraphQL\b",
    r"\bwebhooks?\b",
    r"\bmicroservices?\b",
    r"\brequest (?:body|payload|header|design)\b",
    r"\bresponse (?:body|payload|code|structure|schema)\b",
    r"\bstatus code\b",
    r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/",
    r"\bswagger\b",
    r"\bopenapi\b",
    r"\bbackend service\b",
    r"\bservice contract\b",
)
_API_STRONG_RE = tuple(re.compile(p, re.IGNORECASE) for p in _API_STRONG_PATTERNS)

# Each of these on its own is far too common on a pure-UI ticket. `\bHTTP\b`
# matches any `http://` link; `\bintegration\b` matches "integration with the
# wallet screen"; `\bJSON\b` matches a config snippet. TWO distinct weak hits are
# required before the API rules fire, and the result is marked weak-only.
_API_WEAK_PATTERNS = (
    r"\bHTTP\b",
    r"\bJSON\b",
    r"\bintegration\b",
    r"\bpayloads?\b",
    r"\bservice call\b",
    r"\bthird[- ]party\b",
)
_API_WEAK_RE = tuple(re.compile(p, re.IGNORECASE) for p in _API_WEAK_PATTERNS)
_API_WEAK_MIN = 2

# Error-flow signals. A bare 3-digit number is NOT one of them ("SAR 500",
# "500 users"): a status code must sit next to HTTP/status/error/response
# vocabulary. Bare "fail/failed/failure" is likewise excluded -- almost every
# negative test case says it -- so the flow must be named explicitly.
_ERROR_FLOW_PATTERNS = (
    r"\bAPI (?:failure|error)\b",
    r"\bservice (?:is )?(?:down|unavailable)\b",
    r"\btime[- ]?out(?:s|ed)?\b",
    r"\berror (?:flow|case|handling|state|response)\b",
    r"\b(?:HTTP|status|code|error|response|returns?|responds? with)\s*"
    r"(?:code)?\s*[:=]?\s*(?:4\d\d|5\d\d)\b",
    r"\b(?:4\d\d|5\d\d)\s+(?:error|response|status|page)\b",
    r"\bunhandled exception\b",
    r"\brollback\b",
)
_ERROR_FLOW_RE = tuple(re.compile(p, re.IGNORECASE) for p in _ERROR_FLOW_PATTERNS)

_UI_PATTERNS = (
    r"\bscreens?\b",
    r"\bpages?\b",
    r"\bUI\b",
    r"\bUX\b",
    r"\bbuttons?\b",
    r"\bforms?\b",
    r"\bfields?\b",
    r"\bmockups?\b",
    r"\bdesigns?\b",
    r"\bFigma\b",
    r"\blayout\b",
    r"\bdropdowns?\b",
    r"\bmodals?\b",
    r"\bbanner\b",
    r"\buser (?:can|sees|taps|clicks)\b",
    r"\bdisplay(?:s|ed)?\b",
)
_UI_RE = tuple(re.compile(p, re.IGNORECASE) for p in _UI_PATTERNS)

# An unresolved question in the ticket / comment thread. The generator must not
# silently pick a side ("should we return 201 or 202?" -> writes a 201 case as
# if it were documented); it is surfaced for clarification instead.
_OPEN_QUESTION_RE = re.compile(
    r"(?im)^.{0,200}?\b(?:should we|shall we|do we|can we|are we|which one|"
    r"still (?:open|tbd|to be (?:decided|confirmed))|to be (?:confirmed|decided)|"
    r"TBC|TBD|open question|pending (?:confirmation|decision)|waiting on)\b.{0,200}$"
)

_MAX_EVIDENCE = 5
_MAX_OPEN_QUESTIONS = 5

# Mechanical assumption label. Fixed constants -- never model-written, and never
# citing a standard document the ticket did not name. The wording deliberately
# reads "we are guessing, verify the guess", NOT "documented in the REST
# standard, no need to check".
ASSUMED_EN = (
    "ASSUMPTION, NOT A REQUIREMENT: this behaviour is assumed based on standard "
    "REST convention and is NOT documented in {ref}. Verify the assumption with "
    "the API owner before signing the case off."
)
ASSUMED_AR = (
    "فرضية وليست متطلبًا: هذا السلوك مفترض بناءً على أعراف REST القياسية وغير "
    "موثّق في {ref}. يجب التحقق من الفرضية مع مالك الواجهة البرمجية قبل اعتماد "
    "الحالة."
)
DOCUMENTED_EN = "Contract taken from the OpenAPI/Swagger spec linked on {ref}."

# Marker the generator is told to put in the title of any case built on an
# assumed contract. Detected mechanically afterwards to attach the full label.
ASSUMED_MARKER = "[ASSUMED]"
CLARIFY_MARKER = "[NEEDS-CLARIFICATION]"

_MAX_REF_CHARS = 120
_MAX_QUESTION_CHARS = 200
_REF_STRIP_RE = re.compile(r"[\r\n\t]+")
# Leading markdown/quote/list structure an attacker-written comment could use to
# forge a heading, a list item or a blockquote inside OUR advisory section.
_MD_LEAD_RE = re.compile(r"^[\s>#*\-+0-9.)\]\[|`]+")


@dataclass
class Triggers:
    """What the ticket content mandates. All fields default to "nothing fired"."""

    api: bool = False
    api_evidence: list[str] = field(default_factory=list)
    api_weak_only: bool = False
    ui: bool = False
    ui_evidence: list[str] = field(default_factory=list)
    error_flow: bool = False
    has_spec: bool = False
    spec_hint: str = ""
    open_questions: list[str] = field(default_factory=list)

    @property
    def fired(self) -> bool:
        return bool(self.api or self.ui)


def _evidence(blob: str, patterns: tuple[re.Pattern, ...]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        m = pat.search(blob)
        if m:
            out.append(m.group(0))
        if len(out) >= _MAX_EVIDENCE:
            break
    return out


def safe_display(raw: str) -> str:
    """Neutralise a snippet of UNTRUSTED ticket prose for tester-facing markdown.

    ``standing_warning_section`` is the ONE place this batch echoes externally
    sourced text (an unresolved question lifted from the description or a Jira
    comment) back to the tester. It never reaches an LLM, so ``wrap_untrusted``
    is the wrong tool, but rendering it raw lets comment text forge markdown
    structure -- a fenced block, a heading, a link, or a bold line that reads like
    part of OUR report. Collapse whitespace, strip the leading list/heading/quote
    markers and neutralise the inline markdown/HTML metacharacters, then render
    the result inside a quoted span at the call site.

    Never raises; returns "" for empty input.
    """
    try:
        text = _REF_STRIP_RE.sub(" ", str(raw or "")).strip()
        text = _MD_LEAD_RE.sub("", text)
        for ch in ("`", "*", "_", "[", "]", "<", ">", "|", "~"):
            text = text.replace(ch, " ")
        text = " ".join(text.split())
        return text[:_MAX_QUESTION_CHARS]
    except Exception:  # pragma: no cover - defensive
        return ""


def safe_ref(raw: str) -> str:
    """Sanitise a source reference (ticket key / URL) for display.

    Collapses newlines and caps the length so an externally-sourced value can
    never smuggle instructions or blow up a cell. Never raises.
    """
    try:
        s = _REF_STRIP_RE.sub(" ", str(raw or "")).strip()
        return (s[:_MAX_REF_CHARS] or "the source ticket").strip()
    except Exception:  # pragma: no cover - defensive
        return "the source ticket"


def detect_triggers(
    feature_text: str,
    jira_text: str = "",
    ui_content: dict | None = None,
    openapi_text: str = "",
    images_present: bool = False,
) -> Triggers:
    """Scan the ticket for the API / user-facing-UI standing-rule triggers.

    ``ui_content`` (a live UI extraction) and ``images_present`` (screenshots or
    mockups on the ticket) are themselves proof of a user-facing screen, so the
    UI trigger fires even when the prose never says "screen".

    The API trigger fires on ONE strong signal, or on ``_API_WEAK_MIN`` distinct
    weak signals (then ``api_weak_only=True``), or when a real OpenAPI spec was
    supplied. Never raises -- any failure returns an all-false Triggers, which
    degrades to today's behaviour.
    """
    try:
        blob = "\n".join(t for t in (feature_text or "", jira_text or "") if t)
        strong = _evidence(blob, _API_STRONG_RE)
        weak = _evidence(blob, _API_WEAK_RE)
        has_spec = bool((openapi_text or "").strip())
        weak_only = not strong and not has_spec and len(weak) >= _API_WEAK_MIN
        api = bool(strong) or has_spec or weak_only
        api_evidence = strong or (weak if weak_only else [])
        if weak_only:
            logger.info(
                "standing rules: API trigger fired on WEAK signals only (%s) - the "
                "mandated API cases will be labelled circumstantial",
                ", ".join(api_evidence),
            )
        elif weak and not api:
            logger.debug(
                "standing rules: %d weak API signal(s) (%s) - below the %d-signal "
                "floor, no API rules forced",
                len(weak),
                ", ".join(weak),
                _API_WEAK_MIN,
            )

        ui_evidence = _evidence(blob, _UI_RE)
        has_ui_content = bool(ui_content and not ui_content.get("error"))
        spec_hint = ""
        for token in re.findall(r"https?://\S+", blob)[:20]:
            if looks_like_openapi_url(token):
                spec_hint = token[:_MAX_REF_CHARS]
                break
        open_questions = [
            m.group(0).strip()[:200]
            for m in list(_OPEN_QUESTION_RE.finditer(blob))[:_MAX_OPEN_QUESTIONS]
        ]
        return Triggers(
            api=api,
            api_evidence=api_evidence,
            api_weak_only=weak_only,
            ui=bool(ui_evidence) or has_ui_content or bool(images_present),
            ui_evidence=ui_evidence,
            error_flow=bool(_evidence(blob, _ERROR_FLOW_RE)),
            has_spec=has_spec,
            spec_hint=spec_hint,
            open_questions=open_questions,
        )
    except Exception:
        logger.exception("detect_triggers failed - no standing rule fires")
        return Triggers()


def assumed_label(source_ref: str, has_spec: bool, bilingual: bool = False) -> str:
    """The mechanical assumption/documentation label for an API case.

    Never raises.
    """
    try:
        ref = safe_ref(source_ref)
        if has_spec:
            return DOCUMENTED_EN.format(ref=ref)
        label = ASSUMED_EN.format(ref=ref)
        if bilingual:
            label = label + " | " + ASSUMED_AR.format(ref=ref)
        return label
    except Exception:  # pragma: no cover - defensive
        return ASSUMED_EN.format(ref="the source ticket")


def standing_checklist_lines(triggers: Triggers) -> list[tuple[str, str, str]]:
    """Mandated checklist lines as ``(line_id, text, subsystem)`` tuples.

    Three API lines always fire when the API trigger fires (status codes for
    success AND error, request design, response structure); a fourth error
    handling line is added only when the ticket actually names a failure flow, so
    a passing "API" mention does not inflate the checklist. One baseline UI line
    fires for any user-facing story. Written in EARS ubiquitous shape to match
    the rest of the Batch-2 checklist. Never raises.
    """
    out: list[tuple[str, str, str]] = []
    try:
        if triggers.api:
            if triggers.has_spec:
                source = "the linked OpenAPI/Swagger spec"
            else:
                source = (
                    "standard REST convention (ASSUMED - the ticket documents no "
                    "contract, so the case title must start with the literal marker "
                    + ASSUMED_MARKER
                    + ")"
                )
            out.append(
                (
                    "SR-API-1",
                    "The system shall return the correct STATUS CODES for both a "
                    "successful call and every error path the story implies (2xx on "
                    "success; 4xx for bad input, missing auth or a missing resource; "
                    "5xx surfaced as a user-visible failure, not a blank screen), "
                    f"asserting the exact numeric code per {source}.",
                    "backend",
                )
            )
            out.append(
                (
                    "SR-API-2",
                    "The system shall accept the documented REQUEST DESIGN -- "
                    "required headers (content type, authorization), the request body "
                    "shape, required vs. optional fields -- and shall reject a "
                    "request that omits a required field. Field names and types per "
                    f"{source}.",
                    "backend",
                )
            )
            out.append(
                (
                    "SR-API-3",
                    "The system shall return the documented RESPONSE STRUCTURE: every "
                    "field present, of the right type and nested as specified, with "
                    "unexpected nulls or missing fields treated as failures. Field "
                    f"names and types per {source}.",
                    "backend",
                )
            )
            if triggers.error_flow:
                out.append(
                    (
                        "SR-API-4",
                        "If the failure flow the story names occurs, then the system "
                        "shall return a structured error (code + message), leave "
                        "nothing half-committed, and behave predictably on retry.",
                        "backend",
                    )
                )
        if triggers.ui:
            out.append(
                (
                    "SR-UI-1",
                    "The system shall render the feature's main screen to baseline "
                    "build quality: every element from the design present and "
                    "correctly spelled, alignment and spacing matching the mockup, "
                    "nothing truncated or overlapping at the default window/device "
                    "size, interactive elements showing their hover/focus/disabled "
                    "states, and the screen still correct at the smallest supported "
                    "width.",
                    "ui",
                )
            )
    except Exception:
        logger.exception("standing_checklist_lines failed - returning partial result")
    return out


def format_standing_prompt_block(
    triggers: Triggers, source_ref: str = "", checklist_mode: bool = False
) -> str:
    """System-prompt clause for the standing rules. "" when nothing fired.

    Only the ticket REFERENCE (sanitised) and code constants reach the prompt --
    no untrusted ticket body.

    ``checklist_mode`` is True when the mandated lines were injected onto the
    Batch-2 atomic checklist. The wording then names the checklist ids and
    repeats Batch 2's "coverage is recomputed externally" contract instead of
    telling the model to produce cases "in addition to your category's normal
    output", which would have contradicted it. Never raises.
    """
    try:
        lines = standing_checklist_lines(triggers)
        if not lines and not triggers.open_questions:
            return ""
        if not lines:
            # No standing rule fired, but the ticket has UNRESOLVED questions. The
            # clarification instruction is the whole point of detecting them, so it
            # must still reach the model instead of being dropped with the block.
            return (
                "\n\n## UNRESOLVED QUESTIONS IN THE TICKET\n"
                "The ticket contains questions nobody has answered. Do NOT silently "
                "pick an answer. If a case depends on one, write the case for the "
                "most conservative reading and start its title with the literal "
                f"marker {CLARIFY_MARKER}.\n"
            )
        body = "\n".join(f"- [{lid}] {text}" for lid, text, _sub in lines)
        if checklist_mode:
            head = (
                "\n\n## STANDING RULES (mandatory)\n"
                "The ticket content triggered the rules below. They are ALREADY on "
                "the Atomic Requirements Checklist as lines whose ids start with "
                "RP-SR-. Cover the ones in YOUR category's scope; coverage is "
                "recomputed afterwards by the same INDEPENDENT matcher used for "
                "every other checklist line, so your `requirement_id` tag is "
                "advisory:\n"
            )
        else:
            head = (
                "\n\n## STANDING RULES (mandatory)\n"
                "The ticket content triggered the rules below. Produce at least one "
                "dedicated test case for each bullet that falls in your category's "
                "scope:\n"
            )
        tail = ""
        if triggers.api and not triggers.has_spec:
            tail = (
                "\n- The ticket documents NO API contract. Write the API cases "
                "against standard REST convention and put the literal marker "
                f"{ASSUMED_MARKER} at the START of each such case's title. Do NOT "
                "cite any RFC, standard number or external document - the system "
                "attaches the assumption label mechanically.\n"
            )
            if triggers.api_weak_only:
                tail += (
                    "- The API signals in this ticket are CIRCUMSTANTIAL (matched: "
                    + ", ".join(triggers.api_evidence[:5])
                    + "). If the story genuinely has no API surface, keep these "
                    "cases to ONE minimal case per bullet rather than expanding "
                    "them.\n"
                )
        elif triggers.api and triggers.has_spec:
            tail = (
                "\n- An OpenAPI/Swagger spec was supplied above: take field names, "
                "types and status codes from it and do NOT mark those cases "
                f"{ASSUMED_MARKER}.\n"
            )
        if triggers.open_questions:
            tail += (
                "\n- The ticket contains UNRESOLVED questions. Do NOT silently pick "
                "an answer. If a case depends on one, write the case for the most "
                "conservative reading and start its title with the literal marker "
                f"{CLARIFY_MARKER}.\n"
            )
        return head + body + tail
    except Exception:
        logger.exception("format_standing_prompt_block failed - returning ''")
        return ""


def annotate_assumed_cases(
    cases: list[TestCase], source_ref: str, has_spec: bool, bilingual: bool = False
) -> dict[str, str]:
    """Map tc_id -> Notes text for every case built on an assumed contract.

    Detection is mechanical: the case title carries the ``[ASSUMED]`` marker the
    prompt asked for. Nothing is mutated -- the caller stores the mapping on the
    suite so ``tools/xlsx_generator`` renders it in the existing Notes column.
    Returns {} when nothing is marked. Never raises.
    """
    notes: dict[str, str] = {}
    try:
        if not cases:
            return {}
        label = assumed_label(source_ref, has_spec, bilingual)
        for tc in cases:
            title = (getattr(tc, "title", "") or "").upper()
            if ASSUMED_MARKER in title:
                notes[tc.tc_id] = label
            elif CLARIFY_MARKER in title:
                notes[tc.tc_id] = (
                    "Depends on an UNRESOLVED question in "
                    f"{safe_ref(source_ref)} - confirm the intended behaviour "
                    "before executing."
                )
    except Exception:
        logger.exception("annotate_assumed_cases failed - no notes attached")
        return {}
    return notes


def standing_warning_section(
    triggers: Triggers, notes: dict[str, str], source_ref: str = ""
) -> str:
    """Advisory markdown for the standing rules. "" when nothing fired.

    Never raises.
    """
    try:
        if not triggers.fired and not triggers.open_questions:
            return ""
        lines = ["\n\n## Standing Rules (advisory)", ""]
        if triggers.api:
            src = (
                "the linked OpenAPI/Swagger spec"
                if triggers.has_spec
                else "standard REST convention"
            )
            lines.append(
                "- **API rules fired** (matched: "
                + ", ".join(triggers.api_evidence[:5] or ["an OpenAPI spec"])
                + ") - status-code, request-design and response-structure cases are "
                f"mandatory and were written against {src}."
            )
            if triggers.api_weak_only:
                lines.append(
                    "  - ⚠️ **The trigger was CIRCUMSTANTIAL.** No word like "
                    '"API", "endpoint" or "status code" appears on this ticket; the '
                    "rules fired on "
                    + ", ".join(f"`{e}`" for e in triggers.api_evidence[:5])
                    + ". If this story has no backend surface, ignore the API cases "
                    "and set `QA_STANDING_RULES=false` for this kind of ticket."
                )
            if not triggers.has_spec:
                lines.append(
                    "  - No contract is documented on the ticket. Cases marked "
                    f"`{ASSUMED_MARKER}` carry this note in the Excel **Notes** "
                    f"column: _{assumed_label(source_ref, False)}_"
                )
            if triggers.spec_hint:
                lines.append(
                    "  - A spec URL appears in the ticket text "
                    f"(`{triggers.spec_hint}`). Spec ingestion is always on, so "
                    "if the API cases were not written from the contract the "
                    "URL was not recognised as an OpenAPI/Swagger link, or the "
                    "fetch failed."
                )
        if triggers.ui:
            lines.append(
                "- **User-facing screen detected** - a baseline UI build-quality case "
                "is mandatory (elements present, spelling, alignment, truncation, "
                "interactive states, smallest supported width)."
            )
        if triggers.open_questions:
            lines.append(
                "- **Unresolved questions found in the ticket / comments - these were "
                "NOT answered by the generator:**"
            )
            for q in triggers.open_questions[:5]:
                # UNTRUSTED, attacker-writable ticket/comment prose. safe_display
                # strips the markdown structure it could otherwise forge inside
                # this section, and the quotes make the provenance obvious.
                safe = safe_display(q)
                if safe:
                    lines.append(f'  - "{safe}"')
            lines.append(
                f"  Resolve them before executing any case marked `{CLARIFY_MARKER}`."
            )
        if notes:
            lines.append(
                "- Cases carrying an assumption / clarification note: "
                + ", ".join(sorted(notes)[:20])
                + (" ..." if len(notes) > 20 else "")
            )
        return "\n".join(lines)
    except Exception:
        logger.exception("standing_warning_section failed - returning ''")
        return ""
