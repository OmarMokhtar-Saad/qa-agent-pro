"""Moved from handlers (refactor P2): image_gate."""

from __future__ import annotations

import hashlib
import logging
from typing import NamedTuple
from urllib.parse import urlparse

from config.settings import settings
from handlers import _common, _elicit, jira
from tools.jira_mcp import _valid_issue_key
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# Jira image gate (QA_IMAGE_GATE_ENABLED) -- Approach C, TWO BEATS
#
# BEAT 1 fires BEFORE any fetch, from handle_prepare_test_cases, for a Jira
# source with no source_plan: it discloses that this server cannot read images
# out of Jira and collects a plan (attach / capture / both / text-only). BEAT 2
# fires on the SECOND call, once the ticket is in hand, ONLY when the ticket
# revealed images that nothing supplied -- and it NAMES the screens using the
# ticket's own labels/filenames.
#
# Why a gate at all: tools/jira_mcp makes no outbound HTTP request by hard rule
# and the Atlassian MCP server returns attachment METADATA only, so the existing
# disclosure could only ever be POST-HOC -- appended to a payload the tester's
# chat model had already been told to generate from. Moving it to the FRONT is
# the whole point.
#
# REACHABILITY: both beats live in handle_prepare_test_cases, and
# handle_generate_test_cases RE-ROUTES into it whenever the generation mode
# resolves to host (hardcoded true since 2026-08-01), so the gate fires on
# qa_generate_test_cases too. That tool therefore forwards the same four gate
# arguments -- otherwise the only way to answer the question would be to call a
# DIFFERENT tool, which a client that auto-cancels dialogs cannot recover from.
#
# Every ask uses the three-tier fallback the rest of this module uses (enum
# dialog -> free text -> markdown the host relays), because Cursor 3.12
# auto-cancels enum dialogs: a gate that dead-ends is worse than no gate.
# --------------------------------------------------------------------------- #

_IMAGE_SOURCE_PLANS = ("jira", "jira_attach", "jira_device", "jira_both", "device")

# Label -> plan, mirroring _TC_SOURCE_LABELS' shape so both menus behave alike.
_IMAGE_SOURCE_LABELS = {
    "Ticket text only -- no screens matter here": "jira",
    "I will attach the screenshots to this chat": "jira_attach",
    "Capture the screens from a connected device": "jira_device",
    "Both -- attach some AND capture from a device": "jira_both",
    "Device screens only -- ignore the ticket text": "device",
}

# STABLE MARKERS (2026-08-31). Two release-gate checks used to assert
# on raw sentences from the strings below. That makes a copy edit
# indistinguishable from a regression at the gate -- exactly the false
# alarm the release-verification work was undertaken to remove. Naming
# the fragments does not freeze the wording: it freezes the PHRASE the
# checker keys on, which a rewrite may carry unchanged. Dropping one is
# then a deliberate edit to a named symbol, not an accident.
_MARK_UNREADABLE_ANSWER = "could not read the answer"
_MARK_UNNAMED_REMAINDER = "does not name the remaining"

# The line that is ALWAYS shown -- in both beats and in the markdown fallback.
_IMAGE_GATE_LINE = (
    "**I cannot read images out of Jira -- only text.** Jira is read through "
    "your own Atlassian MCP connection, which returns attachment metadata "
    "(filenames) but never the image bytes. If this ticket's requirements live "
    "in mockups or screenshots, the cases will be written from the ticket TEXT "
    "alone unless you give me the images another way."
)

# P0-3: `image_gate_ack=true` is the AGENT's say-so that the tester agreed to
# skip the screens. Beat 2 asks the tester directly; only this skip label,
# CHOSEN by them, clears it. There is no agent-side override.
_IMAGE_GATE_SKIP_LABEL = "Skip the screens -- generate from the ticket text only"
_IMAGE_GATE_ATTACH_LABEL = "I will attach or capture the screens first"


async def _confirm_image_gate_skip(choose: _common.ChooseCb, beat2: str) -> str:
    """Ask the TESTER whether beat 2 may be skipped. Returns "" only on a CHOSEN
    skip; otherwise ``beat2`` plus a line saying why the ack did not clear it.
    Never raises (_elicit_choice degrades to UNAVAILABLE)."""
    res = await _elicit._elicit_choice(
        choose,
        "This ticket has screens I was not given. Generate from the ticket text only?",
        [_IMAGE_GATE_SKIP_LABEL, _IMAGE_GATE_ATTACH_LABEL],
    )
    if res.status == _common.CHOSEN and res.value == _IMAGE_GATE_SKIP_LABEL:
        return ""
    if res.status == _common.CHOSEN:
        why = "The tester chose to attach the screens first."
    elif res.status == _common.DECLINED:
        why = "The tester dismissed the confirmation dialog."
    else:
        why = (
            "The tester could not be asked (this client shows no dialog, or no "
            "answer arrived), and `image_gate_ack=true` alone cannot skip the "
            "screens. Attach them instead."
        )
    return f"{beat2}\n\n{why}"


_IMAGE_PLAN_ALIASES = {
    "text": "jira",
    "text_only": "jira",
    "ticket": "jira",
    "ticket_only": "jira",
    "ticket_text": "jira",
    "ticket_text_only": "jira",
    "no_screens": "jira",
    "jira_only": "jira",
    "attach": "jira_attach",
    "attachment": "jira_attach",
    "attachments": "jira_attach",
    "screenshot": "jira_attach",
    "screenshots": "jira_attach",
    "chat": "jira_attach",
    "capture": "jira_device",
    "mobile": "jira_device",
    "jira_mobile": "jira_device",
    "device_only": "device",
    "device_screens": "device",
    "device_screens_only": "device",
    "both": "jira_both",
    "attach_capture": "jira_both",
}

# 2026-08-31: the MENU ORDINALS, in the order _image_gate_menu_markdown prints
# them. A tester answering the tier-2 free-text prompt types "1", and a host
# relaying the markdown menu may echo the number the tester picked rather than
# the bracketed token. Both used to normalize to "" and cost another round trip.
_IMAGE_PLAN_ORDINALS = {
    "1": "jira",
    "2": "jira_attach",
    "3": "jira_device",
    "4": "jira_both",
    "5": "device",
}


