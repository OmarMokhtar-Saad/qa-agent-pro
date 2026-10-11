"""Moved from handlers (refactor P2): api."""

from __future__ import annotations

import logging

from handlers import _common, _reply, explore_bug
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")

# The coach-memory bounds (count of areas, length of a label, count of
# findings) live in tools.coach_memory beside the list they bound, and every
# writer -- including handle_submit_explore_step below -- imports them from
# there. A count cap that lived HERE alone was walked around by the other
# writer until 2026-09-06.


# ── API test agent (chat-only; qa_api_test_enabled) ──────────────────────────
def _render_api_write_result(r: dict) -> str:
    if not isinstance(r, dict):
        return "⚠️ write returned an unexpected value."
    if r.get("error") and r.get("status") not in ("failed", "failed_not_discarded"):
        return f"⚠️ {r['error']}"
    status = r.get("status")
    if status == "dry_run":
        java = "\n\n".join(
            f"### {p}\n```java\n{s}\n```"
            for p, s in (r.get("java_sources") or {}).items()
        )
        return (
            f"## API test — dry run\n**branch:** `{r.get('branch')}` (off `{r.get('base_commit')}`)\n"
            f"**targets:** {', '.join(str(t) for t in (r.get('targets') or []))}\n\n{java}\n\n"
            f"```yaml\n{r.get('contract_yaml', '')}\n```\n\n{r.get('note', '')}"
        )
    if status == "committed":
        return f"✅ Committed on `{r.get('branch')}` (`{r.get('commit')}`). {r.get('note', '')}"
    if status in ("committed_head_not_restored",):
        return f"✅ Committed on `{r.get('branch')}` — {r.get('note', '')}"
    if status == "unchanged":
        return f"ℹ️ {r.get('note', '')}"
    if status in ("failed", "failed_not_discarded"):
        out = r.get("output")
        out = out if isinstance(out, str) else ("" if out is None else str(out))
        tail = f"\n\n```\n{out[-2000:]}\n```" if out else ""
        return f"⚠️ {r.get('error')}. {r.get('note', '')}{tail}"
    return f"⚠️ {r.get('error') or 'write failed'}"


async def _api_project_menu() -> str:
    """The card offered when neither ``create`` nor ``use`` was passed."""
    from agents.api_test_agent import _safe
    from tools import api_project

    listed = await api_project.list_projects()
    rows = listed.get("content") or []
    lines = [
        "## API test project",
        "",
        "Every API flow starts with a project. Two ways forward \u2014 ask the tester which:",
        "",
        '1. **A new project** \u2014 `qa_api_project(create="<name>")`. The name becomes the '
        "Maven artifactId and the Java package, so it must be lowercase letters, digits "
        "and single hyphens (for example `wallet-api`).",
        '2. **Continue with an existing one** \u2014 `qa_api_project(use="<name or path>")`.',
        "",
    ]
    if rows:
        lines.append("Already registered:")
        lines += [
            "- `"
            + _safe(r.get("name"), 60)
            + "` \u2014 `"
            + _safe(r.get("path"), 200)
            + "`"
            for r in rows
        ]
    else:
        lines.append("No projects are registered yet.")
    await _common._audit("mcp_api_project_menu")
    return "\n".join(lines)


async def _api_project_use(use: str) -> str:
    """The ``use=`` path: resolve a registered project and confirm it."""
    from agents.api_test_agent import _safe
    from tools import api_project

    resolved = await api_project.resolve_project(use)
    if resolved.get("error"):
        return "\u26a0\ufe0f " + _safe(resolved["error"], 500)
    identity = resolved.get("content") or {}
    await _common._audit("mcp_api_project_use", entity_id=identity.get("name"))
    notes = "\n".join("- " + _safe(n, 300) for n in (identity.get("notes") or []))
    return "\n".join(
        p
        for p in [
            "\u2705 Using project **" + _safe(identity.get("name"), 60) + "**",
            "",
            "- path: `" + _safe(identity.get("path"), 200) + "`",
            "- java package: `" + _safe(identity.get("package_root"), 100) + "`",
            notes,
            "",
            "Next: `qa_prepare_api_tests` with the endpoint source \u2014 a curl command, an "
            "OpenAPI spec, or a plain description.",
        ]
        if p
    )


