"""Action trace rendering for the report: action lines, step rows, sequence rows and plain-language step words."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile import run_store
from tools.mobile.report_frames import _phone_html, _resolve_look, _step_pics
from tools.mobile.report_text import MAX_TEXT, _ms, _text, esc, fmt_ms
from tools.mobile.report_widgets import _outcome_pill

MAX_ROWS = 400


#: The row kind a trace op is drawn as. The kinds are the shell's own vocabulary
#: (``.seq.is-<kind>`` is what the stylesheet colours by).
OP_KIND = {
    "tap": "tap",
    "press": "tap",
    "back": "tap",
    "type": "step",
    "set": "step",
    "clear": "step",
    "swipe": "step",
    "scroll": "step",
    "assert": "event",
    "wait": "log",
    "wait_until_text": "log",
    "wait_until_gone": "log",
    "wait_until_changed": "log",
    "wait_until_idle": "log",
    "done": "done",
    "escape": "note",
    "ask": "note",
    "needs": "note",
}


def _action_line(action: object) -> str:
    """One human line for a trace action, including what it typed.

    **This docstring described the opposite rule until 2026-09-04, and the rule
    it described was itself the defect.** The typed ``text`` was never rendered,
    on the reasoning that a rendering which cannot reach a credential beats one
    that masks it. The cost was that a chat case's report showed ``type -> e14``
    and no tester could tell which question had been asked -- for a lane whose
    whole job is to evidence that the app answered, that is not a report.

    So the control moved from absence to masking, and it is doubled:

    * :func:`tools.mobile.actions.redact_action` masks at the SOURCE -- on the
      ``secret`` marker and on ``CREDENTIAL_TERMS`` -- so the packet, the audit
      log and the checkpoint are covered, not only this page;
    * this function masks again at the RENDER boundary, because a checkpoint
      written by an older build was never through that rule.

    The mutation proof lives on the mask below -- delete it, or either half of
    its condition, and the secret tests in tests/mobile/test_mobile_report.py
    and test_mobile_report_chat.py go red. It does NOT live on the
    ``run_store.redact`` call in :func:`_trace_rows`: deleting that still
    changes no byte of the page, measured, because the mask here fires first.
    Said plainly because the first version of this docstring claimed otherwise.
    """
    body = action if isinstance(action, dict) else {}
    if not body:
        return _text(action, 80)
    hint = _action_hint(body.get("target"))
    extra = _text(body.get("kind") or body.get("field") or body.get("dir"), 40)
    parts = [_text(body.get("op"), 24) or "?"]
    if hint:
        parts.append("-> " + hint)
    if extra:
        parts.append("(" + extra + ")")
    typed = _typed_literal(body)
    if typed:
        parts.append("\u201c" + typed + "\u201d")
    return " ".join(parts)


def _action_hint(target: object) -> str:
    """The target's label for a trace line: ``""`` when it has none."""
    target = target if isinstance(target, dict) else {}
    # `rid` first, then what was on screen, and the short `id` LAST -- the same
    # order `actions.resolve_target` uses, and for the reader's sake rather
    # than the resolver's: an id is a content hash, so a step table that put it
    # first read `tap -> e9f3a1b2` where the model had also given a resource id
    # a tester could recognise.
    return _text(
        target.get("rid")
        or target.get("text")
        or target.get("desc")
        or target.get("id"),
        60,
    )


def _typed_literal(body: dict) -> str:
    """What a ``type`` action sent, or the mask. ``""`` for any other op."""
    if str(body.get("op") or "") != "type":
        return ""
    if body.get("secret") or actions_mod.is_credential_action(body):
        return actions_mod.SECRET_MASK
    return _text(body.get("text"), 160)


def _trace_rows(trace: object) -> list:
    """The step table's rows, each re-redacted before anything is rendered."""
    return [
        _trace_row(entry)
        for entry in list(trace or [])[:MAX_ROWS]
        if isinstance(entry, dict)
    ]


