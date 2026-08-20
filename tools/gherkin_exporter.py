"""Export a TestSuite to a Gherkin/BDD .feature file (T-09 / I-066 / M-001).

This is the first "TestCase -> code artifact" bridge: deterministic, zero LLM
cost, and the output collects under `behave --dry-run` / pytest-bdd. Mapping:

    module          -> Feature
    title           -> Scenario
    type/priority/   -> @tags
      risk_label
    preconditions   -> Given
    action(+data)   -> When / And
    expected_result -> Then / And

Scenarios are grouped by module so each module becomes one Feature block.
Follows the same never-raise / tempfile / cleanup contract as csv_exporter.py.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

from tools.models import TestCase, TestSuite, display_requirement_id
from tools.secure_temp import SUBDIR_NAME, make_secure_temp_path

logger = logging.getLogger(__name__)


def _one_line(text: str) -> str:
    """Collapse a value to a single safe Gherkin step line.

    Newlines become spaces and triple-quotes are defanged so a value can never
    open/close a docstring or break the step grammar.
    """
    return " ".join((text or "").split()).replace('"""', "'''")


def _tag(value: str) -> str:
    """Turn a label into a valid @tag token (no spaces)."""
    return "@" + "_".join(str(value).split())


# F06 (2026-08-19): a tag is the idiomatic Gherkin carrier for traceability
# (`@AC-001`), and BDD runners filter on it. Allowlist-gated rather than passed
# through _tag: `requirement_id` is free text a model wrote, and a value carrying
# an `@`, a quote or a newline would emit a line that is not valid Gherkin. An
# unusable value emits NO tag -- the xlsx/csv Requirement ID column still carries
# it verbatim, so nothing is lost, only this one representation is declined.
_TAG_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_TAG_MAX = 40


def _requirement_tag(tc: TestCase) -> str:
    """``@AC-001`` for a case with a usable requirement tag, else ""."""
    try:
        value = display_requirement_id(getattr(tc, "requirement_id", ""))
        if not value or len(value) > _TAG_MAX:
            return ""
        if not all(ch in _TAG_SAFE for ch in value):
            return ""
        return "@" + value
    except Exception:  # pragma: no cover - defensive
        logger.debug("requirement tag rendering failed", exc_info=True)
        return ""


def _scenario_tags(tc: TestCase) -> str:
    tags = [_tag(tc.type.value), _tag(tc.priority.value)]
    if tc.risk_label:
        tags.append(_tag(f"risk_{tc.risk_label}"))
    requirement = _requirement_tag(tc)
    if requirement:
        tags.append(requirement)
    return "  " + " ".join(tags)


def _scenario_block(tc: TestCase) -> list[str]:
    lines: list[str] = [_scenario_tags(tc)]
    lines.append(f"  Scenario: {_one_line(tc.title)}")

    if tc.preconditions and tc.preconditions.strip():
        lines.append(f"    Given {_one_line(tc.preconditions)}")

    # Actions -> When / And (test data appended inline when present)
    for i, step in enumerate(tc.steps):
        keyword = "When" if i == 0 else "And"
        action = _one_line(step.action)
        if step.test_data and step.test_data.strip():
            action = f"{action} (with {_one_line(step.test_data)})"
        lines.append(f"    {keyword} {action}")

    # Expected results -> Then / And
    for i, step in enumerate(tc.steps):
        keyword = "Then" if i == 0 else "And"
        lines.append(f"    {keyword} {_one_line(step.expected_result)}")

    return lines


def generate_feature_file(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to a .feature file and return its path.

    Raises OSError only on genuine file I/O errors (matches csv_exporter);
    callers in app.py wrap export generation in try/except.
    """
    if output_path is None:
        output_path = make_secure_temp_path(prefix="qa_test_cases_", suffix=".feature")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Group scenarios by module -> one Feature per module, order preserved.
    by_module: "OrderedDict[str, list[TestCase]]" = OrderedDict()
    for tc in suite.test_cases:
        by_module.setdefault(tc.module or "Untitled", []).append(tc)

    blocks: list[str] = []
    for module, cases in by_module.items():
        block = [f"Feature: {_one_line(module)}", ""]
        for tc in cases:
            block.extend(_scenario_block(tc))
            block.append("")  # blank line between scenarios
        blocks.append("\n".join(block).rstrip() + "\n")

    content = "\n\n".join(blocks).rstrip() + "\n"
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    logger.info(
        "Gherkin written: %s (%d scenarios, %d features)",
        output_path,
        len(suite.test_cases),
        len(by_module),
    )
    return output_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete qa_test_cases_*.feature temp files older than max_age_seconds."""
    tmp_dir = Path(tempfile.gettempdir())
    now = time.time()
    deleted = 0
    for base in (tmp_dir / SUBDIR_NAME, tmp_dir):
        for path in base.glob("qa_test_cases_*.feature"):
            try:
                age = now - path.stat().st_mtime
                if age > max_age_seconds:
                    path.unlink(missing_ok=True)
                    deleted += 1
                    logger.info(
                        "Cleaned up stale Gherkin temp file: %s (age %.0fs)", path, age
                    )
            except OSError:
                logger.warning("Could not check/delete temp file: %s", path)
    if deleted:
        logger.info("Gherkin cleanup: removed %d stale file(s)", deleted)
    return deleted