async def _api_project_create(create: str, progress: _common.ProgressCb) -> str:
    """The ``create=`` path: bootstrap from the template, register, reply."""
    import asyncio as _asyncio

    from config.settings import settings as _s
    from tools import api_project, framework_template

    repo = (_s.qa_api_template_repo or "").strip()
    if not repo:
        return (
            "\u26a0\ufe0f Set `QA_API_TEMPLATE_REPO` to the public template repo, e.g. `owner/name`, "
            "in your `.env` and restart \u2014 a new project is created from its latest release. "
            "Nothing was created."
        )
    projects_dir = (_s.qa_api_projects_dir or "").strip()
    if not projects_dir:
        return (
            "\u26a0\ufe0f Set `QA_API_PROJECTS_DIR` to the folder new API projects should be created "
            "in, then restart. Nothing was created."
        )
    await _common._emit(
        progress,
        "\U0001f4e6 Fetching the project template and proving it compiles\u2026",
    )
    created = await _asyncio.to_thread(
        framework_template.bootstrap,
        name=create,
        dest_root=projects_dir,
        repo=repo,
    )
    if created.get("error"):
        return await _api_project_create_failed(created)
    registered = await api_project.register_project(
        name=created["name"],
        path=created["path"],
        artifact_id=created["artifact_id"],
        package_root=created["package_root"],
        template_repo=created["template_repo"],
        template_version=created["template_version"],
    )
    await _common._audit("mcp_api_project_created", entity_id=created["name"])
    return _api_project_created_reply(created, registered)


def _api_project_created_reply(created: dict, registered: dict) -> str:
    """The success card for a created project, with any registry warning."""
    from agents.api_test_agent import _safe

    warning = ""
    # A colliding slug repoints an existing row. R8 records it; disclose it HERE
    # too, or the create path stays silent about it (review MINOR).
    moved_from = str((registered.get("content") or {}).get("repointed_from") or "")
    if moved_from:
        warning += (
            "\n\n\u26a0\ufe0f A project named that was already registered at `"
            + _safe(moved_from, 200)
            + "` and now points here. If those are two different projects, give one"
            " of them a different name."
        )
    if registered.get("error"):
        warning = (
            "\n\n\u26a0\ufe0f The project was created but could not be added to the registry ("
            + _safe(registered["error"], 200)
            + '); continue with `qa_api_project(use="'
            + _safe(created["path"], 200)
            + '")`.'
        )
    return (
        "\u2705 Created project **" + _safe(created["name"], 60) + "**\n\n"
        "- path: `" + _safe(created["path"], 200) + "`\n"
        "- java package: `" + _safe(created["package_root"], 100) + "`\n"
        "- template: `"
        + _safe(created["template_repo"], 120)
        + "` `"
        + _safe(created["template_version"], 60)
        + "`\n"
        "- one local commit, no remote, nothing pushed; `-B test-compile` is green\n\n"
        "Next: `qa_prepare_api_tests` with the endpoint source." + warning
    )


async def _api_project_create_failed(created: dict) -> str:
    """The reply for a template bootstrap that returned an error."""
    from agents.api_test_agent import _safe

    output = created.get("output")
    # Build output comes from the DOWNLOADED template repo: externally-
    # sourced text reaching a model, so it goes through wrap_untrusted like
    # every other such payload (hard constraint). The backtick swap is a
    # SEPARATE concern -- it stops the output breaking out of the fence of
    # the server-authored card around it.

    fenced = (
        wrap_untrusted(
            "template build output",
            str(output)[-2000:].replace("```", "'''"),
        )
        if output
        else ""
    )
    tail = "\n\n```\n" + fenced + "\n```" if output else ""
    await _common._audit(
        "mcp_api_project_create_failed", detail={"gate": created.get("gate")}
    )
    return (
        "\u26a0\ufe0f " + _safe(created["error"], 400) + " Nothing was created." + tail
    )