def _trace_row(entry: dict) -> dict:
    """One step-table row from one trace entry, re-redacted first.

    The ``run_store.redact`` call below is STILL NOT canary-provable, because
    MUTATION SAID SO: deleting it kept every test green, since `_action_line`
    masks on the `secret` marker and on CREDENTIAL_TERMS ITSELF. The structural
    control is the one that holds; this call is kept as depth for the day a
    field IS rendered that only key-based redaction covers. A security comment
    that overstates its own line gets a control trusted for what it does not do.
    """
    safe = run_store.redact(entry)
    safe = safe if isinstance(safe, dict) else {}
    action = safe.get("action")
    action = action if isinstance(action, dict) else {}
    # Derived ONCE. The id that names a screen and the key that names a LOOK
    # at that screen must come from the same normalisation: a truncation on
    # one side and none on the other is two derivations of one id, which is
    # a defect the neighbouring plan for this file has already shipped.
    before_id = _text(safe.get("before_screen_id"), 40)
    after_id = _text(safe.get("after_screen_id"), 40)
    return {
        "index": _text(safe.get("index"), 8),
        "op": _text(action.get("op"), 24) or "?",
        "action": _action_line(safe.get("action")),
        "plain": _plain_action(action),
        "act": action,
        "target_id": _text(
            (action.get("target") or {}).get("id")
            if isinstance(action.get("target"), dict)
            else "",
            40,
        ),
        "outcome": _text(safe.get("outcome"), 40) or "-",
        "ms": _ms(safe.get("ms")),
        "detail": _text(safe.get("detail"), MAX_TEXT),
        "notes_applied": _notes_applied(safe.get("knowledge")),
        "before": before_id,
        "after": after_id,
        # WHICH screen each end was, above; WHAT was on it, here. Two
        # questions, two names, and the key is minted by the STORE's own
        # producer so this reader cannot ask for a key the writer would
        # not have written. An observation id is 25 characters -- a
        # 12-hex screen id, a dash and 12 more -- so it survives the
        # 40-character `_text` a frame applies to it, with no room for
        # two observations to collide on a shared prefix.
        "before_obs": _obs_key(before_id, safe.get("before_screen_hash")),
        "after_obs": _obs_key(after_id, safe.get("after_screen_hash")),
    }


def _notes_applied(raw: object) -> str:
    """``Notes applied: #3 wait confirmed (1200 ms); ...`` from a trace entry's
    ``knowledge`` list, or ``""`` when no saved note touched the step."""
    parts = []
    for item in (raw if isinstance(raw, list) else [])[:6]:
        if not isinstance(item, dict):
            continue
        parts.append(
            "#%s %s %s%s"
            % (
                _text(item.get("note_id"), 12),
                _text(item.get("kind"), 12),
                _text(item.get("outcome"), 20).replace("_", " "),
                " (%s ms)" % _text(item.get("ms"), 10) if item.get("ms") else "",
            )
        )
    return ("Notes applied: " + "; ".join(parts)) if parts else ""


def _obs_key(ident: object, screen_hash: object) -> str:
    """The observation key for this end of a step, or ``""`` when there is none.

    NOT `run_store.observation_key` directly. That function degrades to the bare
    screen id when a screen carries no content hash, which is correct where it
    lives -- a hashless screen IS its own single observation, and the store must
    put it somewhere. At THIS boundary the degraded value is poison: it is
    truthy, so `_changed`'s "do both ends have an observation?" guard passes,
    and it is a screen id, so the comparison is then an observation key against
    an id -- two namespaces, silently.

    That is not a rare shape. `executor` stamps `before_screen_hash` on every
    entry but `after_screen_hash` at ONE of fourteen sites, so "before hashed,
    after not" is what thirteen of fourteen replay paths produce. Measured on a
    four-turn chat: every "Screen after" frame rendered the FULL final
    conversation, including a reply the app had not yet produced, captioned as
    what the action left on screen. Before this feature the frame was suppressed;
    drawing a fabricated one is strictly worse, and a tester cannot tell.

    So: no hash, no observation key, and every reader falls back to the screen id
    it used before. One producer, one meaning.
    """
    digest = str(screen_hash or "")
    if not digest:
        return ""
    return run_store.observation_key(ident, digest)


def _changed(row: dict) -> bool:
    """Did this action leave a DIFFERENT screen than it found?

    CONTENT when both ends recorded a content hash, identity otherwise. The id
    alone answers "a different screen", and on a chat every reply is the same
    screen with different content -- so the frame this used to suppress was the
    only evidence the reply ever arrived. The fallback is what a record written
    before observations existed still gets, unchanged.
    """
    if row["before_obs"] and row["after_obs"]:
        return row["after_obs"] != row["before_obs"]
    return row["after"] != row["before"]


def _seq_rows(rows: list, screens: object = None, app: str = "") -> str:
    """The run sequence: one row per action, in the order it ran.

    A row whose screen CHANGED carries the new screen's frame in its fold, so
    the sequence reads as what the tester would have seen, step by step."""
    if not rows:
        return ""
    longest = max((row["ms"] or 0) for row in rows) or 0
    clock = 0
    out = []
    for row in rows:
        kind = OP_KIND.get(row["op"].lower(), "step")
        label = "0.0s" if clock == 0 else "+%.1fs" % (clock / 1000.0)
        out.append(
            _seq_item(
                kind,
                label,
                _seq_head(row),
                _seq_bar(row, longest),
                _seq_detail(row, screens, app),
            )
        )
        clock += row["ms"] or 0
    return '<ul class="seqlist">' + "".join(out) + "</ul>"


