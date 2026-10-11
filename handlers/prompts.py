"""MCP registration adapters: prompts."""

from tools import guidance


def register_prompts(
    mcp, *, test_cases_only: bool, api_tests: bool, mobile: bool = False, rt
) -> None:
    """Register one MCP Prompt per guidance workflow this edition can support.

    A loop rather than N decorated functions on purpose: the gate deciding
    WHICH prompts exist already lives in ``tools/guidance.prompt_texts``, and
    re-expressing it here as a second ladder of ``if`` statements is exactly how
    two copies of one rule drift apart. Each body is closed over through a
    factory -- closing over the loop variable directly would hand every prompt
    the LAST body, silently.
    """
    for name, body in guidance.prompt_texts(
        test_cases_only=test_cases_only, api_tests=api_tests, mobile=mobile
    ).items():

        def _make(text: str):
            def _prompt() -> str:
                return text

            return _prompt

        fn = _make(body)
        fn.__name__ = name
        description = guidance.prompt_description(name)
        fn.__doc__ = description
        mcp.prompt(name=name, description=description)(fn)
