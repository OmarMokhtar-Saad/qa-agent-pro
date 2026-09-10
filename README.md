<div align="center">

# QA Agent Pro

**An AI QA agent that lives in your editor.**

[![Release](https://img.shields.io/github/v/release/OmarMokhtar-Saad/qa-agent-pro?label=release)](https://github.com/OmarMokhtar-Saad/qa-agent-pro/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](#requirements)
[![MCP](https://img.shields.io/badge/protocol-MCP-6E56CF)](https://modelcontextprotocol.io)
[![Platforms](https://img.shields.io/badge/macOS%20%7C%20Linux%20%7C%20Windows%20%7C%20WSL-supported-informational)](#install)

</div>

QA Agent Pro is an [MCP](https://modelcontextprotocol.io) server for **Cursor**,
**Claude Code** and **Claude Desktop**. It turns a feature description, a Jira
ticket, a web page, a Swagger/OpenAPI link or live mobile screens into a
professional test-case suite — Excel / CSV / TestRail for manual teams, Gherkin
/ Playwright skeletons for automation.

There is no web UI: your AI editor is the interface. There is **no API key to
buy and nothing to log in to** — the test cases are written by the model you are
already chatting with, on the plan and schema this server hands it.

---

## Install

Pick your platform. Run **one** command — it installs the server, its private
virtualenv and Python itself if needed, then registers the server with every
editor it finds. Nothing is written outside your home folder and **no step needs
administrator rights**.

**macOS · Linux · WSL** — in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
```

**Windows** — in PowerShell (native, no WSL):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.ps1 | iex"
```

Then **restart your editor** and ask it:

> run qa-doctor

> generate test cases for our new login page

**There is no third step.** No API key, no `claude login`, nothing to paste into
`.env`.

> [!TIP]
> Re-run the installer any time. It updates an existing install in place,
> repairs an incomplete one, and never touches your `.env`, generated suites or
> corpus.

### Requirements

Python **3.10 or newer**, and `curl`. You do not have to install it yourself:
if the installer finds no suitable Python it provisions a private one with
[uv](https://docs.astral.sh/uv/) under your home folder — the same on macOS,
Linux and WSL, with no package manager and no `sudo`.

<details>
<summary>Installing Python yourself (optional)</summary>

Only if you would rather manage the interpreter. Run the line for **your** OS —
`apt-get` does not exist on macOS, and `brew` does not exist on stock Ubuntu.

macOS ([Homebrew](https://brew.sh)):

```bash
brew install python@3.12
```

Ubuntu / Debian, including inside WSL:

```bash
sudo apt-get update && sudo apt-get install -y python3.12
```

Any of the three, with no admin rights — this is what the installer does for
you automatically:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

Interactive `zsh` (the macOS default) does not treat `#` as a comment, so never
paste a trailing `# ...` note onto any of these lines — it is passed on as an
argument and the command fails.

</details>

<details>
<summary>Where things are installed</summary>

Everything lands in `~/qa-agent-pro` (`%USERPROFILE%\qa-agent-pro` on Windows).
Override with `QA_INSTALL_DIR` (`$env:QA_INSTALL_DIR`). Dependencies go into a
private virtualenv inside that folder; no system Python is modified and nothing
is written outside `$HOME`. `QA_FORCE=1` replaces the code from scratch while
still keeping `.env`, `data/` and `corpus/`.

</details>

## Troubleshooting

| What you see | What it means | Fix |
|---|---|---|
| `sudo: apt-get: command not found` | `apt-get` is Debian/Ubuntu; you are on macOS. | Use `brew`, or just let the installer provision Python. |
| `sh: #: No such file or directory` then `curl: (56)` | Interactive `zsh` does not enable comments, so a trailing `# ...` became arguments to `sh`. | Re-run the command with the comment removed. |
| `<...>: no such file or directory` from `git clone` | You pasted a placeholder such as `<REPO_URL>` literally; `zsh` read `<` as a redirect. | Use the real URL — or the one-liner above, which needs no clone. |
| `ENOENT ... /qa-agent-pro/start.sh` in your editor's MCP log | The server is registered but the install is missing or incomplete. | Re-run the installer; it repairs the folder in place. |
| `... already exists` | An older installer refused to touch an existing folder. | Fixed — the current installer updates instead. Re-run it. |
| `'sh' is not recognized ...` | You ran a macOS/Linux command in CMD or PowerShell. | Use the Windows one-liner above. |
| `Python was not found; run without arguments to install from the Microsoft Store` | Windows' App Execution Alias, not a real Python. | Nothing to do — the installer fetches Python via `uv`. |
| `'uv' is not recognized ...` right after `uv` installed fine | `uv`'s installer does not update the PATH of an open window. | Ignore it; the installer looks for `uv` directly. |
| `The requested operation requires elevation` | That is `wsl --install`, the one command here that needs admin. | You do not need WSL — use the native Windows path. |

Still stuck? `run qa-doctor` in your editor reports the version, the mobile
tooling, connected devices and the exact install command for anything missing
**on your OS**.

## Tools

| Tool | What it does |
|---|---|
| `qa_generate_test_cases` | Feature text, Jira/issue URL, web page URL, or Swagger/OpenAPI link → structured test suite (steps, expected results, priority, risk) with a persisted `suite_id` |
| `qa_export_suite` | Export a suite by `suite_id`: `csv`, `xlsx`, `testrail`, `gherkin`, or `playwright` |
| `qa_list_devices` | List connected Android/iOS devices, emulators and simulators |
| `qa_capture_screens` | Capture phone / emulator screens as image content + reusable `capture_ids` for grounded generation |
| `qa_search_corpus` | Search past generated suites |
| `qa-doctor` | Verify this machine: version, mobile tooling, devices, enabled features |

### Example prompts

> Generate test cases for our new login page with email + password fields

> Generate test cases from https://yourcompany.atlassian.net/browse/SHOP-123

> Generate API test cases from https://api.example.com/v3/api-docs

> Generate test cases from the screens on my connected Android device, then
> export the suite to xlsx

## How it works

```
Cursor / Claude ──(stdio, MCP)──> start.sh / start.cmd
                                    │ 1. check GitHub for a newer release → auto-update
                                    │ 2. verify MANIFEST.sha256 → self-heal edited files
                                    │ 3. chmod code files read-only
                                    ▼
                                mcp_server.py  →  qa_* tools
```

1. Your editor launches `start.sh` (`start.cmd` on Windows) and talks MCP over
   stdio.
2. Before serving, the launcher **updates itself** from the latest GitHub
   release, **verifies every code file** against the release manifest
   (locally-edited files are restored automatically), and **locks the code
   read-only**. A network failure never blocks startup.
3. The server exposes the `qa_*` tools above; the AI in your editor calls them
   for you when you ask for test cases.

Your data is never touched by updates: `.env`, generated suites (`data/`) and
the RAG corpus (`corpus/`) are protected paths.

## Connect your editor

**The installer already did this.** To re-register — after moving the install,
or when you add a new editor:

```bash
~/qa-agent-pro/connect.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File %USERPROFILE%\qa-agent-pro\connect.ps1
```

Editors that are not installed are skipped, existing MCP servers in your configs
are preserved, and a `.bak` backup is written next to any file it touches.

<details>
<summary>Registering by hand</summary>

**Claude Code**

```bash
claude mcp add --scope user qa-agent-pro -- ~/qa-agent-pro/start.sh
```

**Cursor** — `~/.cursor/mcp.json` (Settings → MCP):

```json
{
  "mcpServers": {
    "qa-agent-pro": { "command": "/Users/YOU/qa-agent-pro/start.sh" }
  }
}
```

**Claude Desktop** — the same block in
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows).

On Windows the `command` is `%USERPROFILE%\qa-agent-pro\start.cmd`, or the
`wsl.exe` form if you installed inside WSL. Restart the editor afterwards, then
ask `run qa-doctor`.

</details>

## Connect Jira

No API token and no `.env` entry — Jira Cloud is read through your own Atlassian
MCP connection (OAuth), the same way this server is connected to your editor.

1. Add the Atlassian MCP server in your client's MCP settings
   (`https://mcp.atlassian.com/v1/mcp/authv2`). Claude Code / Desktop: `.mcp.json`
   or Settings → Connectors. Cursor: Settings → Features → MCP. Gemini CLI:
   `gemini mcp add --transport http`.
2. Approve the one-time OAuth consent in the browser tab it opens.
3. Paste a ticket URL. The agent fetches it through that connection and nothing
   is stored on this machine.

If your client has no Atlassian connection yet, the agent replies with these
exact steps rather than failing silently or inventing ticket content.

## Configure

Optional. Edit `~/qa-agent-pro/.env` (created from `.env.example`) and restart
your editor.

| Variable | Purpose |
|---|---|
| `QA_EXPORT_DIR` | Folder auto-exported Excel files are saved to (`data/exports`; persists across sessions and updates) |
| `QA_INSTALL_DIR` | Install location, read by the installer (default `~/qa-agent-pro`) |
| `QA_UPDATE_INTERVAL_MINUTES` | How often a running server checks for a new release (default 15) |
| `QA_MOBILE_RUN_ENABLED` | Opt in to the Android emulator run lane — off by default |
| `QA_UPDATE_REQUIRE_SIGNATURE` | Refuse unsigned releases instead of warning |
| `DO_NOT_TRACK` | Set to `1` to disable telemetry |

There is nothing to configure for generation itself: no LLM credential, no
backend, no Jira token, no switch for Swagger ingestion, mobile screen capture,
corpus learning or the interactive pickers. Those are always on, and the model
that writes your cases is the one in your editor.

## Windows

The one-liner in [Install](#install) is all most people need: native Windows, no
WSL, no admin rights, and no Python required up front.

<details>
<summary>Step by step, with the output you should see</summary>

**1. Install `uv`** — per-user, and it is how the installer gets Python. Skip
this if you already have Python 3.10+.

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

uv finishes by warning that `C:\Users\YOU\.local\bin` is not on your PATH.
**Ignore that.** You do not need to run `uv python install`, and you do not need
a new window: the installer looks there directly.

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

**3. Restart Cursor / Claude Desktop**, then ask it:

> run qa-doctor

> generate test cases for our new login page

### Optional: `adb`, for mobile testing only

Nothing above needs it. Test-case generation, Excel export and Jira
reading all work without it -- `adb` is only for listing Android devices
and capturing their screens. If you want that:

```powershell
winget install --id Google.PlatformTools -e --scope user
```

`--scope user` installs it for you alone and needs no Administrator
rights. Drop that flag only if you want a machine-wide install AND you
have admin on this machine.

`run qa-doctor` lists the optional tools and prints the exact install
command for whatever is missing **on your OS** -- it no longer reports
macOS-only tooling as missing on Windows.

### Optional: running test cases on an Android emulator

**Off by default, and it stays off until you turn it on.** The emulator
lane runs your suite on a real Android app: it plans each case from the
screen, replays it, asks you when a screen needs a credential, and ends
with a self-contained report folder that opens in your browser.

To turn it on, add this to `.env` and restart your editor:

```
QA_MOBILE_RUN_ENABLED=true
```

Then say `run mobile test`. Three tools appear -- `qa_mobile_test`,
`qa_submit_mobile_step` and `qa_mobile_status`; with the flag off they
are not registered at all, so nothing changes for anyone who ignores
this section.

What to expect the FIRST time:

- If Android Studio is already installed, it uses that SDK and downloads
  nothing but a small (~300KB) QA keyboard.
- If it is not, it downloads a JRE, the command-line tools and one system
  image -- up to ~2.2GB, several minutes -- into `~/.qa-agents/mobile/`.
  It checks free disk first and refuses rather than filling the drive.
- Every install, download and launch also needs `apply=true` on the call,
  so nothing large happens because you asked a question.

Two things worth knowing:

- **Credentials are never stored.** When a screen needs one you are asked
  in chat, the value is typed straight onto the device, and it is masked
  in the report, the run files and the audit log.
- **A run resumes in any chat.** Keep the run id; a second chat that
  resumes takes over and the first is told to stop.

**Windows is untested.** The code has Windows paths and flags, but no
Windows machine has run it end to end. On macOS it is tested against a
real emulator.

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
| `connect.ps1` | Re-register your editors |

One Windows-only caveat: a launcher update applies on your next editor start
rather than live. Windows cannot re-exec a process in place, and pretending
otherwise would drop your session mid-run. Server updates still apply live,
exactly as on macOS.

</details>

<details>
<summary>Alternative: installing inside WSL2</summary>

If you already run WSL2 — or want the Linux tooling (`adb`, POSIX paths) — the
bash installer works there unchanged. Note that `wsl --install` itself needs
**administrator** rights, which the native path above does not.

**1.** Install WSL2 if you don't have it (check with `wsl -l -v`). Open
PowerShell via *Run as administrator*, run `wsl --install`, reboot, then pick an
Ubuntu username and password. Nothing after this needs admin.

**2.** Run the bash one-liner **inside the Ubuntu shell**, not in CMD or
PowerShell.

**3. Register your Windows editor by hand.** This is the one step WSL changes:
`connect.sh` writes to the MCP configs it can see *inside* WSL, which are not
the ones a Windows-side Cursor / Claude reads. Point the Windows editor at the
WSL script through `wsl.exe` (replace `YOU` with your WSL username):

```powershell
claude mcp add --scope user qa-agent-pro -- wsl.exe -e /home/YOU/qa-agent-pro/start.sh
```

Cursor (`%USERPROFILE%\.cursor\mcp.json`) and Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`) take the same target as a
command + args pair:

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

Two things behave differently on a WSL install:

- Exported Excel/CSV files land in the WSL filesystem. Open them from Windows
  Explorer at `\\wsl$\Ubuntu\home\YOU\qa-agent-pro\data\exports`.
- Mobile testing (`qa_list_devices`) needs `adb` reachable from *inside* WSL; a
  Windows-side adb server is not visible there by default. Install it with
  `sudo apt install android-tools-adb` — the Windows `winget` package does not
  help here.

If you run Cursor or Claude Code **inside** WSL (Remote-WSL / the Linux build),
none of this applies — skip step 3, `connect.sh` registered them already.

</details>

## Mobile testing

Optional, and nothing above needs it. `adb` is only for listing Android devices
and capturing their screens; test-case generation, Excel export and Jira reading
all work without it.

<details>
<summary>Installing adb</summary>

macOS:

```bash
brew install --cask android-platform-tools
```

Ubuntu / Debian / WSL:

```bash
sudo apt-get install -y android-tools-adb
```

Windows — `--scope user` installs it for you alone and needs no admin:

```powershell
winget install --id Google.PlatformTools -e --scope user
```

</details>

<details>
<summary>Running test cases on an Android emulator (opt-in)</summary>

**Off by default, and it stays off until you turn it on.** The emulator lane
runs your suite against a real Android app: it plans each case from the screen,
replays it, asks you when a screen needs a credential, and ends with a
self-contained HTML report that opens in your browser.

To turn it on, add this to `.env` and restart your editor:

```
QA_MOBILE_RUN_ENABLED=true
```

Then say `run mobile test`. Three tools appear — `qa_mobile_test`,
`qa_submit_mobile_step` and `qa_mobile_status`. With the flag off they are not
registered at all, so nothing changes for anyone who ignores this section.

What to expect the FIRST time:

- If Android Studio is already installed, it uses that SDK and downloads nothing
  but a small (~300KB) QA keyboard.
- If not, it downloads a JRE, the command-line tools and one system image — up
  to ~2.2GB, several minutes — into `~/.qa-agents/mobile/`. It checks free disk
  first and refuses rather than filling the drive.
- Every install, download and launch also needs `apply=true` on the call, so
  nothing large happens because you asked a question.

Two things worth knowing:

- **Credentials are never stored.** When a screen needs one you are asked in
  chat, the value is typed straight onto the device, and it is masked in the
  report, the run files and the audit log.
- **A run resumes in any chat.** Keep the run id; a second chat that resumes
  takes over and the first is told to stop.

> [!WARNING]
> **Windows is untested for this lane.** The code has Windows paths and flags,
> but no Windows machine has run it end to end. On macOS it is tested against a
> real emulator.

</details>

## Updates, versioning & integrity

- **Versioning** — [Semantic Versioning](https://semver.org/); every release is
  a git tag (`vX.Y.Z`) with notes in [CHANGELOG.md](CHANGELOG.md).
- **Automatic and live** — releases are checked at startup and every 15 minutes
  while the server runs (tune with `QA_UPDATE_INTERVAL_MINUTES`). A new release
  installs in the background and takes effect once no tool is running: the
  server restarts itself and transparently replays the MCP handshake, so you
  never restart your editor. This works even when a *different* editor sharing
  the same install applied the update.
- **On demand** — running `qa-doctor` always checks for and installs the newest
  release immediately, then reloads seamlessly. Re-running the installer does
  the same from a terminal.
- **Crash resilient** — if the server process dies, the launcher respawns it and
  your editor session continues.
- **Rare exception** — releases that change tool *definitions* need one editor
  restart (editors cache definitions and ignore refresh notifications).
  `qa-doctor` tells you explicitly when that is the case; otherwise never
  restart.
- **Read-only by design** — code files are hash-verified against
  `MANIFEST.sha256`, Ed25519-signed via `MANIFEST.sig`, and chmod'ed read-only
  on every start. Manual or AI-editor edits fail to save, and anything
  force-edited is restored on the next start. This repo is a build artifact:
  changes land here only through releases.

## Telemetry & privacy

QA Agent Pro sends anonymous usage analytics so we can see which features are
used and on which platforms, and fix crashes faster. It is **on by default** —
the industry standard for developer CLIs (Next.js, Astro, GitHub CLI).

**Collected:** the tool name invoked (e.g. `qa_generate_test_cases`), the app
version, your OS and CPU architecture, an anonymous hashed machine id, call
duration, and success/failure (on failure, only the Python exception class
name). Also content-free tool properties (test-case count, export format, and
source type: feature text / Jira / Swagger / mobile), crash stack traces for
issue grouping (function names, project-relative file names and line numbers —
exception messages and absolute paths are scrubbed before sending), and per-call
AI-generation metrics (model, backend, token counts, latency).

**Never collected:** feature descriptions, Jira/URL/page content, generated test
cases, LLM prompts or completions, exception messages, absolute file paths, or
secrets. The only personal field is an email, and only if you set
`QA_USER_EMAIL` yourself.

**Opt out** at any time in `~/qa-agent-pro/.env`, with the cross-tool standard
variable — since 2026-08-13 it is the only opt-out
(`QA_TELEMETRY_DISABLED` was removed):

```
DO_NOT_TRACK=1
```

With telemetry off nothing is sent, not even an opt-out ping.

## License

Copyright © 2026. All rights reserved. Source is visible for transparency;
redistribution or modification requires the author's permission.