async def handle_api_project(
    create: str = "", use: str = "", *, progress: _common.ProgressCb = None
) -> str:
    """PROJECT step of the chat-only API test agent (Phase A).

    Creates a new project from the public template repo, or continues with an
    existing one by registered name or by path. NEVER uses ctx.elicit -- markdown
    cards only (the blocking-elicitation class, 2026-08-10: those dialogs render
    collapsed-and-required in Cursor and hang the call). The card is
    server-authored text the host model reads as trusted, so every value it
    echoes -- a project name, a path, a marker field -- is fence-sanitised.
    Never raises."""
    from config.settings import settings as _s

    if not _s.qa_api_test_enabled:
        return "\u26a0\ufe0f The API test agent is off. Set QA_API_TEST_ENABLED=true and restart the server."
    create = (create or "").strip()
    use = (use or "").strip()
    if create and use:
        return '\u26a0\ufe0f Pass either `create="<new project name>"` or `use="<name or path>"` \u2014 not both.'
    try:
        if not create and not use:
            return await _api_project_menu()
        if use:
            return await _api_project_use(use)
        return await _api_project_create(create, progress)
    except Exception as exc:
        logger.exception("handle_api_project failed")
        return f"\u26a0\ufe0f API project step failed ({type(exc).__name__}) \u2014 see the server log."


def _api_card_with_intake(result: dict) -> str:
    """Every API intake card must name the intake it belongs to (audit F9).

    ``api_test_agent.render_card`` prints the `qa_prepare_api_tests(intake_id=...)`
    call itself, but the PREREQUISITE cards -- does this endpoint need auth,
    static token or login flow, does it depend on another endpoint, which fields
    carry forward -- and the resume card printed when a dependency pops back to
    its caller do NOT. So the host model answered "no" with no ``intake_id``, the
    server opened a FRESH intake, and the auth and dependency answers already
    collected for that endpoint were silently abandoned. Appended at the one
    place every card leaves the server, so a card added later cannot forget it;
    a card that already names its id (render_card's) is left byte-identical."""
    card = str(result.get("card") or "")
    intake_id = str(result.get("intake_id") or "").strip()
    if not intake_id or intake_id in card:
        return card
    return (
        card
        + '\n\nAnswer it with `qa_prepare_api_tests(intake_id="'
        + intake_id
        + '", input=<their answer>)` — that exact `intake_id`, so this '
        "endpoint's auth and dependency answers are not lost."
    )


async def _api_prepare_error_reply(r: dict, disc: str) -> str:
    """Render a ``kind="error"`` intake result with its declared recovery target."""
    # THE INVARIANT (2026-09-02, after three rounds on this one class):
    #   a reply may name an intake only if its PRODUCER declared that
    #   intake as the recovery target.
    # Round 1 appended the id for `card`/`confirm` only. Round 2 added
    # the two `kind="error"` exits a reviewer had named. Round 3 moved
    # to a chokepoint that INFERRED the target from the caller's
    # argument, and was reverted: it named a test-case prep as an
    # answerable API intake, and at the F-3 expiry exit it named the
    # child while the prose said restart the parent -- a host that
    # obeyed overwrote a completed login intake.
    #
    # So the id is DECLARED at each exit as `recover_with`, and this
    # renders exactly that. `None` means "there is nothing to answer;
    # start fresh", which the caller's argument cannot express.
    # `tests/test_api_recover_with_2026_09_02.py` asserts by AST that
    # every `kind="error"` return in the agent carries the key.
    # 2026-09-02 review: a declared None and a FORGOTTEN key rendered
    # identically here, so the AST test was the only thing standing
    # between the two -- and a test cannot see an exit built by a helper
    # or in another module. The sentinel makes the difference
    # observable at runtime: an undeclared exit is logged by name, so a
    # gap shows up in the server log instead of silently reading as
    # "there is nothing to answer".
    _declared = r.get("recover_with", _reply._NO_RECOVERY_DECLARED)
    if _declared is _reply._NO_RECOVERY_DECLARED:
        # Round-3 review, L3: name the EXIT (its bounded error text), not the
        # test file, so an operator can tell which exit has the gap.
        logger.warning(
            "prepare_api_tests error exit declared no recover_with, "
            "treating it as 'nothing to answer'; the exit said: %s",
            _reply._clip_echo(r.get("error"), 160),
        )
        _declared = None
    _recover = str(_declared or "").strip()
    await _common._audit("mcp_api_prepare_error", entity_id=_recover)
    return (
        "⚠️ "
        + _api_card_with_intake(
            {"card": str(r.get("error") or ""), "intake_id": _recover}
        )
        + disc
    )