def _normalize_source_plan(plan: str) -> str:
    """Coerce a host/tester-supplied ``source_plan`` to one of
    _IMAGE_SOURCE_PLANS, or "" when it is unusable -- which RE-ASKS rather than
    guessing which channel the tester meant. Never raises.

    2026-08-31: accepts the three shapes a real caller sends that this used to
    refuse -- the five _IMAGE_SOURCE_LABELS verbatim (a host relaying the tier-3
    markdown may echo the LABEL the tester clicked, not the bracketed token),
    the menu ordinals 1-5, and an "option N" prefix. The labels are matched on
    the RAW lowercased string, before the separator mangling turns their "--"
    into underscores."""
    try:
        raw = (plan or "").strip().lower()
        if not raw:
            return ""
        for label, mapped in _IMAGE_SOURCE_LABELS.items():
            if raw == label.strip().lower():
                return mapped
        for _lead in ("option ", "option_", "#"):
            if raw.startswith(_lead):
                raw = raw[len(_lead) :].strip()
        raw = raw.strip(".) ")
        if raw in _IMAGE_PLAN_ORDINALS:
            return _IMAGE_PLAN_ORDINALS[raw]
        for ch in (" ", "-", "+", "/", ".", ":", "\t"):
            raw = raw.replace(ch, "_")
        while "__" in raw:
            raw = raw.replace("__", "_")
        raw = raw.strip("_")
        raw = _IMAGE_PLAN_ALIASES.get(raw, raw)
        return raw if raw in _IMAGE_SOURCE_PLANS else ""
    except Exception:
        logger.debug("_normalize_source_plan failed for %r", plan, exc_info=True)
        return ""


def _gate_jira_source(url: str) -> bool:
    """True when *url* is a Jira source BEAT 1 should fire on.

    _looks_like_jira_host only matches atlassian.net / jira.* / .jira.*, so a
    self-hosted Jira on a plain corporate domain (tickets.acme.com) would never
    see the disclosure even though it applies verbatim -- so the configured
    JIRA_BASE_URL host counts too. Never raises."""
    try:
        if jira._looks_like_jira_host(url):
            return True
        base = (getattr(settings, "jira_base_url", "") or "").strip()
        if not base:
            return False
        base_host = (urlparse(base).hostname or "").lower()
        host = (urlparse(url).hostname or "").lower()
        return bool(base_host and host and host == base_host)
    except Exception:
        logger.debug("_gate_jira_source failed for %r", url, exc_info=True)
        return False


def _rejected_plan_note(raw: str) -> str:
    """One line naming a `source_plan` this server could not recognise, or ""
    when there is nothing to disclose. Host-supplied text, so it is stripped of
    line breaks and backticks and hard-capped before it is echoed. Never
    raises."""
    try:
        val = (raw or "").strip()
        if not val or _normalize_source_plan(val):
            return ""
        for ch in ("\r", "\n", "`"):
            val = val.replace(ch, " ")
        val = " ".join(val.split())[:60]
        if not val:
            return ""
        return (
            "> \u26a0\ufe0f  **I did not recognise `source_plan='" + val + "'`, so "
            "it was ignored** -- I am asking the question below instead of "
            "guessing which channel you meant. It must be EXACTLY one of "
            "`jira`, `jira_attach`, `jira_device`, `jira_both`, `device`.\n\n"
        )
    except Exception:  # pragma: no cover - a disclosure never breaks a prepare
        logger.debug("_rejected_plan_note failed for %r", raw, exc_info=True)
        return ""


def _gated_not_unsupported() -> bool:
    """True when THIS client's dialogs are switched off by the strike gate
    rather than absent from the client.

    Both states reach _image_gate_menu_markdown as "unavailable", and only
    one of them is a statement about the client's capabilities. Never raises
    -- a read failure means "not gated", which falls back to the wording that
    was already there.
    """
    try:
        return bool(_elicit.elicit_client_gated())
    except Exception:
        logger.debug("gated-cause read failed", exc_info=True)
        return False


def _image_gate_menu_markdown(elicit_status: str = "", rejected_plan: str = "") -> str:
    """BEAT 1 tier-3 fallback: the menu as an instruction to the HOST assistant.

    Same shape as _tc_source_menu_markdown -- editors render a structured
    multiple-choice question reliably where an MCP elicitation dialog may be
    auto-dismissed. NEVER a dead end: every option names the exact re-call.

    2026-08-09 (Batch 3, FIX 3, review H4): *elicit_status* is the
    "<enum>/<text>" outcome from _elicit_source_plan_status, used ONLY to word
    the CAUSE of this fallback correctly. This menu is relayed whenever no plan
    resolved, which includes a call arriving with no elicitation channel at
    all and the tester DECLINING the dialog -- so asserting "your client has
    no elicitation support" unconditionally would be an over-claim, in the one
    batch dedicated to removing over-claims. 2026-08-14: the "disabled" label
    no longer means a server setting. QA_MCP_ELICIT_ENABLED was DELETED on
    2026-08-13 and hardcoded ON, so _elicit_enabled() is the True constant and
    the only surviving cause is both callbacks being absent.
    Defaults to "" (cause unknown), which words it as a plain statement of fact
    with no cause attributed.

    2026-09-01: the "unavailable" label is NOT proof of a client limitation.
    A client gated by ``elicit_client_gated()`` arrives with both callbacks
    None and is labelled ``unavailable/unavailable`` ON PURPOSE, so the
    mcp_image_gate_beat1 audit row stays byte-identical (see
    _elicit_source_plan_status). Translating that into "your MCP client does
    not support elicitation dialogs" is false for the very client that earns
    the gate -- Cursor SHOWED the dialog and dismissed it -- and it is the
    over-claim this function was written to remove. The gate is asked
    directly rather than inferred from a label that never carried capability.
    Never raises."""
    return (
        "## Before I read the ticket: how do I get its screens?\n\n"
        + _rejected_plan_note(rejected_plan)
        + "> ℹ️ "
        + _IMAGE_GATE_LINE
        + "\n\n"
        + _IMAGE_GATE_OPTIONS
        + _image_gate_warnings(_image_gate_cause(elicit_status))
    )


def _image_gate_cause(elicit_status: str) -> str:
    """Wording of WHY the BEAT 1 dialog fell back to the text menu (see
    _image_gate_menu_markdown for why each label is worded as it is)."""
    _cause = "could not be shown to you inline"
    if elicit_status.endswith("/chosen"):
        # 2026-08-31: the tester ANSWERED -- the text tier reported CHOSEN and
        # the caller still got "" back, which can only mean the reply did not
        # normalize to one of the five plans. Reporting a client limitation here
        # is an over-claim (the dialog tiers are not why we are in the fallback)
        # and it hides the one thing the tester can act on: say it again, in the
        # server's own words. Checked FIRST because such a status also carries
        # the enum tier's "unavailable"/"declined" label, which used to win.
        _cause = (
            f"was answered, but I {_MARK_UNREADABLE_ANSWER} as one of the "
            "five plans below"
        )
    elif elicit_status.startswith("disabled"):
        _cause = (
            "was not shown inline because this call arrived with no elicitation "
            "channel (your client offered none for this call)"
        )
    elif "declined" in elicit_status:
        _cause = "was shown inline and dismissed"
    elif "unavailable" in elicit_status and _gated_not_unsupported():
        _cause = (
            "was not asked again -- an earlier dialog this session was dismissed "
            "without an answer, so I stopped spending round trips on dialogs"
        )
    elif "unavailable" in elicit_status:
        _cause = (
            "could not be shown inline because your MCP client does not support "
            "elicitation dialogs"
        )
    return _cause


