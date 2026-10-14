"""Protocol-native guidance: the server ``instructions`` block and the MCP
Prompts, as pure text.

Every tester-facing workflow this server implements is a HANDSHAKE -- prepare,
then submit -- because no model runs here: the calling agent's own chat model
does every generative step. That contract lives in the tool descriptions and in
the payloads ``agents/host_*.py`` builds, which means a client only learns it
AFTER it has already called a tool, and only for the tool it happened to call.
Two things fix that at the protocol level, and this module is the text for both:

* ``server_instructions`` -- the ``instructions`` string FastMCP sends once at
  ``initialize``. Ambient, always in the client's context, so it has to be
  SHORT: it names the tool ORDER and the three replies that are NOT the
  answer, and nothing else.
* ``prompt_texts`` -- named MCP Prompts the tester invokes deliberately
  (``/mcp__qa-agents__qa_generate`` in Claude Code). These may be long, because
  the client pays for them only when one is chosen.

Two rules this module exists to keep:

1. **It DESCRIBES the flow; it never duplicates a payload.** The authoritative
   instruction text for a generation run is the one ``host_mode`` puts in the
   prepare payload, resolved against that run's flags and that ticket's
   content. A copy here would drift silently and a tester would never know
   which one was stale. So the text says *which tool comes next and what to do
   with each reply shape*, and stops there.
2. **It is EDITION-AWARE by parameter, not by import.** Both entry points take
   the gates as booleans, so this module imports nothing internal -- no
   ``config.settings``, no ``handlers`` -- and the caller
   (``mcp_server.build_server``) resolves each gate exactly once, from the same
   expression that decides whether the tool is registered at all. Guidance
   naming a tool the tester's edition does not have is worse than no guidance:
   it sends the agent to a tool call that can only fail.

There is NO settings flag here. Per the flag policy this ships ON: guidance a
tester never sees is dead text, and an off-path nobody exercises is a bug
waiting for the one install that flips it.
"""

from __future__ import annotations

from tools.guidance_instr import (  # noqa: F401
    _INSTRUCTIONS_API,
    _INSTRUCTIONS_CORE,
    _INSTRUCTIONS_FULL,
    _INSTRUCTIONS_MOBILE,
)
from tools.guidance_p_bug import _P_API, _P_BUG, _P_EXPLORE  # noqa: F401
from tools.guidance_p_flow import _P_GENERATE, _P_JIRA  # noqa: F401
from tools.guidance_p_mobile import _P_MOBILE  # noqa: F401
from tools.guidance_prompts import (  # noqa: F401
    _API_ONLY,
    _FULL_ONLY,
    _MOBILE_ONLY,
    _PROMPTS,
)

# --- Server instructions ---------------------------------------------------- #
# Sent once at initialize and resident in the client's context for the whole
# session, so length is a real cost paid on every turn. ``MAX_INSTRUCTION_LINES``
# is the budget, pinned by a test: an ambient block that grows without a ceiling
# is how a helpful paragraph becomes a permanent tax.
# The arithmetic is the justification rather than headroom-by-feel. The mobile
# block is two lines of text and ``server_instructions`` joins its parts with a
# blank line, so the worst case is core + full + api + mobile. Measured before
# the host-check line: 42 there, 39 for full + api without the mobile block
# (then the default, when a flag gated the lane), 32 test-cases-only.
#
# The host-check line is ONE line and it lives in CORE, not in a gated block,
# because ``qa_host_check`` is registered in every edition -- so it costs +1 on
# every row: worst case 43, default install 40, test-cases-only 33. It buys the
# one failure an ambient block can actually prevent here: an agent proposing an
# elevated install command to a tester whose account cannot run it, which is a
# dead end the tester cannot diagnose and the server used to print regardless.
#
# The Jira-auth line ("Jira auth fails: STOP ...") is ONE more line in CORE, so
# +1 on every row again: worst case 44.
#
# The no-raw-adb line is ONE line in the mobile block: a Cursor agent read the
# qa_mobile_test description, called no tool and drove the emulator with raw
# adb, bypassing the destructive guard, keyboard restore and evidence. It costs
# +1 only where the lane is registered: worst case 45.
MAX_INSTRUCTION_LINES = 45


# The ``instructions=`` block for this edition.
#
# ``test_cases_only`` mirrors ``handlers._common.test_cases_only()``,
# ``api_tests`` mirrors ``settings.qa_api_test_enabled`` and ``mobile``
# mirrors ``handlers._common._mobile_lane_enabled()`` -- the caller passes
# the SAME expressions that gate registration, so a tool can never be named
# here and absent there.
#
# ``mobile`` defaults to False so an existing caller keeps its behaviour. Its
# block is a SIBLING of the full block, never nested inside it: the caller's
# ``mobile`` argument is already ``_mobile_lane_enabled()``, so nesting it
# under ``not test_cases_only`` was a SECOND copy of an edition gate -- the
# same defect that kept the lane off every distribution build until
# 2026-09-04, and it would have survived that fix, leaving a dist tester with
# three registered tools and an instructions block that never names them.
# ``prompt_texts`` gates the ``qa_mobile_run`` prompt on ``mobile`` alone;
# these two now agree.
def server_instructions(
    *, test_cases_only: bool, api_tests: bool, mobile: bool = False
) -> str:
    """The ``instructions=`` block for this edition."""
    parts = [_INSTRUCTIONS_CORE]
    if not test_cases_only:
        parts.append(_INSTRUCTIONS_FULL)
        if api_tests:
            parts.append(_INSTRUCTIONS_API)
    if mobile:
        parts.append(_INSTRUCTIONS_MOBILE)
    return "\n\n".join(p.strip("\n") for p in parts)


# --- Prompts ----------------------------------------------------------------- #
# One entry per MCP Prompt: name -> (one-line description, body). The
# description is what a client lists in its prompt picker, so it reads as an
# imperative the tester recognises, not as a module summary.


def prompt_texts(
    *, test_cases_only: bool, api_tests: bool, mobile: bool = False
) -> dict[str, str]:
    """``{prompt_name: body}`` for this edition, in registration order.

    The same gates as :func:`server_instructions`, resolved by the caller. A
    prompt is omitted rather than degraded: half a workflow whose middle step
    names a tool that is not there wastes more of the tester's time than no
    prompt at all.
    """
    out: dict[str, str] = {}
    for name, (_desc, body) in _PROMPTS.items():
        if name in _FULL_ONLY and test_cases_only:
            continue
        if name in _API_ONLY and not (api_tests and not test_cases_only):
            continue
        if name in _MOBILE_ONLY and not mobile:
            continue
        out[name] = body
    return out


def prompt_description(name: str) -> str:
    """The one-line description a client shows in its prompt picker."""
    entry = _PROMPTS.get(name)
    return entry[0] if entry else ""
