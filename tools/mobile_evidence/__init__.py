"""App-log evidence for the mobile lane: what the app itself said while a case ran.

The mobile lane replays actions and stores screens. Everything ELSE a tester wants
beside a case -- the model round-trips and their tokens, the endpoint calls and
their status, the tool calls, the cards the app drew -- lives only in the app's
own log. This package captures that log (``capture``), reads it (``grammar``),
attributes it to cases by time window (``evidence``), turns each call into the
report shell's exchange row (``exchanges``) and prices it (``model``), with the
redaction nets (``scrub``) applied before the first byte reaches disk and again
at render.

**The engine is app-agnostic; the profile carries every vendor word.** Nothing in
a ``*.py`` file here names an app, a log tag, a prose phrase or a glyph the app
prints. All of that is data in a ``Profile`` (``profiles.py``), loaded from a
JSON file that is selected by the run's package name. The one profile shipped
in this private repository is EXCLUDED from the public distribution; an
installed distribution reads profiles only from the tester's own
``~/.qa-agents/mobile/profiles/`` directory. A package with no profile gets no
capture, no adb call and an honest "no app log captured" everywhere the page
would otherwise show evidence. A test walks this package for an emoji, an arrow
glyph, a right-to-left codepoint or a vendor word and fails on the first one.

**Contract.** No function in this package raises to a caller. Where the
reference implementation exited, these return ``{"error": ..., "content":
None}``; where a stream cannot be parsed, it is disabled and NAMED under
"Can this capture be trusted" rather than silently absent. A duration that was
not measured is ``None``, never ``0``; an outcome the log did not state is
``None``, never ``False``.
"""

from __future__ import annotations