_IMAGE_GATE_OPTIONS = (
    "Present EXACTLY these five options to the user as a multiple-choice "
    "question (use your ask-user/questions UI, not prose), then call the "
    "SAME tool again with the SAME `feature_or_url` plus `source_plan` set "
    "to the value in brackets. Do NOT fetch the ticket first, and do not "
    "invent other options. Do NOT ask the tester additional questions about "
    "which device or how many screens — pass what they gave you and let the "
    "server default the rest.\n\n"
    "1. **Ticket text only** [`jira`] -- no screens matter here. If the "
    "fetched ticket then turns out to contain screens, I will name them and "
    "ask ONCE more; send `image_gate_ack=true` alongside `source_plan` to "
    "skip that second ask.\n"
    "2. **I'll attach the screenshots to this chat** [`jira_attach`] -- "
    "attach them, then also pass `attached_image_count=<how many>`\n"
    "3. **Capture them from a connected device** [`jira_device`] -- call "
    "`qa_capture_screens` first, then pass its `capture_ids`\n"
    "4. **Both** [`jira_both`] -- chat attachments AND captured screens\n"
    "5. **Device screens only** [`device`] -- ignore the ticket text\n\n"
)


def _image_gate_warnings(cause: str) -> str:
    """The round-trip notice and the never-guess warning of the BEAT 1 menu."""
    return (
        "> \u23f1\ufe0f  **This reply cost a round trip.** My question "
        f"{cause}, so I had to hand it to you as text. NOTHING has been "
        "prepared -- no fetch, no generation -- so nothing was wasted except "
        "this turn. To avoid it next time, ask the user where a Jira ticket's "
        "screens come from BEFORE your first `qa_prepare_test_cases` / "
        "`qa_generate_test_cases` call and pass `source_plan` on that first "
        "call.\n\n"
        "> \u26d4 **Only pass `source_plan` if the USER answered -- never guess "
        "it, and never send `image_gate_ack=true` unless the user explicitly "
        "said the screens do not matter.** Guessing `source_plan='jira'` skips "
        "this ask, and pairing it with `image_gate_ack=true` ALSO skips the "
        "second, informed ask that NAMES the screens the fetched ticket really "
        "has -- that is a quieter gate, not a cheaper one, and the cases get "
        "written from ticket text that may not contain the requirements.\n\n"
        "Many images are fine: attach as many as the user has, and "
        "`qa_capture_screens` captures screen after screen."
    )


async def _elicit_source_plan_status(
    choose: _common.ChooseCb, ask_text: _elicit.AskCb
) -> tuple[str, str]:
    """BEAT 1 elicitation, returning the plan AND why it resolved that way.

    Tier 1 the enum dialog; tier 2 a free-text prompt (Cursor 3.12 auto-cancels
    enum dialogs but still renders text prompts); tier 3 is the caller relaying
    _image_gate_menu_markdown when the plan comes back "". Never raises, never
    dead-ends, and the ORDER and NUMBER of elicitation attempts is exactly what
    it was before this function existed -- only the reported label is new.

    2026-08-09 (Batch 3, FIX 3): the second return value is the DIAGNOSTIC this
    beat lacked. The audit row for the 08:18:23 run said only
    {"resolved": false, "plan": ""}, and BOTH degradation paths log at DEBUG
    while the installed log file runs at INFO, so there was no way to tell why.

    WHY THE LABEL IS NOT JUST THE ChoiceResult (review H3): _elicit_choice returns
    UNAVAILABLE for a MISSING callback *and* for a raising ctx.elicit (the module
    comment above ChoiceResult already flags this), and the elicitation gate
    used to default FALSE -- so on a stock install the label would read
    "unavailable/unavailable", indistinguishable from the client-capability limit
    it exists to identify. The flag/callback state is the only thing that can tell
    those apart, so an unresolved gate on an install whose _elicit_enabled()
    seam is off is reported as
    "disabled/disabled" and everything else keeps the honest
    "<enum-status>/<text-status>" form ("unavailable/unavailable" for a client
    that cannot show dialogs, "declined/..." for a tester who dismissed one)."""
    _disabled = _elicit._elicitation_disabled(choose, ask_text)
    picked = await _elicit._elicit_choice(
        choose,
        "I cannot read images out of Jira -- where do this ticket's screens come from?",
        list(_IMAGE_SOURCE_LABELS),
    )
    if picked.status == _common.CHOSEN:
        plan = _IMAGE_SOURCE_LABELS.get(picked.value or "", "")
        if plan:
            return plan, f"{picked.status}/skipped"
    asked = await _elicit._elicit_text(
        ask_text,
        "I cannot read images out of Jira, only text. Where do the screens come "
        "from? Reply with one of: jira (text only), jira_attach (you attach them "
        "here), jira_device (capture from a connected device), jira_both, "
        "device.",
    )
    return _text_tier_result(picked.status, asked, _disabled)


def _text_tier_result(enum_status: str, asked, disabled: bool) -> tuple[str, str]:
    """Plan and status once the free-text tier has answered (or not)."""
    status = f"{enum_status}/{asked.status}"
    if asked.status == _common.CHOSEN:
        plan = _normalize_source_plan(asked.value or "")
        if plan:
            return plan, status
        # Answered but unusable -- report it as ANSWERED, never as "disabled".
    elif disabled:
        status = "disabled/disabled"
    logger.info(
        "image gate beat 1: no source plan from elicitation (%s) -- relaying the "
        "markdown menu instead; the host must re-call with source_plan",
        status,
    )
    return "", status


async def _elicit_source_plan(choose: _common.ChooseCb, ask_text: _elicit.AskCb) -> str:
    """The plan-only form, for callers that do not need the status.

    A thin delegate to _elicit_source_plan_status, so the signature every
    existing caller and test uses is unchanged. NOTE for future maintainers: the
    beat-1 call site uses the STATUS form, so a test that monkeypatches beat-1
    elicitation must patch BOTH names or it silently asserts nothing (this is why
    tests/test_jira_image_gate.py patches both). Never raises, never dead-ends."""
    plan, _status = await _elicit_source_plan_status(choose, ask_text)
    return plan


_IMAGE_NAME_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._ -"
)


def _safe_image_name(raw) -> str:
    """Sanitize ONE ticket-supplied image name before beat 2 interpolates it.

    tools/jira_mcp._extract_image_attachments produces attachment filenames as a
    bare ``str(att.get("filename") or "attachment")`` off the HOST-submitted --
    and explicitly UNTRUSTED -- issue payload: no charset gate, no length cap.
    (Only `description_image_labels` is charset-gated there.) Beat 2 puts those
    names inside an instruction-shaped markdown block that tells the host what to
    do next, so they are allowlisted to [A-Za-z0-9._ -] and capped at 60 chars
    HERE, at the point of use -- jira_mcp is deliberately not modified by this
    plan. Never raises."""
    try:
        return "".join(
            ch for ch in str(raw or "") if ch in _IMAGE_NAME_ALLOWED
        ).strip()[:60]
    except Exception:
        return ""


