"""MCP registration adapters: machine_tools."""

from handlers import _common, host_check, setup_check, wizard
from tools import selfcheck as selfcheck_module


def register_machine_tools(mcp, Context, rt) -> None:
    """Register the wizard, doctor, host-check, machine-report and selfcheck tools."""
    # DELETED on 2026-08-15: `qa_run_mobile_suite` in dead-code deletion
    # batch D2 with tools/maestro_*.py, and `qa_run_web_suite` /
    # `qa_submit_web_run` in batch D3 with tools/web_runner.py. Both had
    # refused on every install since batches 6/7 retired their features, and
    # a registered tool that can only refuse costs a tester a round trip to
    # learn nothing; a client with either name cached now gets an
    # unknown-tool error instead of a disabled notice, which belongs in the
    # release note. Device capture is unaffected -- qa_capture_screens and
    # qa_list_devices are registered above and still live.
    if not _common.test_cases_only():
        _register_wizard_tool(mcp, Context, rt)
    _register_doctor_tool(mcp, Context, rt)
    _register_host_check_tool(mcp, Context, rt)
    _register_machine_report_tool(mcp, Context, rt)
    _register_selfcheck_tool(mcp, Context, rt)


def _register_wizard_tool(mcp, Context, rt) -> None:
    """Register qa_wizard (full edition only; the caller holds the gate)."""

    @mcp.tool()
    async def qa_wizard(ctx: Context) -> str:
        """Guided entry point: pick a workflow (Test cases / Bug report / Exploratory); it walks you END-TO-END, asking where the feature comes from (description / Jira ticket / mobile screens / Jira + mobile), and returns the suite. Feature Analysis: `qa_feature_analysis`. No parameters; without MCP elicitation, a markdown menu."""
        return await rt._tracked(
            "qa_wizard",
            ctx,
            wizard.handle_wizard(
                **rt._make_elicitors(ctx),
                progress=rt._make_progress(ctx),
            ),
        )


def _register_doctor_tool(mcp, Context, rt) -> None:
    """Register qa-doctor (every edition)."""

    @mcp.tool(name="qa-doctor")
    async def qa_doctor(ctx: Context, fix: bool = False) -> str:
        """Is THIS machine ready? Verdict, environment, integrations (Jira/Atlassian), CLI tooling (adb/xcrun), features, action items. Reports NO model backend: every generative step runs in YOUR chat model. Read-only by default; fix=true repairs what it can (rewrites `.env` with a backup, writes the hosted `atlassian` entry into this client's MCP config). Run first on a new machine."""
        progress = rt._make_progress(ctx)
        # Resolved BEFORE entering _tracked: this is a round trip back to the
        # client, not part of the report's own work, and _tracked owns the
        # in-flight counter that gates the drift restart.
        roots = await rt._workspace_roots(ctx)
        return await rt._tracked_noted(
            "qa-doctor",
            ctx,
            setup_check.handle_setup_check(
                progress=progress, workspace_roots=roots, fix=fix
            ),
        )


# ALL editions, NO flag: flag policy says a new feature ships ON, and this
# one falls into none of the four flag categories -- it makes no outbound
# call, needs no per-install config, is not an experiment, and there is no
# install where "do not tell the tester what this machine allows" is the
# right value. Registered UNCONDITIONALLY here and named in the ambient
# instructions block, which a test cross-checks per edition. Its producer is
# tools/host_privileges.py, top level, so this survives the test-cases-only
# edition, which ships no tools/mobile/ on disk.
def _register_host_check_tool(mcp, Context, rt) -> None:
    """Register qa_host_check (every edition)."""

    @mcp.tool()
    async def qa_host_check(ctx: Context, refresh: bool = False) -> str:
        """What OS is this and can this account elevate? Call BEFORE proposing any install or provision command. ADVISORY ONLY: blocks nothing, names the admin-free route first, reports "cannot determine" as UNDETERMINED, never prompts for a password. Cached per process; `refresh=true` re-probes."""
        return await rt._tracked_noted(
            "qa_host_check",
            ctx,
            host_check.handle_host_check(refresh=bool(refresh)),
        )


# ALL editions, NO flag. It makes no outbound call, needs no per-install
# credential, is not an experiment, and there is no install where "do not
# tell the client what this machine is" is the right value -- so it falls
# into none of the four flag categories and owes no FEATURE_FLAGS entry.
# Deliberately NOT named in tools/guidance.py: it is for a GUI client, not
# for a chat model, and naming it there would spend instruction budget on a
# tool no chat should call.
def _register_machine_report_tool(mcp, Context, rt) -> None:
    """Register qa_machine_report (every edition)."""

    @mcp.tool()
    async def qa_machine_report(ctx: Context, section: str = "all") -> str:
        """Machine-readable rows about THIS install for a non-chat client. JSON: `backend`, `doctor` (component/status/detail/fix_hint rows), `clients` (MCP clients and whether their entry points at THIS install) and `provisioning`. READ-ONLY, safe to poll; `qa-doctor` is the human-read one."""
        return await rt._tracked_noted(
            "qa_machine_report",
            ctx,
            host_check.handle_machine_report(section),
        )


def _register_selfcheck_tool(mcp, Context, rt) -> None:
    """Register the selfcheck tool (every edition)."""

    @mcp.tool(name=selfcheck_module.SELF_TOOL_NAME)
    async def qa_selfcheck(ctx: Context) -> str:
        """Check whether THIS build's own replies still describe it: call every registered tool with defaults; report any reply naming a deleted setting or module, a model this server cannot call, or a tool this edition does not register. Read-only: no `apply=true`, outbound connections blocked. `qa-doctor` covers the MACHINE."""
        return await rt._tracked_noted(
            selfcheck_module.SELF_TOOL_NAME,
            ctx,
            host_check.handle_selfcheck(server=mcp),
        )
