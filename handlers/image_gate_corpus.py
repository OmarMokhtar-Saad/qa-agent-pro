"""Jira image gate: the stable corpus identity of the source a suite was generated from."""

from __future__ import annotations

import hashlib

from config.settings_guard import never_raise

# The server-side ambiguity gate lived here until 2026-08-16 (dead-code
# deletion P2-J): _ambiguity_source_text, _shape_ambiguity_clarify, the
# bounded verdict cache and _maybe_ambiguity_clarify itself. It had been
# unreachable since 2026-08-12 -- ground_and_gate was its only caller and
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


# Stable corpus identity for the SOURCE a suite was generated from.
#
# For a Jira/spec URL: the trimmed, trailing-slash-stripped URL, which a
# re-generation of the same ticket reproduces exactly.
#
# For everything else -- a pasted feature description, which is the product's
# primary advertised flow -- a digest of that description, whitespace-collapsed
# and lower-cased so incidental re-formatting still matches. This used to
# return "" for every non-URL source, and the caller reads "" as "just append":
# the de-dup fix therefore protected Jira suites and left the pasted-text path
# append-only, re-creating in that half exactly the corpus self-confounding
# (571 -> 676 docs, 5/5 neighbours flagged as duplicate risk) that
# gen_persist_suite below exists to end.
#
# An EDITED description is a different key and so appends -- deliberately: two
# different descriptions are two different sources, and only the server-side
# text can be compared here. The duplicate this closes is the dominant one, a
# re-run of the same input.
#
# With neither a URL nor any text there is no identity at all and this still
# returns "". Pure; never raises.
def _corpus_source_key(source_url: object, feature_text: object = "") -> str:
    """Return the stable corpus identity of the source, or an empty string."""
    with never_raise():
        raw = str(source_url or "").strip().rstrip("/")
        if raw.lower().startswith(("http://", "https://")):
            return raw[:_CORPUS_SOURCE_KEY_CAP]
        text = " ".join(str(feature_text or "").split()).lower()
        if not text:
            return ""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        return f"{_CORPUS_TEXT_KEY_PREFIX}{digest}"
    return ""