def _grounding_source_note(
    source_text: str, url_content: dict | None, openapi_text=None
) -> str:
    """One line naming WHAT the suite will be generated from.

    2026-09-01. The prepare reply disclosed a great deal -- images it could
    not read, the ticket snapshot, condensed payloads -- but never the one
    fact that would have caught the worst defect found so far: a Jira BOARD
    url was scraped as an ordinary web page and its ``<title>`` (the single
    word "Jira") became the whole feature description, with eight categories
    generated from it. Nothing in the reply was false; the source was simply
    never named, so the error was invisible until someone read the cases.

    Static, derived from what grounding already returned -- no fetch, no
    model call. Never raises: a note is a disclosure, and failing to build
    one must never cost the tester their suite.
    """
    try:
        content = url_content or {}
        if openapi_text:
            where = jira._safe_host(source_text)
            return (
                "> \U0001f9ed Grounded on the **OpenAPI/Swagger spec**"
                + (f" at `{where}`" if where else "")
                + "."
            )
        if content.get("source") == "atlassian_mcp":
            return _grounding_note_atlassian(content)
        if content.get("content"):
            where = jira._safe_host(source_text)
            return (
                "> \U0001f9ed Grounded on the **web page**"
                + (f" at `{where}`" if where else " you gave me")
                + " -- not a Jira ticket. If you meant a ticket, paste its "
                "`/browse/KEY-123` url and I'll read that instead."
            )
        return (
            "> \U0001f9ed Grounded on the **feature description you gave me** "
            "-- no ticket and no page were read."
        )
    except Exception:
        logger.debug("grounding source note failed", exc_info=True)
        return ""


def _grounding_note_atlassian(content: dict) -> str:
    """The grounding note for a ticket read through the host's Atlassian MCP."""
    key = _valid_issue_key(content.get("issue_key"))
    named = f"Jira ticket `{key}`" if key else "the Jira ticket"
    note = (
        f"> \U0001f9ed Grounded on {named}, read through YOUR Atlassian "
        "MCP connection -- this server never fetched it."
    )
    # Audit F5 (verified open 2026-09-02): a payload with NO `key`
    # proceeds by design, but the reply used to name the link's key as
    # if the payload had confirmed it. Say that it was assumed.
    if key and content.get("issue_key_from_url"):
        note += (
            " The JSON handed back carried no `key` field, so the key "
            "is taken from the link -- if the ticket you meant is a "
            "different one, re-run `getJiraIssue` and pass its raw result."
        )
    return note


# 2026-09-02: `_server_fetched_image_note` DELETED. It rendered the
# QA_JIRA_ATTACHMENT_FETCH_ENABLED disclosure from `images_fetched_server_side`,
# which nothing has written since batch D1 (2026-08-15) deleted the fetch, so
# its two tester-facing strings named a deleted flag and could never be shown.
# A revived fetch (docs/RETIRED_CAPABILITIES.md §1) writes its own note.


def _unreadable_images_note(
    url_content: object, *, attested: int = 0, captured: int = 0
) -> str:
    """The prepare-reply notice for ticket images this server could NOT read.

    Returns "" when there is nothing left to say. Split out of
    handle_prepare_test_cases (Batch C, C1) for two reasons: it is now
    CONDITIONAL prose rather than one fixed string, and inline it was reachable
    only through a whole prepare, so the falsehood below shipped untested.

    Since the R3 completeness fix a PARTIAL server-side fetch leaves
    ``images_unavailable`` SET on purpose -- that is what keeps the image gate's
    completeness arm armed -- and the old blanket wording then stated two
    falsehoods in the SAME reply: it counted every attachment as unread when some
    had just been fetched, and it promised a "TEXT only" generation directly
    under _server_fetched_image_note, which lists the screens attached to that
    very reply. So on a partial fetch the count, the names and the wording narrow
    to the REMAINDER -- matched by FILENAME against what actually arrived, not by
    position, because jira_attachments._ordered gives inline images the budget
    first and the ticket's order is therefore not the fetch order.

    With nothing fetched the original wording is returned unchanged. Filenames
    are ticket-supplied and untrusted, so they go through _safe_image_name
    exactly as beat 2's do (the inline version interpolated them raw).

    2026-08-09 (review M2/W2, NARROWED the same day by review L2): the OTHER two
    intake channels are subtracted from the remainder, because a note calling a
    screen unreadable while it rides on the very same request is simply false.
    They are NOT equivalent evidence and are still not treated as such:
    ``captured`` is this server's own observation (it handed those bytes to the
    chat client) and rebuts the "TEXT only" claim on EVIDENCE, while ``attested``
    is host-ASSERTED with no server-side evidence at all -- it can never claim
    the ticket's own screens arrived, and never upgrades the wording to say they
    did.

    What L2 changed is only what this server may ASSERT AGAINST ITSELF. A flat
    "generated from the ticket TEXT only" is a claim about the WHOLE payload, and
    it is false the moment ANY image rides on the request -- so on the zero-fetch
    arm an attestation now retires that sentence too, exactly as it already
    retired the "generated WITHOUT the remaining screen(s)" clause on the partial
    arm. The weaker/stronger ordering is unchanged; the stronger claim simply no
    longer survives on the weaker branch. On EVERY arm the screens the fetch
    could not get are still NAMED. Never raises."""
    try:
        return _unreadable_images_text(url_content, attested, captured)
    except Exception:
        logger.debug("_unreadable_images_note failed", exc_info=True)
        return ""


def _unreadable_images_text(url_content: object, attested: int, captured: int) -> str:
    """Body of `_unreadable_images_note`; may raise, the caller swallows."""
    uc = url_content if isinstance(url_content, dict) else {}
    names = [
        _safe_image_name(a.get("filename")) or "attachment"
        for a in (uc.get("image_attachments") or [])
        if isinstance(a, dict)
    ]
    if not names:
        # Nothing was ever disclosed, so there is no unreadable image to
        # report (review N2): the old inline version could only run with a
        # non-empty attachment list, and a "0 image attachment(s)" paragraph
        # would be a fresh falsehood.
        return ""
    # SERVER-OBSERVED vs HOST-ASSERTED (review W2, narrowed by L2 on
    # 2026-08-09): only `captured` REBUTS a TEXT-only claim on evidence --
    # only those bytes demonstrably left this server, so only they earn the
    # "check they cover the same ground" wording. `attested` cannot claim
    # equivalence and never will; it only stops this server ASSERTING the
    # stronger negative -- that no image reached the model at all -- about a
    # request whose own IMAGE_JOB describes the attachments. It joins
    # `_other` for the partial-fetch remainder AND for the zero-fetch
    # narrowing below.
    observed = _clamped_count(captured, hi=99)
    other = _clamped_count(attested, hi=99) + observed
    fetched = _clamped_count(uc.get("images_fetched_server_side"), hi=999)
    if not fetched:
        return _zero_fetch_unreadable_note(names, observed, other)
    return _partial_fetch_unreadable_note(uc, names, fetched, other)