def _api_envelope_reply(r: dict) -> str:
    """The generate-the-suite task for the host, from an ``envelope`` result."""
    return explore_bug.shape_host_task(
        "Generate the API test suite — your turn",
        r.get("task_id", ""),
        r.get("envelope") or {},
        "qa_submit_api_tests",
        'field `suite` (JSON {"cases": [...]})',
    )


async def handle_prepare_api_tests(
    input: str = "",
    intake_id: str = "",
    confirmed: bool = False,
    project: str = "",
    *,
    progress: _common.ProgressCb = None,
) -> str:
    """PREPARE half of the chat-only API test agent. The server makes NO model
    call — it returns an intake card or a task envelope your chat model acts on.
    Never raises."""
    from config.settings import settings as _s

    if not _s.qa_api_test_enabled:
        return "⚠️ The API test agent is off. Set QA_API_TEST_ENABLED=true and restart the server."
    try:
        from agents import api_test_agent as _agent

        await _common._emit(progress, "🧭 Processing the API endpoint intake…")
        r = await _agent.prepare_api_tests(
            input or "",
            (intake_id or "").strip(),
            bool(confirmed),
            (project or "").strip(),
        )
        kind = r.get("kind")
        disc = ("\n\n" + r["disclosure"]) if r.get("disclosure") else ""
        if kind == "error":
            return await _api_prepare_error_reply(r, disc)
        if kind in ("card", "confirm"):
            await _common._audit(
                f"mcp_api_prepare_{kind}", entity_id=r.get("intake_id")
            )
            return _api_card_with_intake(r) + disc
        if kind == "ai_fill_prompt":
            await _common._audit("mcp_api_prepare_aifill", entity_id=r.get("intake_id"))
            return (r.get("prompt") or "") + disc
        if kind == "envelope":
            await _common._audit("mcp_api_prepare_envelope", entity_id=r.get("task_id"))
            return _api_envelope_reply(r) + disc
        return "⚠️ Unexpected intake result."
    except Exception as exc:
        logger.exception("handle_prepare_api_tests failed")
        return f"⚠️ API test preparation failed ({type(exc).__name__}) — see the server log."


async def handle_submit_api_tests(
    task_id: str, suite: str, *, progress: _common.ProgressCb = None
) -> str:
    """SUBMIT half: ground the cases YOUR chat model wrote, persist the suite.
    Never raises."""
    from config.settings import settings as _s

    if not _s.qa_api_test_enabled:
        return "⚠️ The API test agent is off (QA_API_TEST_ENABLED)."
    task_id = (task_id or "").strip()
    if not task_id:
        return "⚠️ Pass the `task_id` from `qa_prepare_api_tests`."
    if not (suite or "").strip():
        return '⚠️ Send your generated cases as `suite` (JSON {"cases": [...]}).'
    try:
        from agents import api_test_agent as _agent

        await _common._emit(
            progress, "🔎 Grounding the generated cases against the contract…"
        )
        r = await _agent.submit_api_suite(task_id, suite)
        if r.get("error"):
            return f"⚠️ {r['error']}. A task id is one-shot — start again with `qa_prepare_api_tests`."
        await _common._audit("mcp_api_submit", entity_id=r.get("suite_id"))
        parts = [
            r.get("markdown", ""),
            "",
            r.get("disclosure", ""),
            "",
            f'Review the cases, then `qa_write_api_test(suite_id="{r["suite_id"]}")` for a dry-run of the Java.',
        ]
        return "\n".join(p for p in parts if p is not None)
    except Exception as exc:
        logger.exception("handle_submit_api_tests failed")
        return f"⚠️ API suite submission failed ({type(exc).__name__}) — see the server log."


