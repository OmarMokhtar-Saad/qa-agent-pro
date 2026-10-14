"""Hook builders A (common, capture, image, note) for the host-mode prepare handler.

Agents never import handlers; the moved prepare code reaches handler modules (and
names tests patch on ``handlers.generate``) through these groups. Every field
resolves its target INSIDE the lambda, at call time: facade names go through
``host()``, handler modules are read as attributes, so a patch applied before or
after a builder ran still takes effect.
"""

from __future__ import annotations

from agents.host_gen_args import NoticeArgs
from agents.host_gen_hooks import CaptureHooks, CommonHooks, ImageHooks, NoteHooks
from handlers import _common, capture, capture_limits, image_gate
from handlers.generate_host import host


def _common_hooks() -> CommonHooks:
    return CommonHooks(
        audit=lambda *a, **k: _common.audit(*a, **k),
        emit=lambda *a, **k: _common.emit(*a, **k),
        is_url=lambda *a, **k: _common._is_url(*a, **k),
        boot_version=lambda: _common.BOOT_VERSION,
    )


def _capture_hooks() -> CaptureHooks:
    return CaptureHooks(
        drop_captures=lambda *a, **k: capture._drop_captures(*a, **k),
        retry_hint=lambda *a, **k: capture._capture_retry_hint(*a, **k),
        resolvable=lambda *a, **k: capture.resolvable_captures(*a, **k),
        carry_forward=lambda *a, **k: capture.carry_forward_or_refuse(*a, **k),
        max_missing_shown=lambda: capture_limits._MAX_MISSING_SHOWN,
    )


def _image_hooks() -> ImageHooks:
    return ImageHooks(
        gate_jira_source=lambda *a, **k: image_gate._gate_jira_source(*a, **k),
        normalize_source_plan=lambda *a, **k: image_gate._normalize_source_plan(
            *a, **k
        ),
        clamped_count=lambda *a, **k: image_gate._clamped_count(*a, **k),
        unreadable_note=lambda *a, **k: image_gate._unreadable_images_note(*a, **k),
        grounding_source_note=lambda *a, **k: image_gate._grounding_source_note(
            *a, **k
        ),
    )


def _note_hooks() -> NoteHooks:
    return NoteHooks(
        safe_snapshot_stamp=lambda *a, **k: host()._safe_snapshot_stamp(*a, **k),
        jira_content_chars=lambda *a, **k: host()._jira_content_chars(*a, **k),
        condensed_payload_note=lambda *a, **k: host()._condensed_payload_note(*a, **k),
        ticket_image_note=lambda *a, **k: host()._ticket_image_note(*a, **k),
        captured_screens_note=lambda *a, **k: host()._captured_screens_note(*a, **k),
        snapshot_note=lambda *a, **k: host()._snapshot_note(*a, **k),
        reply_notes=lambda *a, **k: host()._prepare_reply_notes(*a, **k),
        server_llm_notice=lambda *a, **k: host()._host_mode_server_llm_notice(
            NoticeArgs(*a, **k)
        ),
    )