def _zero_fetch_unreadable_note(names: list, observed: int, other: int) -> str:
    """The unreadable-images note when the server fetched none of the images."""
    if observed >= len(names):
        return (
            "> \u2139\ufe0f This ticket's "
            f"{len(names)} image attachment(s) could NOT be read from "
            f"Jira by this server ({', '.join(names) or 'unnamed'}), but "
            f"{observed} device screen(s) this server captured ride on "
            "this same request \u2014 so this is NOT a TEXT-only "
            "generation. Check they cover the same ground, and attach "
            "the ticket's own screenshots if they matter."
        )
    if other:
        # 2026-08-09 (review L2). The STRONGER claim must not survive
        # on the WEAKER branch: the partial-fetch arm already lets
        # an ATTESTED image retire "generated WITHOUT the remaining
        # screen(s)", so leaving the flat "TEXT only" standing here
        # asserted something stronger still -- that no image reached the
        # model at all -- while this very payload's IMAGE_JOB describes
        # the attached screenshots. The unread attachments stay NAMED
        # and the explanation is unchanged; only the claim narrows to
        # what this server can actually stand behind.
        return (
            "> \u2139\ufe0f This ticket has "
            f"{len(names)} image attachment(s) that could NOT be read "
            f"({', '.join(names) or 'unnamed'}): Jira is now read "
            "through your own Atlassian MCP connection, which returns "
            f"attachment metadata but not the image bytes. {other} "
            "image(s) from the other intake channels (chat attachments "
            "and/or captured device screens) ride on this same request, "
            "so this is NOT a TEXT-only generation \u2014 check they "
            "cover the same ground, and attach the ticket's own "
            "screenshot(s) if they matter."
        )
    return (
        "> \u2139\ufe0f This ticket has "
        f"{len(names)} image attachment(s) that could NOT be read "
        f"({', '.join(names) or 'unnamed'}): Jira is now read through "
        "your own Atlassian MCP connection, which returns attachment "
        "metadata but not the image bytes. The test cases below are "
        "generated from the ticket TEXT only \u2014 attach the "
        "screenshot(s) to this chat if they matter."
    )


def _partial_fetch_unreadable_note(
    uc: dict, names: list, fetched: int, other: int
) -> str:
    """The unreadable-images note for the remainder of a partial fetch."""
    # Both sides normalise IDENTICALLY (review N3): a filename that
    # sanitizes to "" becomes "attachment" in `names`, so it must do the same
    # here or a screen that DID arrive would be reported as outstanding.
    got = {
        _safe_image_name((i or {}).get("filename")) or "attachment"
        for i in (uc.get("images") or [])
        if isinstance(i, dict)
    }
    rest = [n for n in names if n not in got] or names[fetched:]
    if len(rest) > len(names) - fetched:
        # Under-matching (duplicate or unrecognisable filenames) must never
        # let the reported remainder exceed what is actually outstanding.
        rest = names[fetched:]
    if not rest:
        # Everything the ticket named is attached to this reply. The fetch
        # note above already said so, and a second paragraph claiming
        # unreadable images would contradict it.
        return ""
    if other >= len(rest):
        # Covered by the other channels (review M2). The FETCH still fell
        # short and that stays disclosed, screens still NAMED -- what is
        # dropped is the false "generated WITHOUT the remaining screen(s)"
        # claim, replaced by the check the tester can actually act on.
        return (
            "> \u2139\ufe0f "
            f"{len(rest)} of this ticket's {len(names)} image attachment(s) "
            f"could NOT be fetched by this server ({', '.join(rest)}) "
            f"\u2014 it got {fetched} of them (named above). {other} "
            "image(s) from the other intake channels (chat attachments "
            "and/or captured device screens) ride on this same request, so "
            "the cases below are NOT generated without those screens "
            "\u2014 check they are the same ones."
        )
    return (
        "> \u2139\ufe0f "
        f"{len(rest)} of this ticket's {len(names)} image attachment(s) "
        f"could NOT be read ({', '.join(rest)}) \u2014 this server fetched "
        f"{fetched} of them (named above) and could not get the rest. The "
        "test cases below are generated WITHOUT the remaining screen(s) "
        "\u2014 attach those to this chat if they matter."
    )


def _ticket_image_evidence(url_content: dict | None) -> tuple:
    """What the FETCHED ticket reveals about images: ``(count, names, kind)``.

    kind is "attachments" (image attachment metadata came back, i.e.
    images_unavailable), "embedded" (the description embeds image refs),
    "unknown" (the payload carried no `attachment` field at all -- NOT the same
    fact as "no attachments"), or "" when the ticket revealed nothing.

    Precedence matches the existing post-hoc notice exactly. *names* are the
    ticket's OWN filenames/labels, so beat 2 can name the screens -- passed
    through _safe_image_name because attachment FILENAMES arrive from jira_mcp
    unsanitized (see that helper). Pure, never raises."""
    try:
        uc = url_content if isinstance(url_content, dict) else {}
        if uc.get("images_unavailable"):
            names = [
                _safe_image_name(a.get("filename")) or "attachment"
                for a in (uc.get("image_attachments") or [])
                if isinstance(a, dict)
            ][:8]
            return (len(names) or 1, names, "attachments")
        refs = int(uc.get("description_image_refs") or 0)
        if refs > 0:
            names = [
                _safe_image_name(x)
                for x in (uc.get("description_image_labels") or [])[:8]
                if _safe_image_name(x)
            ]
            return (refs, names, "embedded")
        if uc.get("attachments_unknown"):
            return (0, [], "unknown")
        return (0, [], "")
    except Exception:
        logger.debug("_ticket_image_evidence failed", exc_info=True)
        return (0, [], "")


def _clamped_count(raw, *, lo: int = 0, hi: int = 99, default: int = 0) -> int:
    """Coerce an UNTRUSTED count to a sane int inside [lo, hi]. Never raises.

    Both image gates read counts they did not produce -- `attached_image_count`
    is HOST-supplied and the prepare stamps are envelope data -- and both
    interpolate them into tester-facing prose. A string, None, a float or an
    absurd 2**40 must degrade to a number the wording can safely carry rather
    than raise inside a disclosure helper. Plain arithmetic, in the same spirit
    as the set-based sanitizers above: `re` is deliberately not imported here.
    """
    try:
        n = int(raw)
    except Exception:
        n = int(default)
    return max(lo, min(hi, n))


def _jira_image_cap() -> int:
    """The ONE reading of JIRA_MAX_IMAGES shared by the two halves of the
    image-COMPLETENESS contract.

    Scope, precisely (review N5): this used to cover TWO places that decide
    whether the screens on hand are "all of them" -- the server-side attachment
    fetch and _image_gate_second_beat. Batch D1 (2026-08-15) deleted the fetch,
    and tools/jira_attachments.py with it, so only the gate reads this clamp
    today. It is KEPT and kept SHARED on purpose: a revived fetch
    (docs/RETIRED_CAPABILITIES.md) must clamp JIRA_MAX_IMAGES exactly the way
    the gate does, and clamping it differently in the two halves is the defect
    this function exists to prevent. The prepare payload's own image budget
    below is deliberately NOT rewired here; it answers a different question.

    Batch C, M1 (2026-08-09), the reasoning that still binds a revival: the
    fetch-completeness check and the beat-2 arithmetic in
    _image_gate_second_beat are two halves of ONE contract -- "how many screens
    will this server ever carry" -- and clamping the same setting differently in
    each would let a misconfigured install fetch a number the gate then refuses
    to call complete, or the reverse. lo=1 because a cap of zero makes
    "complete" vacuous; hi=20 because that is the bound the gate has always
    applied to a tester-facing demand. Never raises (see _clamped_count)."""
    return _clamped_count(
        getattr(settings, "jira_max_images", 3), lo=1, hi=20, default=3
    )


