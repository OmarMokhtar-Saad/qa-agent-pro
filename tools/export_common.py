"""Shared output-path preamble and text write for the test-case exporters."""

from __future__ import annotations

from pathlib import Path

from tools.secure_temp import make_secure_temp_path


def resolve_output_path(
    output_path: str | None, suffix: str, prefix: str = "qa_test_cases_"
) -> str:
    """Return output_path, or a secure temp path when None; ensure its parent dir exists."""
    if output_path is None:
        output_path = make_secure_temp_path(prefix=prefix, suffix=suffix)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    return output_path


def write_text_export(output_path: str, content: str) -> None:
    """Write content to output_path as UTF-8 text (OSError propagates)."""
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)
