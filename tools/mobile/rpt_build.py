from __future__ import annotations

from tools.mobile.report_frames import _SHOTS, _shots
from tools.mobile.report_media import _IMAGES, _image_book
from tools.mobile.report_text import _SECRETS, _run_secrets
from tools.mobile.rpt_input import _DocInput, _merged_library
from tools.mobile.rpt_page import _document_body


# The page, with this run's inlined frames funded ONCE for the whole build.
#
# A wrapper rather than a parameter on every builder: see :data:`_SHOTS`. The
# signature is unchanged for every caller, and the book is RESET in ``finally``
# so one report's frames can never be read by the next.
#
# Funded against :func:`_merged_library` -- the same call the body makes -- so
# the keys this pays for are exactly the keys the frames resolve on.
def _document(**kwargs) -> str:
    token = _SHOTS.set(
        _shots(
            str(kwargs.get("run_id") or ""),
            _merged_library(kwargs.get("screens"), kwargs.get("observations")),
        )
    )
    images = _IMAGES.set(_image_book(kwargs.get("run_id") or ""))
    secrets = _SECRETS.set(_run_secrets(kwargs.get("manifest"), kwargs.get("cases")))
    try:
        return _document_body(_DocInput(**kwargs))
    finally:
        _SECRETS.reset(secrets)
        _IMAGES.reset(images)
        _SHOTS.reset(token)