async def _api_write_target(project: str) -> tuple[str, str, list, str]:
    """Pick the framework repo to write into.

    Returns ``(framework_path, package_root, project_notes, refusal)``; a
    non-empty *refusal* is the finished reply and nothing else is usable.
    """
    from agents.api_test_agent import _safe
    from config.settings import settings as _s

    # Project selection is EXPLICIT (A5): the tester names the project on
    # the call, so nothing depends on hidden "current project" state that a
    # second editor window could silently change. Naming none falls back to
    # QA_API_FRAMEWORK_PATH, so everything that works today keeps working.
    if not project:
        if not _s.qa_api_framework_path:
            return (
                "",
                "",
                [],
                '⚠️ Name a project with `project="<name>"` (see `qa_api_project()`), or set '
                "QA_API_FRAMEWORK_PATH to your api-automation-framework checkout and restart.",
            )
        return _s.qa_api_framework_path, "", [], ""
    from tools import api_project as _projects

    # adopt=False ON PURPOSE. Naming a PATH here must never write
    # .qa-api-project.json into the tester's repo as a side effect of a
    # write: adoption is a visible, tester-initiated qa_api_project(use=)
    # action, which is also the only place the "commit it" note is
    # shown -- and op 8's clean-tree MARKER_NAME exemption would stop
    # the gate from ever revealing a marker adopted silently here.
    resolved = await _projects.resolve_project(project, adopt=False)
    if resolved.get("error"):
        # Fence-sanitised like every other echoed value: this string can
        # carry a path and marker fields out of a repo we do not control.
        return "", "", [], "⚠️ " + _safe(resolved["error"], 500)
    identity = resolved.get("content") or {}
    # Do NOT drop these. A repoint warning and a template-version note only
    # reach the tester if the write path echoes them (review MINOR: dropping
    # them was one of the concealment mechanisms R5 set out to remove).
    notes = ["- " + _safe(n, 300) for n in (identity.get("notes") or [])]
    return identity.get("path") or "", identity.get("package_root") or "", notes, ""


def _api_write_flag_on() -> bool:
    """A real write needs the write flag ON and dry-run OFF; otherwise apply is a no-op refusal."""
    from config.settings import settings as _s

    return bool(_s.qa_api_framework_write_enabled) and not bool(
        _s.qa_api_framework_write_dry_run
    )


async def handle_write_api_test(
    suite_id: str,
    apply: bool = False,
    project: str = "",
    *,
    progress: _common.ProgressCb = None,
) -> str:
    """WRITE half: render + (dry-run or) write the Java into the framework repo via
    that repo's ops pipeline. Never raises."""
    from config.settings import settings as _s

    if not _s.qa_api_test_enabled:
        return "⚠️ The API test agent is off (QA_API_TEST_ENABLED)."
    suite_id = (suite_id or "").strip()
    if not suite_id:
        return "⚠️ Pass the `suite_id` from `qa_submit_api_tests`."
    project = (project or "").strip()
    try:
        from agents import api_test_agent as _agent

        fw_path, package_root, project_notes, refusal = await _api_write_target(project)
        if refusal:
            return refusal

        await _common._emit(progress, "🧱 Rendering the Java…")
        r = await _agent.write_api_suite(
            suite_id,
            bool(apply),
            framework_path=fw_path,
            write_enabled=(_api_write_flag_on() and bool(apply)),
            package_root=package_root,
        )
        await _common._audit(
            "mcp_api_write",
            entity_id=suite_id,
            detail={
                "apply": bool(apply),
                "status": r.get("status"),
                "project": project,
            },
        )
        rendered = _render_api_write_result(r)
        if project_notes:
            rendered = "\n".join(project_notes) + "\n\n" + rendered
        return rendered
    except Exception as exc:
        logger.exception("handle_write_api_test failed")
        return f"⚠️ API test write failed ({type(exc).__name__}) — see the server log."
