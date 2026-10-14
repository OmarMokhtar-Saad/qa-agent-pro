"""The qa_export_suite handler (moved out of handlers.export)."""

from __future__ import annotations

import logging

from agents.host_submission import safe_prep_id as host_submission_safe_prep_id
from handlers import _common, _elicit, _reply
from handlers.export_formats import _available_exporters, _format_menu_markdown
from handlers.export_hooks import export
from handlers.export_suite import ExportJob, ExportOptions, _export_suite_reply

logger = logging.getLogger("tools.mcp_handlers")


async def _resolve_format(fmt: str, choose: _common.ChooseCb) -> tuple[str, str | None]:
    """The export format, or a reply that ends the call."""
    fmt = (fmt or "").strip().lower()
    if not fmt:
        picked = await _elicit.elicit_choice(
            choose, "Which export format?", list(sorted(_available_exporters()))
        )
        if picked.status == _common.CHOSEN:
            fmt = (picked.value or "").strip().lower()
        elif picked.status == _common.DECLINED:
            return fmt, "👍 Cancelled — no export format selected."
        else:
            return fmt, _format_menu_markdown()
    if fmt not in _available_exporters():
        return (
            fmt,
            f"⚠️ Unknown format '{_reply._clip_echo(fmt, 60)}'. Choose one of: "
            f"{', '.join(sorted(_available_exporters()))}.",
        )
    return fmt, None


async def _resolve_suite_id(
    suite_id: str, choose: _common.ChooseCb
) -> tuple[str, str | None]:
    """The stripped suite id, or a reply that ends the call."""
    suite_id = (suite_id or "").strip()
    if not suite_id:
        picked = await _elicit.elicit_suite(choose)
        if picked.status == _common.CHOSEN:
            suite_id = (picked.value or "").strip()
        elif picked.status == _common.DECLINED:
            return suite_id, "👍 Cancelled — no suite selected."
        else:
            return suite_id, await _elicit.recent_suites_markdown("qa_export_suite")
    if not suite_id:
        return suite_id, await _elicit.recent_suites_markdown("qa_export_suite")
    return suite_id, None


async def _export_suite_resolve(
    suite_id: str, fmt: str, choose: _common.ChooseCb
) -> tuple[str, str, str | None]:
    """Settle the format and suite id; the third item is a reply that ends the call."""
    fmt, early = await _resolve_format(fmt, choose)
    if early is not None:
        return fmt, suite_id, early
    suite_id, early = await _resolve_suite_id(suite_id, choose)
    return fmt, suite_id, early


# The next-call answer when a prep id arrives where a suite id belongs.
#
# 2026-08-12: the two are easy to confuse right after a volume refusal -- the
# point at which a host is most likely to give up on the tool path -- so this
# answers with the next call rather than an error. One sanitiser, not two:
# the inline version this replaced dropped newlines instead of replacing
# them, concatenating the fragments either side.
def _export_prep_id_reply(safe_prep_id: str) -> str:
    """The next-call answer when a prep id arrives where a suite id belongs."""
    return (
        f"⚠️ `prep_id` is not a suite id, so nothing was exported "
        f"and prep `{safe_prep_id}` is intact.\n\n"
        "A prep becomes exportable only once `qa_submit_suite` finalizes it: "
        "that call returns the `suite_id` this tool needs, and it already "
        "auto-exports the .xlsx for you.\n\n"
        "- Still short of the volume floor? Top up the short categories with "
        "`qa_submit_category`, then " + export._finalize_route_phrase() + ".\n"
        "- `qa_prep_status` shows what is staged for this prep.\n\n"
        "Do NOT write the suite to a file yourself -- see the refusal note "
        "on `qa_submit_suite`."
    )


async def _load_and_export(suite_id: str, fmt: str, options: ExportOptions) -> str:
    """Load the stored suite and export it, or say why that cannot be done."""
    loaded = await export.load_suite(suite_id)
    if loaded.get("error"):
        return (
            f"⚠️ Could not load suite `{_reply._clip_echo(suite_id, 80)}`: "
            + _reply._clip_echo(loaded["error"], 300)
        )
    suite = loaded.get("content")
    if suite is None:
        return _reply._suite_unreadable_reply(suite_id, loaded)
    job = ExportJob(suite, suite_id, fmt, options.output_dir, options.progress)
    return await _export_suite_reply(job)


# Export a stored suite to one format and return the written path.
#
# *output_dir* (I4, 2026-08-10) is the OPTIONAL, tester-supplied save folder.
# It exists because the auto-export save-location dialog is unreachable
# whenever QA_EXPORT_DIR resolves -- which it does on every default install --
# so tester control belongs on the call that is explicitly about exporting,
# as a parameter rather than an elicitation. Always optional: a required
# field here would break every existing caller. Empty keeps each format's
# current destination exactly -- a secure temp folder for all five
# single-file exporters, which since batch D4 deleted the Zephyr pair
# (2026-08-15) is every format there is. Never raises.
async def handle_export_suite(
    suite_id: str, fmt: str, options: ExportOptions | None = None
) -> str:
    """Export a stored suite to one format and return the written path."""
    options = options or ExportOptions()
    # A prep_id is not a suite id (see `_export_prep_id_reply`); suite_id wins
    # when both arrive. The id is host-supplied: ONE sanitiser caps it and
    # strips backticks/newlines so it cannot break out of its code span.
    _prep_only = host_submission_safe_prep_id(options.prep_id)
    if _prep_only and not (suite_id or "").strip():
        return _export_prep_id_reply(_prep_only)
    fmt, suite_id, early = await _export_suite_resolve(suite_id, fmt, options.choose)
    if early is not None:
        return early
    try:
        return await _load_and_export(suite_id, fmt, options)
    except Exception as exc:
        logger.exception("handle_export_suite failed")
        _common.capture_error(exc, "qa_export_suite")
        return f"⚠️ Export failed: {exc}"
