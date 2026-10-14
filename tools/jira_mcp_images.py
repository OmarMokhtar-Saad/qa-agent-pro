"""Jira MCP: images helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
import re

from tools import settings_port

logger = logging.getLogger("tools.jira_mcp")

# Images embedded IN the description rather than uploaded as attachments:
# markdown/ADF renderings (![](blob:...) / ![alt](url)) and Jira wiki syntax
# (!screen.png!). TICKET-5645 carries its three UI screens this way, and a host
# that reshapes the issue JSON can drop `attachment` while the description
# survives -- so this is the signal that cannot be trimmed away.
_IMAGE_REF_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)"  # markdown image, incl. ![](blob:...)
    r"|blob:https?://"  # a bare Atlassian media blob URL
    r"|!\S+\.(?:png|jpe?g|gif|webp|bmp)[|!]",  # Jira wiki !file.png! / !f.png|x!
    re.IGNORECASE,
)


# Absurd inputs are untrusted text; stop counting well before it matters.
_MAX_IMAGE_REFS = 50


def _count_image_refs(text: object) -> int:
    """How many images the DESCRIPTION embeds. 0 when the flag is off. Never raises."""
    try:
        if not settings_port.get().jira_fetch_images:
            return 0
        body = str(text or "")
        if not body:
            return 0
        return min(_MAX_IMAGE_REFS, len(_IMAGE_REF_RE.findall(body)))
    except Exception:
        logger.exception("Counting embedded image references failed")
        return 0


# A short bold/heading label on the line directly above an embedded image
# ("**UI#01**", "## Screen 2"), captured so the disclosure can NAME the
# screens the tester should attach instead of a bare count. Untrusted text:
# each label is charset-gated (no ':', '&' or '=' -- a URL can never pass)
# and length-capped, and at most _MAX_IMAGE_LABELS are kept.
_IMAGE_LABEL_RE = re.compile(r"(?:\*\*|#+\s*)([^*\n]{1,48}?)\s*(?:\*\*)?\s*$")


_LABEL_SAFE_RE = re.compile(r"^[\w #.\-/()\[\]]{1,32}$")


_MAX_IMAGE_LABELS = 8


# The label can only be on the line ABOVE the reference, so only that much text
# needs looking at. Slicing body[: m.start()] instead made the walk quadratic in
# the untrusted description (1.2s at 40k refs, against 0.012s for the counter
# over the same body). 8 KB is ~68x the 120 characters actually read.
#
# HONEST ABOUT THE BOUND: this is narrower than the old walk, not equivalent.
# Two shapes lose a label they used to find -- a label separated from its image
# by more than 8 KB of whitespace, and a first label appearing after reference
# #500. Neither is a ticket a human wrote (_MAX_IMAGE_REFS is 50), and the
# failure direction is a missing label, never a wrong one.
_LABEL_LOOKBEHIND_CHARS = 8192


_MAX_LABEL_SCAN_REFS = 500


def _label_before(body: str, start: int) -> str | None:
    """The safe short label on the last line before ``start``, or None."""
    window = body[max(0, start - _LABEL_LOOKBEHIND_CHARS) : start]
    window = window.rstrip()[-120:]
    last_line = window.splitlines()[-1].strip() if window else ""
    lm = _IMAGE_LABEL_RE.search(last_line)
    if not lm:
        return None
    label = lm.group(1).strip()
    return label if _LABEL_SAFE_RE.fullmatch(label) else None


def _scan_labels(body: str) -> list:
    """Deduped safe labels found before the image refs in *body*, scan- and label-capped."""
    labels: list = []
    for scanned, m in enumerate(_IMAGE_REF_RE.finditer(body)):
        if scanned >= _MAX_LABEL_SCAN_REFS:
            break
        label = _label_before(body, m.start())
        if label is None:
            continue
        if label not in labels:
            labels.append(label)
        if len(labels) >= _MAX_IMAGE_LABELS:
            break
    return labels


# Short labels the DESCRIPTION gives its embedded images ("UI#01"), in
# document order, deduped, gated and capped. Empty when the flag is off, nothing is
# labelled, or on ANY error. Never raises.
def _image_ref_labels(text: object) -> list:
    """Short labels the DESCRIPTION gives its embedded images ("UI#01"), deduped, capped."""
    try:
        if not settings_port.get().jira_fetch_images:
            return []
        body = str(text or "")
        if not body:
            return []
        return _scan_labels(body)
    except Exception:
        logger.exception("Extracting embedded image labels failed")
        return []