def _reprep_image_loss_refusal(
    *,
    prep_id: str,
    age_s: float,
    captured: int,
    attested: int,
    labels: list,
    recovered: int = 0,
    carries_captured: int = 0,
    carries_attested: int = 0,
    shortfall: int = 0,
    prior_captured: int | None = None,
    prior_attested: int | None = None,
) -> str:
    """REFUSE a re-prepare that would generate without screens the last one had.

    2026-08-09, from a live run: a prep carrying 2 captured device screens was
    followed 3 minutes later by a second prep for the SAME source with none, and
    all 8 categories were written from the imageless one. The generic
    duplicate-prep warning is dismissed by ``proceed_anyway=true`` -- which that
    host model sent -- so this refusal takes its OWN, SPECIFIC ack
    (``image_carry_ack=true``), exactly like ``volume_floor_ack`` on the submit
    side, and NAMES both flags together so a deliberate restart still costs one
    call rather than two refusals in a row.

    Describes the SHORTFALL, not the whole prior prep: anything this server
    already recovered off the carry-forward shelf is counted and excluded, so a
    partial recovery never reads as a total loss. The labels are the tester's own
    words -- UNTRUSTED text -- so they ride inside wrap_untrusted. The except
    returns a SHORTER refusal rather than "": a disclosure that cannot be
    rendered must never become a silent proceed."""
    try:
        counts = _refusal_counts(
            captured, attested, recovered, shortfall, (prior_captured, prior_attested)
        )
        carries = (
            _clamped_count(carries_captured, hi=99),
            _clamped_count(carries_attested, hi=99),
        )
        return _refusal_text(prep_id, age_s, labels, counts, carries)
    except Exception:
        logger.debug("_reprep_image_loss_refusal failed", exc_info=True)
        return (
            "\u26d4 A recent preparation for this exact source was grounded on "
            "screens that this call does not carry. Re-send them, or pass "
            "`image_carry_ack=true` to generate from the ticket text alone "
            "(`proceed_anyway=true` does not dismiss this)."
        )


class _RefusalCounts(NamedTuple):
    """The clamped counts behind one image-loss refusal (see UNITS below)."""

    recovered_cap: int
    prior_cap: int
    prior_att: int
    short_total: int


def _refusal_counts(
    captured: int,
    attested: int,
    recovered: int,
    shortfall: int,
    priors: tuple,
) -> _RefusalCounts:
    """Clamp and combine the counts the refusal arithmetic needs."""
    # UNITS (2026-08-09, adversarial review of 2dcdc73). Every count here
    # carries what it MEASURES in its own name, because the five bugs that
    # review found were all ONE variable silently changing meaning between
    # the arithmetic and the prose:
    #   gap_*      -- screens the prior prep had that this call does NOT
    #   prior_*    -- what the prior prep was grounded on, in TOTAL
    #   carries_*  -- what THIS call carries
    #   short_*    -- the residual after recovery and any surplus credit
    # `captured`/`attested` arrive as GAPS (kept for the existing call
    # sites); the prior TOTALS arrive separately and fall back to the gaps
    # for a direct caller that only has those.
    prior_captured, prior_attested = priors
    gap_cap = _clamped_count(captured, hi=99)
    gap_att = _clamped_count(attested, hi=99)
    recovered_cap = _clamped_count(recovered, hi=99)
    gap_total = gap_cap + gap_att
    prior_cap = _clamped_count(
        gap_cap if prior_captured is None else prior_captured, hi=99
    )
    prior_att = _clamped_count(
        gap_att if prior_attested is None else prior_attested, hi=99
    )
    # 2026-08-09 (review C1): the caller may have credited a channel SURPLUS
    # (screens this call carries beyond what the prior prep had on that
    # channel) that this helper cannot see, so it passes the authoritative
    # shortfall in. 0 means "work it out", which is the original behaviour
    # and what the direct-call tests exercise.
    short_total = _clamped_count(shortfall, hi=99) or max(1, gap_total - recovered_cap)
    return _RefusalCounts(recovered_cap, prior_cap, prior_att, short_total)


def _refusal_ago(age_s: float) -> str:
    """How long ago the prior preparation started, in words."""
    try:
        mins = max(0, int(float(age_s or 0) / 60))
    except (TypeError, ValueError, OverflowError):
        mins = 0
    return f"{mins} minute(s) ago" if mins else "less than a minute ago"


def _refusal_label_block(labels: list) -> str:
    """The prior preparation's screen labels, wrapped as untrusted text."""
    named = [str(x).strip() for x in list(labels or [])[:8] if str(x).strip()]
    if not named:
        return ""
    return "\n\nThe previous preparation named its screens:\n\n" + wrap_untrusted(
        "prior_screen_labels", "\n".join(named), limit=800
    )


def _refusal_channels(counts: _RefusalCounts) -> str:
    """What the prior preparation was grounded on, as one phrase."""
    # 2026-08-09 (review M2): the "was grounded on" clause describes the
    # PRIOR PREP, so it renders prior TOTALS. It used to render the GAPS
    # handed in for the headline, so a prep grounded on 2 + 2 was reported
    # as having had 1 + 1 -- the shortfall arithmetic leaking into a
    # sentence about a different quantity.
    channels = []
    if counts.prior_cap:
        channels.append(f"{counts.prior_cap} device screen(s) captured on this server")
    if counts.prior_att:
        channels.append(f"{counts.prior_att} screenshot(s) you attached to the chat")
    if not channels:
        # A degenerate direct call must not render "was grounded on .".
        channels.append("screens it did not record")
    return " and ".join(channels)


def _refusal_carries_line(carries: tuple) -> tuple[str, bool]:
    """What THIS call carries, and whether it carries any image."""
    # 2026-08-09 (review C1): this used to hardcode "THIS call carries no
    # images at all", which became FALSE the moment the precondition went
    # per-channel -- a call that re-captures two device screens but drops the
    # chat attachments carries plenty of images, just not the missing ones.
    # Say what the call actually carries, so the tester can see the
    # substitution the server saw.
    carries_cap, carries_att = carries
    has = []
    if carries_cap:
        has.append(f"{carries_cap} captured device screen(s)")
    if carries_att:
        has.append(f"{carries_att} attached screenshot(s)")
    if not has:
        return " THIS call carries no images at all.", False
    return " THIS call carries " + " and ".join(
        has
    ) + ", which does not cover them.", True