def _seq_bar(row: dict, longest: int) -> str:
    """The step's duration bar against the longest step, or '' without a time."""
    if row["ms"] is None or not longest:
        return ""
    return (
        '<span class="seqbar k-tool" title="'
        + str(int(row["ms"]))
        + " ms of the "
        + str(int(longest))
        + ' ms longest step in this case"><i style="width:%.1f%%"></i></span>'
        % max(3.0, min(100.0, row["ms"] / float(longest) * 100.0))
    )


def _seq_head(row: dict) -> str:
    """The step's operation, action text, time and outcome pill."""
    return (
        '<span class="seqk">'
        + esc(row["op"], 24)
        + '</span><span class="seqtx" dir="auto">'
        + esc(row["action"], 160)
        + "</span>"
        + (
            ('<span class="mt">' + esc(fmt_ms(row["ms"]), 16) + "</span>")
            if row["ms"] is not None
            else ""
        )
        + _outcome_pill(row["outcome"])
    )


def _seq_detail(row: dict, screens: object, app: str) -> str:
    """The folded detail of a step: full text, screen change, new screen frame."""
    detail = ""
    if row["detail"]:
        detail = '<p class="seqfull" dir="auto">' + esc(row["detail"]) + "</p>"
    if row["before"] or row["after"]:
        detail += (
            '<div class="chipnote"><i>screen</i>'
            + (esc(row["before"], 40) or "—")
            + " → "
            + (esc(row["after"], 40) or "—")
            + "</div>"
        )
    if row["after"] and _changed(row):
        detail += (
            '<div class="phonewide">'
            + _phone_html(
                "Screen after",
                "what this action left on screen",
                row["after_obs"] or row["after"],
                screens,
                app,
            )
            + "</div>"
        )
    return detail


def _seq_item(kind: str, label: str, head: str, bar: str, detail: str) -> str:
    """One sequence list item: a fold when there is detail, else a plain row."""
    if detail:
        return (
            '<li class="seq is-'
            + kind
            + '"><details class="seqfold"><summary><span class="chev" aria-hidden="true"></span>'
            '<span class="seqt">'
            + label
            + '</span><div class="seqmain">'
            + head
            + "</div>"
            + bar
            + '</summary><div class="seqdetail">'
            + detail
            + "</div></details></li>"
        )
    return (
        '<li class="seq is-'
        + kind
        + '"><span class="chev ghost" aria-hidden="true"></span><span class="seqt">'
        + label
        + '</span><div class="seqmain">'
        + head
        + "</div>"
        + bar
        + "</li>"
    )


#: A trace outcome in plain words. Anything unlisted reads as its own name.
OUTCOME_WORDS = {
    "ok": "done",
    "done": "finished",
    "assert_pass": "check passed",
    "assert_fail": "check failed",
    "visual_check": "visual check (judged by the model)",
    "left_app": "left the app",
    "route_mismatch": "screen not as saved",
    "supplied": "tester answered",
    "knowledge_avoid": "stopped by a saved note",
    "-": "not run",
}


def _q(text: object) -> tuple:
    """A quoted run of app text inside a plain-words line."""
    return ("q", _text(text, 80))


def _rid_tail(rid: object) -> str:
    return str(rid or "").rsplit("/", 1)[-1].replace("_", " ")


_TARGET_VERBS = {
    "tap": "Tapped ",
    "long_press": "Long-pressed ",
    "clear": "Cleared ",
}

_FIXED_WORDS = {
    "wait_until_changed": "Waited for the screen to change",
    "wait_until_idle": "Waited for the screen to settle",
    "launch": "Opened the app",
}

_WAIT_OPS = ("wait", "wait_until_text", "wait_until_gone")


_TURN_OPS = ("assert", "done", "ask_tester")


def _named_target(target: object) -> tuple:
    """The target's quoted name, its role, and the words that point at it."""
    target = target if isinstance(target, dict) else {}
    name = _text(
        target.get("text")
        or target.get("label")
        or target.get("desc")
        or _rid_tail(target.get("rid")),
        60,
    )
    role = _text(target.get("role"), 24)
    thing = [_q(name)] if name else ["the " + role if role else "an unlabelled element"]
    return name, role, thing


def _plain_wait(op: str, body: dict) -> list:
    """The plain words of the three waits that name a text or a time."""
    if op == "wait_until_text":
        return ["Waited for ", _q(_text(body.get("text"), 80)), " to appear"]
    if op == "wait_until_gone":
        return ["Waited for ", _q(_text(body.get("text"), 80)), " to go"]
    until = _text(body.get("until_text"), 80)
    if until:
        return ["Waited for ", _q(until), " to appear"]
    return ["Waited " + fmt_ms(_ms(body.get("ms")) or 0)]


