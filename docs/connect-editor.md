# Connect your editor

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


## Registering by hand

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

