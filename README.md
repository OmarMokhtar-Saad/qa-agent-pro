# QA Agent Pro

**An AI QA agent for your editor.** QA Agent Pro is an
[MCP](https://modelcontextprotocol.io) server that plugs into **Cursor**,
**Claude Code**, and **Claude Desktop**, and turns a feature description, a
Jira ticket, a web page, a Swagger/OpenAPI link, or live mobile screens
into professional test-case suites — for both manual and automated
testing (Excel / CSV / TestRail for manual teams, Gherkin / Playwright
skeletons for automation).

There is no web UI to run — your AI editor is the interface.

## How it works

```
Cursor / Claude ──(stdio, MCP)──> start.sh
                                    │ 1. check GitHub for a newer release → auto-update
                                    │ 2. verify MANIFEST.sha256 → self-heal edited files
                                    │ 3. chmod code files read-only
                                    ▼
                                mcp_server.py  →  qa_* tools
```

1. Your editor launches `start.sh` and talks MCP over stdio.
2. Before serving, the launcher **updates itself** from the latest GitHub
   release, **verifies every code file** against the release manifest
   (locally-edited files are restored automatically), and **locks the
   code read-only**. A network failure never blocks startup.
3. The server exposes the `qa_*` tools below; the AI in your editor calls
   them for you when you ask for test cases.

Your data is never touched by updates: `.env`, generated suites (`data/`),
and the RAG corpus (`corpus/`) are protected paths.

## Tools

| Tool | What it does |
|---|---|
| `qa_generate_test_cases` | Feature text, Jira/issue URL, web page URL, or Swagger/OpenAPI link → structured test suite (steps, expected results, priority, risk) with a persisted `suite_id` |
| `qa_export_suite` | Export a suite by `suite_id`: `csv`, `xlsx`, `testrail`, `gherkin`, or `playwright` |
| `qa_feature_analysis` | Enterprise Feature Analysis report from a Jira ticket, captured mobile screens, or both merged |
| `qa_list_devices` | List connected Android/iOS devices, emulators and simulators |
| `qa_search_corpus` | Search past generated suites (requires `QA_RAG_ENABLED`) |
| `qa_setup_check` | Verify this machine: LLM auth, mobile tooling, devices, enabled features |

## Quick start

Requires **Python 3.10+** and `curl` (the installer picks the newest
suitable `python3.x` on your PATH automatically).

**Step 1 — Install.** Run this one command in your terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
```

It downloads the latest release into `~/qa-agent-pro` (override with
`QA_INSTALL_DIR`), installs dependencies into a private virtualenv,
locks the code files read-only, **and registers the server with Claude
Code, Cursor, and Claude Desktop automatically** — there is no JSON
config to copy-paste.

**Step 2 — Add your LLM credentials.** `.env` is a settings file — open
it in a text editor (don't type its path as a command):

```bash
nano ~/qa-agent-pro/.env        # or on macOS: open -e ~/qa-agent-pro/.env
```

Then pick a backend:

- `QA_LLM_BACKEND=cli` (default) — uses your `claude` CLI login; run
  `claude login` once if you haven't.
- `QA_LLM_BACKEND=api` — set `ANTHROPIC_API_KEY=sk-ant-...`.
- `QA_LLM_BACKEND=cursor` — set `CURSOR_API_KEY=...`.

**Step 3 — Restart your editor** (Cursor / Claude Code / Claude
Desktop) and try:

> run qa_setup_check

> generate test cases for our new login page

## Connect your editor

**The installer already did this for you in Step 1.** `connect.sh` lives
*inside* the install folder, so it exists only after Step 1 — if
`~/qa-agent-pro/connect.sh` says `no such file or directory`, run the
Step 1 installer first. Re-run it any time (after moving the install,
or when you add a new editor):

```bash
~/qa-agent-pro/connect.sh
```

Editors that are not installed are skipped; existing MCP servers in
your configs are preserved, and a `.bak` backup is written next to any
file it touches.

**Manual** — if you prefer to edit configs yourself:

**Claude Code**

```bash
claude mcp add qa-agent-pro -- ~/qa-agent-pro/start.sh
```

**Cursor** — add to `~/.cursor/mcp.json` (Settings → MCP):

```json
{
  "mcpServers": {
    "qa-agent-pro": { "command": "/Users/YOU/qa-agent-pro/start.sh" }
  }
}
```

**Claude Desktop** — add the same block to
`~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).

