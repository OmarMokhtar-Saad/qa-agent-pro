"""The standalone HTML run report, built from a run's own files and nothing else.

``render(run_id)`` reads ``runs/<run_id>/manifest.json``, ``cases/TC-*.json``,
``screens/<screen_id>.json`` and ``lease.json`` -- and NOTHING else. No device,
no ``suite_store``, no in-memory state left by a previous call. That is what
makes a MID-RUN report possible: a half-finished run is simply a run whose
checkpoints are not all terminal yet, and the page says so (``partial``).

**The page is the Sara report shell.** ``report_shell.html`` beside this module
is the design file the reference device-run report is drawn with -- the
same tokens, the same appbar, masthead, KPI tiles, filter toolbar, expandable
case cards and phone frames, and the same script behind them -- so a reader who
has learned that page can read this one without relearning anything. This
module owns the DATA only: it fills the shell's ``{{SLOT}}``s with markup built
from the run's files. A colour never belongs here, and a number never belongs
in the shell.

**Every string in here was chosen by an app on the device.** A dump's ``text``,
``content-desc`` and ``resource-id`` are attacker-influenced, and this is an
HTML document, so the exposure is XSS in the tester's browser rather than only
prompt spoofing. ``perception._clean`` caps length and neutralises the guard
markers; it does NOT escape HTML. Three layers answer that, in this order:

1. **The one ``script`` element's text is :data:`SHELL_SCRIPT`, read from the
   shell file, with no interpolation of any kind -- and no event-handler
   attribute is ever emitted.** The script is the shell's own behaviour (theme
   switch, filter, sort, deep links); nothing a store value carries can reach
   it, and a test asserts the page's script text equals the constant, so any
   attacker byte reaching it makes the two differ. The store islands the shell
   supports (``<script type="text/plain">``) are NEVER emitted by this lane.
2. **The one ``style`` element's text is :data:`SHELL_STYLE`, likewise read
   from the shell, with no interpolation** -- no f-string, no ``%``, no
   ``.format``. The CSS context is absent by construction, and a test asserts
   the page's style text equals the constant.
3. **Every interpolated value goes through :func:`esc`** =
   ``html.escape(_text(value), quote=True)``. ``quote=True`` is load-bearing:
   attribute values are quoted and ``&quot;``/``&#x27;`` is what stops a value
   closing its own attribute.

Slots are filled in ONE regex pass over the template (:func:`_fill`), so a
value that happens to contain ``{{SECTIONS}}`` is never itself expanded.

Geometry is the one thing not escaped and does not need to be: a rect's
``style`` carries only ``int``s this module computed (:func:`scale_bounds`),
never a store string, and a test pins that they really are ``int``.

**Secrets.** The store already writes ``***`` for marked values and masks
credential-NAMED keys regardless, so on a file this tree wrote the extra
:func:`run_store.redact` call below changes nothing. It earns its place on the
three inputs the store did not write -- a checkpoint from another build, a file
someone edited, a run directory copied from another machine. It CANNOT catch a
value stored under an unrecognised key with no marker; ``run_store``'s own
docstring says so and this module does not claim more. Two further, structural
reductions: the report never prints a typed literal (``text``) from an action
and never JSON-dumps a trace entry, so a credential in an action's own text has
no route into the page and the token ``secret`` never appears in it at all.

**The kill-switch is read here.** Writing a file is not in
``tests/mobile/test_mobile_killswitch_surface.py``'s ``EFFECT_CALLS``, so
nothing mechanical binds this module -- but ``report_selfcheck`` is reachable as
``python3.12 -m tools.mobile.report_selfcheck <run_id>`` from outside the MCP
process entirely, which is the exact shape that produced three review rounds in
this programme (``provisioner --apply``, the extracted ``emulator.start``,
``session.start_install``). A guard on a caller is only as good as the list of
callers, and for a module with a ``-m`` entry point that list includes the
shell. ``run_store.write_case`` is unguarded and that asymmetry is deliberate:
it has no entry point of its own.
"""

from __future__ import annotations

END_ID = "qa-report-end"


#: Every environment fact this page can state, and the named reason for each it cannot.
#: One row per fact, and a row is NEVER omitted when the value is missing -- an omitted
#: row reads as "nothing to say", which is the success shape.


#: The run's last known moment, DERIVED, and labelled as derived everywhere it is shown.
#:
#: The manifest carries ``created`` and NO ``ended``: nothing writes one, because a run can
#: be abandoned and an abandoned run has no honest end. So this page never prints the word
#: "ended". What it can say is when the newest case was last checkpointed, which is a
#: different fact with a different name -- and one producer for it, so the masthead, the
#: overview strip and the timeline cannot disagree about when the run stopped.


# ── the run page: header, issues, journey, scripted cases, diagnostics ─────────


#: Section ids, top to bottom. The nav lists only the ones this run fills.
ISSUES_ID = "issues"
RECORDING_ID = "recording"
JOURNEY_ID = "journey"
CASES_ID = "cases"
DIAG_ID = "diagnostics"
ABOUT_ID = "about"
KNOWLEDGE_ID = "knowledge"