def _refusal_consequence(has_images: bool) -> str:
    """The sentence naming what generating now would do."""
    # 2026-08-09 (review M2): the TEXT-alone claim is CONDITIONAL now. It
    # sat two clauses after the carries line had just listed the images this
    # call DOES carry, so the refusal contradicted itself on every partial
    # substitution.
    if has_images:
        return (
            " Generating now writes those cases WITHOUT them -- that is the "
            "silent regression this guard exists to prevent, and "
            "`proceed_anyway=true` does NOT dismiss it."
        )
    return (
        " Generating now writes those cases from the ticket TEXT alone "
        "-- that is the silent regression this guard exists to prevent, and "
        "`proceed_anyway=true` does NOT dismiss it."
    )


_REFUSAL_OPTIONS = (
    "\n\nCall the SAME tool again with the SAME `feature_or_url` (and "
    "the SAME `jira_content_json` if you already fetched the ticket -- do "
    "NOT fetch it twice) plus ONE of:\n\n"
    "1. **The missing screens** -- re-run `qa_capture_screens` and pass "
    "the new `capture_ids`, and/or re-attach the screenshots and pass "
    "`attached_image_count=<how many>` (add `proceed_anyway=true` if this "
    "is also a deliberate restart of the open preparation).\n"
    "2. **Generate with what is available, deliberately** -- pass "
    "`image_carry_ack=true` (add `proceed_anyway=true` if this is also a "
    "deliberate restart of the open preparation). Ask the user first: "
    "the cases will not reflect anything that exists only in those "
    "screens, and the reply will say so.\n\n"
    "Nothing has been prepared yet, so this costs no generation."
)


def _refusal_text(
    prep_id: str, age_s: float, labels: list, counts: _RefusalCounts, carries: tuple
) -> str:
    """Assemble the image-loss refusal from its computed parts."""
    carries_line, has_images = _refusal_carries_line(carries)
    # "of the MISSING screen(s)", same review: now that the channels clause
    # counts the prior TOTALS, a bare "of them" would read as a recovery out
    # of that total rather than out of the shortfall.
    recovered_line = (
        f" I recovered {counts.recovered_cap} of the missing screen(s) from that "
        f"preparation, so {counts.short_total} would still be missing."
        if counts.recovered_cap
        else ""
    )
    consequence = _refusal_consequence(has_images or bool(counts.recovered_cap))
    return (
        f"## \u26d4 This would generate WITHOUT {counts.short_total} of the screens "
        "the last preparation had\n\n"
        f"A preparation for this exact source (`{prep_id}`, started "
        f"{_refusal_ago(age_s)}) was grounded on "
        + _refusal_channels(counts)
        + "."
        + carries_line
        + recovered_line
        + consequence
        + _refusal_label_block(labels)
        + _REFUSAL_OPTIONS
    )


def _text_only_image_skip_stamp(plan: str, img_n: int, img_names: list) -> tuple:
    """(count, names[:8]) to stamp into envelope["meta"] when a text-only
    source_plan='jira' pick skipped beat 2 while the ticket referenced
    screens (v1.97.0 cursor-hardening item 2) -- (0, []) otherwise, so the
    skip is disclosed rather than silently dropped. Never raises."""
    try:
        n = _clamped_count(img_n, lo=0, hi=99) if plan == "jira" else 0
        names = [str(x) for x in (img_names or [])][:8] if n > 0 else []
        return n, names
    except Exception:
        logger.debug("_text_only_image_skip_stamp failed", exc_info=True)
        return 0, []


def _image_gate_second_beat(
    *, count: int, names: list, kind: str, plan: str, have_images: int
) -> str:
    """BEAT 2 text, or "" to stay SILENT.

    Silent whenever the ticket revealed no images, or images actually arrived,
    or the evidence is merely INCONCLUSIVE (kind == "unknown": the Jira payload
    came back with no `attachment` field at all). That last case used to gate,
    which spent a whole extra round trip to say "I could not tell" on a ticket
    that may well have no images -- it is purely informational, so it is left to
    the pre-existing post-hoc notice on the finished payload instead. Silent
    also whenever images actually arrived, or the ticket's own plan is the
    text-only `source_plan='jira'` pick -- a plan that already covered the
    screens is never asked twice, and beat 1's `source_plan='jira'` IS the
    tester's own answer to where the screens come from, so beat 2 must not
    ask a second time (v1.97.0 cursor-hardening item 2 -- this used to fire
    here too, forcing a double ask). Never
    raises."""
    try:
        if _second_beat_is_silent(kind, plan, have_images):
            return ""
        # COMPLETENESS, not presence (2026-08-09): ONE image out of three used
        # to silence this gate, so generation started on a SUBSET of the
        # screens. `count` is the ticket's OWN claim and is clamped to
        # settings.jira_max_images -- that cap bounds how many screens this
        # server will ever carry, so an unclamped N could demand images the
        # tester can never supply. `image_gate_ack=true` is still an ABSOLUTE
        # override at any ratio: the CALLER checks it before reaching here.
        _cap = _jira_image_cap()
        _want = min(max(_clamped_count(count), 1), _cap)
        _have = _clamped_count(have_images, hi=999)
        if _have >= _want:
            return ""
        named = (
            " The ticket names them: " + ", ".join(f"`{n}`" for n in names) + "."
            if names
            else ""
        )
        if _have > 0:
            return _second_beat_partial(_want, _have, names, named)
        return _second_beat_full(count, kind, plan, named)
    except Exception:
        logger.debug("_image_gate_second_beat failed", exc_info=True)
        return ""


def _second_beat_is_silent(kind: str, plan: str, have_images: int) -> bool:
    """True when beat 2 must not ask, whatever the image counts say."""
    if kind not in ("attachments", "embedded"):
        return True
    # v1.97.0 cursor-hardening (item 2): source_plan='jira' (text-only) IS
    # the tester's own answer to "where do the screens come from" -- beat 1
    # already asked and got that answer, so beat 2 must not ask again. The
    # skipped-attachment count is still disclosed elsewhere (never silent),
    # just not via a second dialog.
    if plan == "jira":
        return True
    # 2026-08-31 (C12): menu option 5 reads "Device screens only -- IGNORE
    # the ticket text". Demanding the ticket's attachments from a tester who
    # picked it and has already captured contradicts the choice they were
    # offered. Only once a capture actually arrived: `device` with nothing
    # supplied is still a plan that promised images and delivered none.
    return plan == "device" and have_images > 0