Restart the editor afterwards. Ask `run qa_setup_check` first to confirm
the machine is ready.

## Configure

Edit `~/qa-agent-pro/.env` (created from `.env.example`):

| Variable | Purpose |
|---|---|
| `QA_LLM_BACKEND` | `cli` (claude CLI login), `api` (`ANTHROPIC_API_KEY`), or `cursor` |
| `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` | Lets you paste Jira ticket URLs |
| `QA_SWAGGER_ENABLED` | Swagger/OpenAPI link → API test cases |
| `QA_MOBILE_CAPTURE` | Mobile-screen capture (Android via adb; vision needs `ANTHROPIC_API_KEY`) |
| `QA_RAG_ENABLED` | Learn from your past suites: grounding + duplicate flagging (on by default) |
| `QA_MCP_ELICIT_ENABLED` | Interactive pickers in Cursor / Claude Code (on by default; automatic text-menu fallback on clients without dialog support) |

## Connect Jira (to paste ticket URLs)

Each user connects with their own Atlassian account once.

**Easiest — in chat:** create an API token at
<https://id.atlassian.com/manage-profile/security/api-tokens>, then
tell the agent: *"configure Jira"* and give it your Jira URL, email
and the token — it saves them into the local `.env` for you and
reloads (the token never leaves your machine).

**Or manually:**

1. Create an API token: <https://id.atlassian.com/manage-profile/security/api-tokens>
2. Add to `~/qa-agent-pro/.env`:

```
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=<your token>
```

3. Run `qa_setup_check` — it reloads and shows Jira as configured.

If a pasted ticket needs credentials that are missing, the agent
replies with these exact steps (including your Jira host) instead of
failing silently.

## Example prompts

> Generate test cases for our new login page with email + password fields

> Generate test cases from https://yourcompany.atlassian.net/browse/SHOP-123

> Generate API test cases from https://api.example.com/v3/api-docs

> Run a feature analysis on the connected Android device together with
> ticket SHOP-123, then export the suite to xlsx

## Updates, versioning & integrity

- **Versioning**: [Semantic Versioning](https://semver.org/); every release
  is a git tag (`vX.Y.Z`) with notes in [CHANGELOG.md](CHANGELOG.md).
- **Updates on demand**: running `qa_setup_check` always checks for and
  installs the newest release immediately, then reloads seamlessly.
- **Updates are automatic and live**: releases are checked at startup
  AND every 15 minutes while the server runs (tune with
  `QA_UPDATE_INTERVAL_MINUTES`). A new release installs in the
  background and takes effect at the next idle minute — the launcher
  restarts the inner server and transparently replays the MCP
  handshake, so you never restart your editor.
- **Crash resilient**: if the server process ever dies, the launcher
  respawns it and your editor session continues.
- **Rare exception**: releases that change tool *definitions* need one
  editor restart (editors cache definitions and ignore refresh
  notifications) — `qa_setup_check` tells you explicitly when that is
  the case; otherwise never restart.
- **The install is read-only by design**: code files are hash-verified
  against `MANIFEST.sha256` and chmod'ed read-only on every start. Manual
  or AI-editor edits fail to save — and anything force-edited is restored
  on the next start. This repo is a build artifact: changes land here
  only through releases.

## License

Copyright © 2026. All rights reserved. Source is visible for transparency;
redistribution or modification requires the author's permission.
