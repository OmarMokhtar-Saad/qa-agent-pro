# QA Agent Pro

**An AI QA agent for your editor.** QA Agent Pro is an
[MCP](https://modelcontextprotocol.io) server that plugs into **Cursor**,
**Claude Code**, and **Claude Desktop**, and turns a feature description, a
Jira ticket, a web page, a Swagger/OpenAPI link, or live mobile screens
into professional test-case suites — for both manual and automated
testing (Excel / CSV / TestRail for manual teams, Gherkin / Playwright
skeletons for automation).

There is no web UI to run — your AI editor is the interface. There is
also **no API key to buy and nothing to log in to**: the test cases are
written by the model you are already chatting with.

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
| `qa_list_devices` | List connected Android/iOS devices, emulators and simulators |
| `qa_search_corpus` | Search past generated suites (requires `QA_RAG_ENABLED`) |
| `qa_setup_check` | Verify this machine: version, mobile tooling, devices, enabled features — no credentials to check |

## Quick start

Requires **Python 3.10+** and `curl` (the installer picks the newest
suitable `python3.x` on your PATH automatically). Check with
`python3 --version`. If that fails or shows an older version, install
Python first:

```bash
# macOS (installs Homebrew first if you don't have it: https://brew.sh)
brew install python@3.12

# Ubuntu / Debian (incl. WSL)
sudo apt-get update && sudo apt-get install -y python3.12
```

**Step 1 — Install.** Run this one command in your terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
```

It downloads the latest release into `~/qa-agent-pro` (override with
`QA_INSTALL_DIR`), installs dependencies into a private virtualenv,
locks the code files read-only, **and registers the server with Claude
Code, Cursor, and Claude Desktop automatically** — there is no JSON
config to copy-paste.

**Step 2 — Restart your editor** (Cursor / Claude Code / Claude
Desktop) and try:

> run qa_setup_check

> generate test cases for our new login page

**There is no third step.** No API key, no `claude login`, nothing to
paste into `.env`. This server does the grounding, the quality checks
and the exports; the test cases themselves are written by the model in
your editor, on the plan and schema the server hands it — which is also
why nothing here is billed to you twice.

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
| _(none)_ | No LLM credentials: generation runs in your own chat model, so there is no API key or backend to set |
| _(none)_ | Jira ticket URLs work via your own Atlassian MCP connection -- no `.env` entry needed |
| `QA_SWAGGER_ENABLED` | Swagger/OpenAPI link → API test cases |
| `QA_MOBILE_CAPTURE` | Mobile-screen capture (Android via adb). The screenshots are handed to your own chat model, so no API key is involved |
| `QA_RAG_ENABLED` | Learn from your past suites: grounding + duplicate flagging (on by default) |
| `QA_MCP_ELICIT_ENABLED` | Interactive pickers in Cursor / Claude Code (on by default; automatic text-menu fallback on clients without dialog support) |
| `QA_AUTO_EXPORT_XLSX` | Auto-build the Excel file the instant generation finishes; the reply tells you exactly where the `.xlsx` is (on by default) |
| `QA_EXPORT_DIR` | Folder the auto-exported Excel files are saved to -- `data/exports` in the dist, so your files persist there across sessions and updates |

## Connect Jira (to paste ticket URLs)

No API token, no `.env` entry -- Jira is read through your own
Atlassian MCP connection (OAuth, Jira Cloud only), the same way this
tool itself is connected to your editor.

1. Add the Atlassian MCP server in your client's MCP settings
   (`https://mcp.atlassian.com/v1/mcp/authv2`) -- Claude Code / Desktop:
   `.mcp.json` or Settings > Connectors; Cursor: Settings > Features >
   MCP; Gemini CLI: `gemini mcp add --transport http`.
2. Approve the one-time OAuth consent in the browser tab it opens.
3. Paste a ticket URL -- the agent fetches it through that connection
   and nothing is stored on this machine.

If your client has no Atlassian MCP connection yet, the agent replies
with these exact steps instead of failing silently or inventing
ticket content.

## Example prompts

> Generate test cases for our new login page with email + password fields

> Generate test cases from https://yourcompany.atlassian.net/browse/SHOP-123

> Generate API test cases from https://api.example.com/v3/api-docs

> Generate test cases from the screens on my connected Android device,
> then export the suite to xlsx

## Updates, versioning & integrity

- **Versioning**: [Semantic Versioning](https://semver.org/); every release
  is a git tag (`vX.Y.Z`) with notes in [CHANGELOG.md](CHANGELOG.md).
- **Updates on demand**: running `qa_setup_check` always checks for and
  installs the newest release immediately, then reloads seamlessly.
- **Updates are automatic and live**: releases are checked at startup
  AND every 15 minutes while the server runs (tune with
  `QA_UPDATE_INTERVAL_MINUTES`). A new release installs in the
  background and takes effect once no tool is running — the server
  restarts itself and transparently replays the MCP handshake, so you
  never restart your editor. This works even when a DIFFERENT editor
  sharing the same install applied the update.
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

## Telemetry & privacy

QA Agent Pro sends anonymous usage analytics so we can see which
features are used and on which platforms, and fix crashes faster. It
is ON by default - the industry standard for developer CLIs (Next.js,
Astro, GitHub CLI).

**Collected:** the tool name invoked (e.g. `qa_generate_test_cases`),
the app version, your OS + CPU architecture, an anonymous hashed
machine id, call duration, and success/failure (on failure ONLY the
Python exception class name). We also send content-free tool properties
(test-case count, export format, and the source type: feature text /
Jira / Swagger / mobile), crash stack traces for issue grouping (function
names, project-relative file names and line numbers — exception
messages and absolute paths are scrubbed before sending), and per-call
AI-generation metrics (model, backend, input/output token counts, and
latency).

**Never collected:** feature descriptions, Jira/URL/page content,
generated test cases, LLM prompts or completions, exception messages,
absolute file paths, or secrets. The only personal field is an email, and ONLY if
you set `QA_USER_EMAIL` yourself.

**Opt out** at any time (either works) in `~/qa-agent-pro/.env`:

```bash
QA_TELEMETRY_DISABLED=1
# or the cross-tool standard:
DO_NOT_TRACK=1
```

With telemetry off nothing is sent (no opt-out ping).

## License

Copyright © 2026. All rights reserved. Source is visible for transparency;
redistribution or modification requires the author's permission.
