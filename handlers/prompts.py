"""MCP registration adapters: prompts."""

from typing import NamedTuple

from tools import guidance


class PromptEditions(NamedTuple):
    """The edition gates that decide which prompts exist."""

    test_cases_only: bool
    api_tests: bool
    mobile: bool = False


def _prompt_for(name: str, body: str):
    """One prompt function that returns ITS OWN body (a factory, not a closure over a loop variable)."""

    def _prompt() -> str:
        return body

    _prompt.__name__ = name
    return _prompt


# A loop rather than N decorated functions on purpose: the gate deciding
# WHICH prompts exist already lives in ``tools/guidance.prompt_texts``, and
# re-expressing it here as a second ladder of ``if`` statements is exactly how
# two copies of one rule drift apart. Each body is closed over through a
# factory -- closing over the loop variable directly would hand every prompt
# the LAST body, silently.
def register_prompts(mcp, editions: PromptEditions, rt) -> None:
    """Register one MCP Prompt per guidance workflow this edition can support."""
    texts = guidance.prompt_texts(
        test_cases_only=editions.test_cases_only,
        api_tests=editions.api_tests,
        mobile=editions.mobile,
    )
    for name, body in texts.items():
        fn = _prompt_for(name, body)
        description = guidance.prompt_description(name)
        fn.__doc__ = description
        mcp.prompt(name=name, description=description)(fn)