def _partial_outstanding_clause(want: int, have: int, names: list) -> str:
    """The 'what is still outstanding' clause of the PARTIAL beat-2 text."""
    # PARTIAL intake: say the ratio out loud and name what is still
    # outstanding. WHICH screens arrived is unknowable -- the chat
    # channel is a COUNT, not a manifest -- so the missing list is
    # offered in the ticket's own order with that caveat attached.
    # Names already came through _safe_image_name in
    # _ticket_image_evidence and are capped there at 8.
    _missing = [n for n in (names or []) if n][have:][:8]
    if _missing:
        return (
            " In the ticket's own order that leaves "
            + ", ".join(f"`{n}`" for n in _missing)
            + " -- if the ones you have are different, say so."
        )
    # 2026-08-31 (C13): a ticket can claim more images than it NAMES --
    # embedded refs outnumber parsed labels, or the label list was capped
    # at 8. The ratio was still quoted while the outstanding clause went
    # empty, so the tester was told "2 of 5" and nothing about the 3.
    if want > have:
        return (
            f" The ticket {_MARK_UNNAMED_REMAINDER} "
            f"{want - have}, so send whichever ones you have not "
            "already given me."
        )
    return ""


def _second_beat_partial(want: int, have: int, names: list, named: str) -> str:
    """Beat 2 text for a PARTIAL intake: some screens arrived, not all."""
    _which = _partial_outstanding_clause(want, have, names)
    return (
        "## ⏸️ One decision before I generate: the REST of the "
        "ticket's screens\n\n"
        f"> ℹ️ This ticket has {want} image(s) I could NOT read, and "
        f"you gave me {have} of {want}."
        + named
        + _which
        + " "
        + _IMAGE_GATE_LINE
        + "\n\n"
        "Ask the user, then call the SAME tool again with the SAME "
        "`feature_or_url` AND the SAME `jira_content_json` (do NOT "
        "fetch the ticket again) plus ONE of:\n\n"
        "1. **Attach the missing screens** -- attach them (Claude "
        "Code: drag the file into this chat or use `/attach`; "
        "Cursor: click the paperclip icon and pick the file), then "
        "pass `attached_image_count=<the TOTAL now attached, not "
        "just the new ones>`. They stay in YOUR context: no image "
        "bytes are sent to this server, and the generation payload "
        "will ask you to describe them.\n"
        "2. **Generate from the screens I already have** -- ASK THE "
        "TESTER FIRST; only if they agree, pass `image_gate_ack=true`. "
        "I then show the tester a confirmation dialog and only THEIR "
        "'skip the screens' pick clears this gate. The cases will "
        "reflect only those "
        f"{have}, and the reply will say so.\n\n"
        "Nothing has been prepared yet, so this costs no re-fetch and "
        "no second generation."
    )


def _second_beat_full(count: int, kind: str, plan: str, named: str) -> str:
    """Beat 2 text when NO image arrived at all."""
    if kind == "attachments":
        head = f"This ticket has {count} image attachment(s) I could NOT read."
    else:
        head = (
            f"This ticket's description embeds {count} image(s) -- UI mockups "
            "or screens -- that I could NOT read."
        )
    promised = ""
    if plan in ("jira_attach", "jira_device", "jira_both", "device"):
        promised = (
            " You picked a plan that included images, but none of them reached me."
        )
    return (
        "## ⏸️ One decision before I generate: the ticket's screens\n\n"
        "> ℹ️ " + head + named + promised + " " + _IMAGE_GATE_LINE + "\n\n"
        "Ask the user which they want, then call the SAME tool again with "
        "the SAME `feature_or_url` AND the SAME `jira_content_json` (do NOT "
        "fetch the ticket again) plus ONE of:\n\n"
        "1. **Attach the screens to this chat** -- attach them (Claude "
        "Code: drag the file into this chat or use `/attach`; Cursor: "
        "click the paperclip icon and pick the file), then pass "
        "`attached_image_count=<how many you attached>`. They stay in YOUR "
        "context: no image bytes are sent to this server, and the generation "
        "payload will ask you to describe them.\n"
        "2. **Capture them from a connected device** -- call "
        "`qa_capture_screens` first, then pass its `capture_ids`.\n"
        "3. **Generate from the ticket TEXT anyway** -- ASK THE TESTER "
        "FIRST; only if they agree, pass `image_gate_ack=true`. I then "
        "show the tester a confirmation dialog and only THEIR 'skip the "
        "screens' pick clears this gate. The cases will not reflect "
        "anything that exists only in those screens, and the reply will "
        "say so.\n\n"
        "Nothing has been prepared yet, so this costs no re-fetch and no "
        "second generation."
    )


# The server-side ambiguity gate lived here until 2026-08-16 (dead-code
# deletion P2-J): _ambiguity_source_text, _shape_ambiguity_clarify, the
# bounded verdict cache and _maybe_ambiguity_clarify itself. It had been
# unreachable since 2026-08-12 -- _ground_and_gate was its only caller and
# reached it only through the `else` of `if not run_ambiguity_llm:`, a
# condition constantly True because the generation mode is the "host"
# constant. tools/requirement_analyzer.py, which held analyze_requirements
# and gate_triggers, went with it; the ledger id
# `requirement_analyzer.ambiguity_gate` STAYS in tools.host_llm.LEDGER_IDS,
# which never shrinks. The check itself is not lost: it runs on the tester's
# own model as the prepare payload's step-0 ambiguity_job
# (agents.host_jobs.attach_ambiguity_job), which carries the TICKET-7154 rule
# verbatim.


# Cap on the free-form feature description stored as corpus metadata (the
# fine-tune exporter uses it as the prompt); older rows simply lack the key.
_FEATURE_TEXT_METADATA_CAP = 2000


_CORPUS_SOURCE_KEY_CAP = 300

# Prefix for the identity a PASTED FEATURE DESCRIPTION gets. It is a prefix, not
# a bare digest, so a key can never be mistaken for a URL and the two namespaces
# cannot collide.
_CORPUS_TEXT_KEY_PREFIX = "text:"


def _corpus_source_key(source_url: object, feature_text: object = "") -> str:
    """Stable corpus identity for the SOURCE a suite was generated from.

    For a Jira/spec URL: the trimmed, trailing-slash-stripped URL, which a
    re-generation of the same ticket reproduces exactly.

    For everything else -- a pasted feature description, which is the product's
    primary advertised flow -- a digest of that description, whitespace-collapsed
    and lower-cased so incidental re-formatting still matches. This used to
    return "" for every non-URL source, and the caller reads "" as "just append":
    the de-dup fix therefore protected Jira suites and left the pasted-text path
    append-only, re-creating in that half exactly the corpus self-confounding
    (571 -> 676 docs, 5/5 neighbours flagged as duplicate risk) that
    _persist_suite_to_corpus below exists to end.

    An EDITED description is a different key and so appends -- deliberately: two
    different descriptions are two different sources, and only the server-side
    text can be compared here. The duplicate this closes is the dominant one, a
    re-run of the same input.

    With neither a URL nor any text there is no identity at all and this still
    returns "". Pure; never raises."""
    try:
        raw = str(source_url or "").strip().rstrip("/")
        if raw.lower().startswith(("http://", "https://")):
            return raw[:_CORPUS_SOURCE_KEY_CAP]
        text = " ".join(str(feature_text or "").split()).lower()
        if not text:
            return ""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        return f"{_CORPUS_TEXT_KEY_PREFIX}{digest}"
    except Exception:  # pragma: no cover - defensive
        return ""
