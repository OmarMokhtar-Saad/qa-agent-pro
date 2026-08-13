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
Cursor / Claude ──(stdio, MCP)──> start.sh / start.cmd
                                    │ 1. check GitHub for a newer release → auto-update
                                    │ 2. verify MANIFEST.sha256 → self-heal edited files
                                    │ 3. chmod code files read-only
                                    ▼
                                mcp_server.py  →  qa_* tools
```

1. Your editor launches `start.sh` (`start.cmd` on Windows) and talks
   MCP over stdio.
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
| `qa_capture_screens` | Capture phone / emulator screens as image content + reusable `capture_ids` for grounded generation (needs `QA_MOBILE_CAPTURE`, shipped on in this edition) |
| `qa_search_corpus` | Search past generated suites (requires `QA_RAG_ENABLED`) |
| `qa-doctor` | Verify this machine: version, mobile tooling, devices, enabled features — no credentials to check |

## Quick start

> **On Windows? Use [the Windows quick start](#windows) instead.**
> The commands on this page are macOS / Linux / *inside* WSL: `sh`,
> `bash` and `curl ... | bash` are not Windows commands. Windows has
> its own one-liner -- PowerShell, no WSL, and **no administrator
> rights**.

Requires **Python 3.10+** and `curl` (the installer picks the newest
suitable `python3.x` on your PATH automatically). Check with
`python3 --version`. If that fails or shows an older version, install
Python first:

```bash
# Run these in a macOS/Linux terminal or the WSL shell -- NOT in CMD.

# macOS (installs Homebrew first if you don't have it: https://brew.sh)
brew install python@3.12

# Ubuntu / Debian (incl. WSL)
sudo apt-get update && sudo apt-get install -y python3.12

# No admin rights? Both commands above need them -- uv does not:
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs into ~/.local
uv python install 3.12
# ...then, ONLY if `python3.12 --version` still fails, add it to PATH:
export PATH="$HOME/.local/bin:$PATH"
```

**No admin rights?** The install itself never needs any: everything lands
under your home folder (`~/qa-agent-pro`, override with `QA_INSTALL_DIR`)
in a private virtualenv -- no `sudo`, no system Python, nothing written
outside `$HOME`, and no elevation prompt. Only the Python prerequisite
above can require admin, which is exactly what the `uv` route avoids.
On Windows, the per-user Python from python.org (leave *Install for all
users* unchecked) needs no admin either.

The same holds on Windows: the native installer writes only under your
user profile, and every Windows Python option is per-user (python.org
with *Install for all users* unchecked, the Microsoft Store build, or
`uv`). Nothing in the Windows path needs administrator rights -- see
[the Windows quick start](#windows). WSL is only an ALTERNATIVE there,
and `wsl --install` is the one thing that WOULD need admin.

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

> run qa-doctor

> generate test cases for our new login page

**There is no third step.** No API key, no `claude login`, nothing to
paste into `.env`. This server does the grounding, the quality checks
and the exports; the test cases themselves are written by the model in
your editor, on the plan and schema the server hands it — which is also
why nothing here is billed to you twice.

## Windows

**Native Windows. No WSL, no administrator rights, and no Python needed
up front.** Two commands in PowerShell -- this exact sequence is verified
end to end on Windows 11 (build 22631) with a non-admin account and no
Python installed:

**1. Install `uv`** -- per-user, and it is how the installer gets Python:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

uv finishes by warning that `C:\Users\YOU\.local\bin` is not on your PATH.
**Ignore that.** You do NOT need to run `uv python install`, and you do NOT
need to open a new window: the installer looks for uv there directly and
fetches Python 3.12 itself. Already have Python 3.10+? Skip this step.

**2. Install QA Agent Pro:**

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.ps1 | iex"
```

A successful run looks like this:

```text
No Python found, but uv is installed -- fetching Python 3.12 ...
Installed Python 3.12.13 in 17.03s
Using Python interpreter: C:\Users\YOU\AppData\Roaming\uv\python\...\python.exe
Fetching the latest release of OmarMokhtar-Saad/qa-agent-pro ...
Creating virtualenv + installing dependencies (a few minutes) ...

Installed QA Agent Pro vX.Y.Z to C:\Users\YOU\qa-agent-pro

Registering with your AI editors ...
  - Claude Code: 'claude' CLI not found - skipped
  + Cursor: added (C:\Users\YOU\.cursor\mcp.json)
  + Claude Desktop: added (C:\Users\YOU\AppData\Roaming\Claude\claude_desktop_config.json)
```

`Claude Code: ... skipped` is normal unless you use the Claude CLI.
Everything lands in `%USERPROFILE%\qa-agent-pro` (override with
`$env:QA_INSTALL_DIR`); nothing is written outside your own user profile, and
you are never prompted to elevate.

**3. Restart Cursor / Claude Desktop**, then ask it:

> run qa-doctor

> generate test cases for our new login page

### Optional: `adb`, for mobile testing only

Nothing above needs it. Test-case generation, Excel export and Jira
reading all work without it -- `adb` is only for listing Android devices
and capturing their screens. If you want that:

```powershell
winget install --id Google.PlatformTools -e
```

`run qa-doctor` lists the optional tools and prints the exact install
command for whatever is missing **on your OS** -- it no longer reports
macOS-only tooling as missing on Windows.

### Windows troubleshooting

Every row here is an error a real tester hit, in order:

