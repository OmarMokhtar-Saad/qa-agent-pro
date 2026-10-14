"""Constants of the action vocabulary (moved verbatim from ``actions``)."""

from __future__ import annotations

from tools.device_manager import valid_package_name

MAX_ACTIONS = 40

#: What ONE model submission may carry (owner decision, batch 3). The replay
#: stops at the first surprise and hands the screen back, so every action
#: planned past it was planned blind and is thrown away. ``MAX_ACTIONS`` stays
#: the ceiling of a :class:`Script` itself -- the total a budget-stop queue
#: folds into, and any script that was already accepted once.
MAX_MODEL_ACTIONS = 10
MAX_WAIT_MS = 10000

#: The wall clock ONE submit may spend replaying, in ms. A client kills a tool
#: call at around 60 s: measured on 2026-09-04, where `qa_submit_mobile_step`
#: returned "ok in 60040 ms" to a client that had already given up, and the
#: model started a fresh run rather than resuming. 40 s leaves the reply, the
#: checkpoint write and the packet render inside the window.
#:
#: It lives HERE, next to the vocabulary, and not in ``executor``, because the
#: packet quotes it to the model and ``tools/mobile/run_packets`` may not import the
#: executor. ``executor.SUBMIT_BUDGET_S`` is derived from this one value.
SUBMIT_BUDGET_MS = 40000

#: The SUM of every wait in one script. A single wait is already capped at
#: :data:`MAX_WAIT_MS`, but nothing capped the total, and two 5 s waits plus two
#: types and two asserts took a submit past the client's kill. Refusing costs
#: the tester nothing: a refused script is not an escape, so the model resends a
#: shorter one against the same screen.
MAX_TOTAL_WAIT_MS = 40000

#: What a ``wait`` carrying only ``until_text`` counts as, for the total above:
#: the bound the executor actually applies to it. Mirrored rather than imported
#: -- ``actions`` must not import ``executor`` -- and pinned equal to
#: ``executor.DEFAULT_WAIT_UNTIL_TEXT_S`` by a test, so the two cannot drift.
UNTIL_TEXT_DEFAULT_MS = 20000

#: The condition waits (S17, owner decision: no fixed waits anywhere). Each one
#: returns the moment its condition holds, or when something NEW takes the
#: screen, and ``max_s`` is only the bound on a condition that never comes.
WAIT_UNTIL_OPS = (
    "wait_until_changed",
    "wait_until_text",
    "wait_until_gone",
    "wait_until_idle",
)
#: The longest one condition wait may run, in seconds. Also the cap a legacy
#: ``wait ms`` is clipped to, since it now runs as ``wait_until_changed``.
WAIT_UNTIL_MAX_S = 20
WAIT_UNTIL_DEFAULT_S = 10
#: Every op that waits and actuates nothing. ``executor.ACTUATING_OPS`` is
#: ``MUTATING_OPS`` minus this set.
WAIT_OPS = frozenset({"wait", *WAIT_UNTIL_OPS})
MAX_TEXT_CHARS = 4000
MAX_ERROR_CHARS = 600

#: How many packages one step may declare in ``expect_apps``: the apps a step
#: expects to move into (an installer, the App Tester, the app under test). Each
#: id is whitelist-validated and the executor lets a foreign-app screen through
#: ONLY for these, so the list is kept short and the model has to name them.
MAX_EXPECT_APPS = 8

SCROLL_DIRECTIONS = ("up", "down", "left", "right")
ASSERT_KINDS = (
    "text_present",
    "text_absent",
    "element",
    "new_text",
    "screen_changed",
    "visual",
)
VERDICTS = ("pass", "fail", "blocked")
URL_SCHEMES = ("https", "http", "market")

# Which operations change the device, and therefore force a re-dump. ``wait`` is
# in here because a wait exists precisely to let the screen change.
#
# ``press`` is in here for the same reason it exists: the keyboard's action key
# SUBMITS the focused field, so the screen after it is a different screen.
# ``executor.ACTUATING_OPS`` is derived from this set minus ``wait``, so a press
# also counts as having touched the device for the escape budget -- correct,
# because it taps to focus and then sends a key.
MUTATING_OPS = frozenset(
    {
        "tap",
        "tap_text",
        "type",
        "fill",
        "clear",
        "back",
        "home",
        "scroll",
        "launch",
        "clear_app_data",
        "open_url",
        "wait",
        *WAIT_UNTIL_OPS,
        "press",
    }
)

#: The keys ``press`` may send, and the ONLY ones -- name -> Android keycode.
#:
#: Deliberately NOT ``adb.KEYEVENT_RE``. That regex is the right bound for
#: ``adb.keyevent``, whose callers are this package's own code, but it admits
#: every ``KEYCODE_*`` name and every bare numeric code 0-999 -- ``KEYCODE_POWER``,
#: the volume and call keys, three-digit codes nobody enumerated. A model-facing
#: vocabulary cannot inherit that surface: the model chooses the value, and
#: "anything the device understands" is not a bound. So ``press`` carries its
#: own allowlist and ``adb`` validates again behind it.
#:
#: One key on purpose. ``enter`` IS the IME action on a single-line field.
#: Each addition is one line here plus one behavioural test.
PRESS_KEYS: dict[str, str] = {"enter": "KEYCODE_ENTER"}


#: The longest word or phrase `tap_text` will look for. A LABEL, not a
#: paragraph: it is matched against an element's visible text, so a query longer
#: than any label on any screen can only ever miss. It bounds the string a model
#: can put into a matcher.
TAP_TEXT_MAX_QUERY_CHARS = 120

#: How many DISTINCT controls may carry the word before `tap_text` refuses.
#: One, and that is the point rather than a tuning knob: two controls carrying
#: the same word means the script did not say which it meant, and choosing
#: between them by dump order is how "Send voice message" was tapped instead of
#: "Send". The refusal names the count, so the model can re-target with a `tap`
#: and a narrower selector -- which is always available.
TAP_TEXT_MAX_CANDIDATES = 1


def is_package_id(value: object) -> bool:
    """True for a well-formed Android package id with no stray whitespace.

    ``device_manager.valid_package_name`` anchors its pattern with ``$``, which
    also matches before a trailing newline, so ``com.a.b\n`` passes it. A package
    id reaches a subprocess argument list, so the extra strip check is kept here.
    """
    return (
        isinstance(value, str) and valid_package_name(value) and value == value.strip()
    )