def _plain_turn_op(op: str, body: dict) -> list:
    """The plain words of the ops that end or check a turn."""
    if op == "assert":
        text = _text(body.get("text") or body.get("contains"), 80)
        if text:
            return ["Checked that ", _q(text), " is on screen"]
        return ["Checked the screen"]
    if op == "done":
        reason = _text(body.get("reason"), 200)
        if reason:
            return ["Finished the turn: ", _q(reason)]
        return ["Finished the turn"]
    field = _text(body.get("field"), 24)
    return ["Asked the tester for " + (field or "input")]


def _plain_action(action: object) -> list:
    """A trace action as plain words: text parts and :func:`_q` quotes.

    Typed text goes through :func:`_typed_literal`, so a credential is the
    mask here exactly as it is in the step table.
    """
    body = action if isinstance(action, dict) else {}
    op = str(body.get("op") or "")
    name, role, thing = _named_target(body.get("target"))
    if op in _TARGET_VERBS:
        return [_TARGET_VERBS[op]] + thing
    if op in _FIXED_WORDS:
        return [_FIXED_WORDS[op]]
    if op == "type":
        typed = _typed_literal(body)
        return ["Typed ", _q(typed)] + ([" into "] + thing if name or role else [])
    if op in ("scroll", "swipe"):
        return ["Scrolled " + (_text(body.get("dir"), 12) or "the screen")]
    if op in ("back", "home"):
        return ["Pressed " + op.capitalize()]
    if op in _WAIT_OPS:
        return _plain_wait(op, body)
    if op in _TURN_OPS:
        return _plain_turn_op(op, body)
    return [_action_line(body)]


def _parts_html(parts: object) -> str:
    out = []
    for part in list(parts or []):
        if isinstance(part, tuple):
            out.append('<q dir="auto">' + esc(part[1], 200) + "</q>")
        else:
            text = str(part)
            out.append(
                (" " if text[:1] == " " else "")
                + esc(text, 200)
                + (" " if text[-1:] == " " else "")
            )
    return "".join(out)


def _element_of(target_id: object, screen: object) -> dict:
    tid = str(target_id or "")
    body = screen if isinstance(screen, dict) else {}
    for element in list(body.get("elements") or []) if tid else []:
        if isinstance(element, dict) and str(element.get("id") or "") == tid:
            return element
    return {}


def _step_words(row: dict, library: object) -> list:
    """The row's plain words, naming its target from the before screen when
    the trace itself recorded only the element's id."""
    action = row.get("act") if isinstance(row.get("act"), dict) else {}
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    if not target or any(target.get(k) for k in ("text", "label", "desc", "rid")):
        return row.get("plain") or []
    _ident, screen, _verdict = _resolve_look(
        row.get("before_obs") or row.get("before"), library
    )
    element = _element_of(target.get("id"), screen)
    if not element:
        return row.get("plain") or []
    named = {k: element.get(k) for k in ("text", "label", "desc", "rid", "role")}
    return _plain_action(dict(action, target=dict(target, **named)))


#: Row key the case card sets on its LAST step to ``(key, note)`` when it knows a
#: better After than the row's own key: the substituted end state, disclosed in
#: *note*. Carried on the row (a copy) so `_step_html` keeps five parameters.
_AFTER_LOOK = "_after_look"


def _step_html(tc_slug: str, index: int, row: dict, library: object, app: str) -> str:
    """One step: its words, its outcome, what the server said about it, and
    the screen before and after (see :data:`_AFTER_LOOK`).
    """
    outcome = str(row.get("outcome") or "-")
    word = OUTCOME_WORDS.get(outcome.lower(), outcome.replace("_", " "))
    pics = _step_pics(row, library, app, row.get(_AFTER_LOOK) or ())
    detail = str(row.get("detail") or "")
    applied = str(row.get("notes_applied") or "")
    return (
        '<li class="step" id="step-'
        + tc_slug
        + "-"
        + str(int(index))
        + '"><div class="stephead"><span class="stepn">'
        + str(int(index) + 1)
        + '</span><p class="stepact" dir="auto">'
        + _parts_html(_step_words(row, library))
        + "</p>"
        + _outcome_pill(word)
        + "</div>"
        + "".join(
            '<p class="stepnote" dir="auto">' + esc(line, 300) + "</p>"
            for line in (detail, applied)
            if line
        )
        + (('<div class="steppics">' + pics + "</div>") if pics else "")
        + "</li>"
    )
