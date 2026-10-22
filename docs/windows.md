# Windows and WSL

The one-liner in [the README](../README.md#install) is all most people need: native Windows, no
WSL, no admin rights, and no Python required up front. (A WSL install is the
alternative path and does need admin once, to enable WSL itself.)

## Native Windows, step by step

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

Mobile testing on Windows (`adb`, emulator): see [Mobile testing](mobile.md).

## Windows entry points

If the installer says `... already exists`, an install is already there. Updates are automatic, so there is nothing to do; `$env:QA_FORCE=1` reinstalls from scratch.

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


## Alternative: installing inside WSL2

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

