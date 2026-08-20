"""Export a TestSuite to a pytest-playwright test skeleton (LT-5 / M-003/M-007).

The second "TestCase -> code artifact" bridge after Gherkin. Without page
selectors we can't emit fully-runnable automation, so this produces a
deterministic *skeleton*: one `def test_*` per case, the steps rendered as
ordered comments with their expected results, and a `# TODO` where the Playwright
calls go. It collects under `pytest` immediately (valid Python) and gives an
automation engineer a structured starting point instead of a blank file.

Never-raise / secure-tempfile / cleanup contract, matching gherkin_exporter.py.
"""

from __future__ import annotations

import keyword
import logging
import re
import tempfile
import time
from pathlib import Path

from tools.models import TestCase, TestSuite, display_requirement_id
from tools.secure_temp import SUBDIR_NAME, make_secure_temp_path

logger = logging.getLogger(__name__)

_HEADER = '''\
"""Auto-generated Playwright test skeletons (QA Assistant).

These are SKELETONS: each test mirrors a manual test case as ordered steps and
expected results. Fill in the Playwright calls (page.goto / get_by_role / expect)
where marked TODO. Requires: pip install pytest-playwright && playwright install.
"""

import pytest
from playwright.sync_api import Page, expect  # noqa: F401
'''


def _slug(text: str, used: set[str]) -> str:
    """A unique, valid python identifier derived from a case title."""
    base = re.sub(r"[^0-9a-zA-Z]+", "_", (text or "").lower()).strip("_")
    base = re.sub(r"_+", "_", base) or "case"
    if base[0].isdigit() or keyword.iskeyword(base):
        base = f"case_{base}"
    name = base[:60]
    candidate = name
    i = 2
    while candidate in used:
        candidate = f"{name}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _comment(text: str) -> str:
    """One-line, comment-safe rendering of a value."""
    return " ".join((text or "").split())


def _test_block(tc: TestCase, slug: str) -> str:
    lines: list[str] = []
    marker = tc.type.value.lower()
    lines.append(f"@pytest.mark.{re.sub(r'[^a-z0-9_]', '_', marker)}")
    lines.append(f"def test_{slug}(page: Page) -> None:")
    lines.append(f'    """{_comment(tc.title)} [{tc.tc_id} / {tc.stable_id}]"""')
    # F06: the acceptance criterion this skeleton belongs to. Omitted entirely
    # when the case carries no usable tag -- a comment reading "Requirement:"
    # with nothing after it is worse than no line.
    requirement = display_requirement_id(getattr(tc, "requirement_id", ""))
    if requirement:
        lines.append(f"    # Requirement: {_comment(requirement)}")
    if tc.preconditions and tc.preconditions.strip():
        lines.append(f"    # Precondition: {_comment(tc.preconditions)}")
    lines.append("    # TODO: navigate to the feature under test, e.g. page.goto(...)")
    for s in tc.steps:
        data = f" [data: {_comment(s.test_data)}]" if s.test_data else ""
        lines.append(f"    # Step {s.step_number}: {_comment(s.action)}{data}")
        lines.append(f"    #   Expect: {_comment(s.expected_result)}")
    lines.append("    # TODO: implement the actions above with Playwright and assert")
    lines.append('    pytest.skip("skeleton — implement the steps above")')
    return "\n".join(lines)


def generate_playwright_script(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to a pytest-playwright skeleton (.py) and return its path.

    Raises OSError only on genuine file I/O errors (callers wrap in try/except).
    """
    if output_path is None:
        output_path = make_secure_temp_path(prefix="qa_test_cases_", suffix=".py")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    used: set[str] = set()
    blocks = [_test_block(tc, _slug(tc.title, used)) for tc in suite.test_cases]
    content = _HEADER + "\n\n" + "\n\n\n".join(blocks) + "\n"

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    logger.info(
        "Playwright skeleton written: %s (%d tests)",
        output_path,
        len(suite.test_cases),
    )
    return output_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete stale Playwright skeletons (.py) older than max_age_seconds.

    NB-020: sweep ONLY the app's own secure per-app subdir — sweeping the shared
    /tmp root for ``qa_test_cases_*.py`` risked deleting an unrelated user's file
    that happened to match the pattern.
    """
    base = Path(tempfile.gettempdir()) / SUBDIR_NAME
    if not base.is_dir():
        return 0
    now = time.time()
    deleted = 0
    for path in base.glob("qa_test_cases_*.py"):
        try:
            age = now - path.stat().st_mtime
            if age > max_age_seconds:
                path.unlink(missing_ok=True)
                deleted += 1
                logger.info(
                    "Cleaned up stale Playwright temp file: %s (age %.0fs)",
                    path,
                    age,
                )
        except OSError:
            logger.warning("Could not check/delete temp file: %s", path)
    if deleted:
        logger.info("Playwright cleanup: removed %d stale file(s)", deleted)
    return deleted