| What you see | What it means |
|---|---|
| `'sh' is not recognized ...` | You ran a macOS/Linux command in CMD. Use the PowerShell one-liners above. |
| `The requested operation requires elevation` | That is `wsl --install`, and it is the ONE thing here needing admin. You do not need WSL at all -- use the native path above. |
| `Python was not found; run without arguments to install from the Microsoft Store` | That is the App Execution Alias, not a real Python. Do step 1. |
| `'uv' is not recognized ...` right after uv installed fine | uv's installer does not update the PATH of the window you are already in. You do not need uv on PATH -- just run step 2. |
| `... already exists` | An install is already there. Updates are automatic, so nothing to do; `$env:QA_FORCE=1` reinstalls from scratch. |

Windows entry points, if you ever need them by hand:

| File | Use |
|---|---|
| `start.cmd` | The MCP command your editor runs (the Windows `start.sh`) |
| `install.ps1` | The installer above |
| `connect.ps1` | Re-register your editors (`powershell -ExecutionPolicy Bypass -File %USERPROFILE%\qa-agent-pro\connect.ps1`) |

Other no-admin ways to get Python, if you would rather not use uv: the
python.org installer with *Install for all users* unchecked, or the
Microsoft Store build. Both are per-user and the installer finds either.

One Windows-only caveat: a launcher update applies on your next editor
start rather than live. Windows cannot re-exec a process in place, and
pretending otherwise would drop your session mid-run. Server updates
still apply live, exactly as on macOS.

### Alternative: WSL2

If you already run WSL2 -- or want the Linux tooling (`adb`, POSIX
paths) -- the bash installer works there unchanged. Note `wsl --install`
itself needs **administrator** rights, which the native path above does
not:

**1. Install WSL2** if you don't have it -- check with `wsl -l -v`.
This needs **administrator** rights (it enables a machine-wide Windows
feature): open PowerShell or Terminal via *Run as administrator*, run
the command below, reboot, then pick an Ubuntu username + password when
it first launches. No admin? Use the native path above instead.

```powershell
wsl --install
```

Nothing after this point needs admin.

**2. Run the Step 1 installer INSIDE the Ubuntu shell** -- not in CMD
or PowerShell. Recent Ubuntu images already ship Python 3.12, so
`python3 --version` is usually all the prerequisite check you need:

```bash
curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
```

**3. Register your Windows editor by hand.** This is the one step WSL
changes: `connect.sh` writes to the MCP configs it can see *inside* WSL,
which are not the ones a Windows-side Cursor / Claude reads. Point the
Windows editor at the WSL script through `wsl.exe` (replace `YOU` with
your WSL username):

```powershell
claude mcp add --scope user qa-agent-pro -- wsl.exe -e /home/YOU/qa-agent-pro/start.sh
```

Cursor (`%USERPROFILE%\.cursor\mcp.json`) and Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`) take the same target
as a command + args pair:

```json
{
  "mcpServers": {
    "qa-agent-pro": {
      "command": "wsl.exe",
      "args": ["-e", "/home/YOU/qa-agent-pro/start.sh"]
    }
  }
}
```

Then restart the editor and ask `run qa-doctor`.

Two things behave differently on a WSL install:

- Exported Excel/CSV files land in the WSL filesystem. Open them from
  Windows Explorer at
  `\\wsl$\Ubuntu\home\YOU\qa-agent-pro\data\exports`.
- Mobile testing (`qa_list_devices`) needs `adb` reachable from *inside*
  WSL; a Windows-side adb server is not visible there by default. Install
  it in WSL with `sudo apt install android-tools-adb` -- the Windows
  `winget` package does not help here.

If you run Cursor or Claude Code **inside** WSL (Remote-WSL / the Linux
build), none of this applies -- skip step 3, `connect.sh` registered them
already.

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
claude mcp add --scope user qa-agent-pro -- ~/qa-agent-pro/start.sh
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

On Windows the `command` is `%USERPROFILE%\qa-agent-pro\start.cmd`
(native) -- or the `wsl.exe` form if you installed inside WSL. See
[Windows](#windows).

Restart the editor afterwards. Ask `run qa-doctor` first to confirm
the machine is ready.

## Configure

Edit `~/qa-agent-pro/.env` (created from `.env.example`):

| Variable | Purpose |
|---|---|
| _(none)_ | No LLM credentials: generation runs in your own chat model, so there is no API key or backend to set |
| _(none)_ | Jira ticket URLs work via your own Atlassian MCP connection -- no `.env` entry needed |
| _(none)_ | Swagger/OpenAPI link → API test cases: paste a spec URL and it is ingested automatically |
| `QA_MOBILE_CAPTURE` | Mobile-screen capture (Android via adb). The screenshots are handed to your own chat model, so no API key is involved |
| `QA_RAG_ENABLED` | Learn from your past suites: grounding + duplicate flagging (on by default) |
| `QA_MCP_ELICIT_ENABLED` | Interactive pickers in Cursor / Claude Code (on by default; automatic text-menu fallback on clients without dialog support) |
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
- **Updates on demand**: running `qa-doctor` always checks for and
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
  notifications) — `qa-doctor` tells you explicitly when that is
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

**Opt out** at any time in `~/qa-agent-pro/.env`, with the cross-tool
standard variable -- since 2026-08-13 this is the only opt-out
(`QA_TELEMETRY_DISABLED` was removed):

```bash
DO_NOT_TRACK=1
```

With telemetry off nothing is sent (no opt-out ping).

## License

Copyright © 2026. All rights reserved. Source is visible for transparency;
redistribution or modification requires the author's permission.
