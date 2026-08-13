# Register QA Agent Pro with Claude Code, Cursor, and Claude Desktop on
# native Windows. Finds the install path automatically; idempotent -- re-run
# any time:  powershell -ExecutionPolicy Bypass -File connect.ps1
#
# The JSON half deliberately delegates to tools/client_registry so Windows,
# connect.sh and the launcher's startup pass all share ONE implementation.
$ErrorActionPreference = "Continue"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Start = Join-Path $InstallDir "start.cmd"
$Py = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host "QA Agent Pro - registering MCP server at: $Start"

# Claude Code (CLI) -- user scope so it is available in every project.
if (Get-Command claude -ErrorAction SilentlyContinue) {
  # 2>&1 not 2>$null: a native exe writing to stderr becomes a
  # NativeCommandError, and merging is what actually defuses it.
  & claude mcp remove --scope user qa-agent-pro 2>&1 | Out-Null
  & claude mcp add --scope user qa-agent-pro -- "$Start" 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "  + Claude Code: registered (user scope)"
  } else {
    Write-Host "  ! Claude Code: could not register automatically - run manually:"
    Write-Host "      claude mcp add --scope user qa-agent-pro -- \"$Start\""
  }
} else {
  Write-Host "  - Claude Code: 'claude' CLI not found - skipped"
}

# Cursor + Claude Desktop -- merge JSON configs, preserving other servers.
# REPAIR mode (insert_only=False), same as connect.sh: re-running after the
# install moved must fix a stale command, while the startup pass must never
# rewrite an entry the tester edited by hand.
Set-Location $InstallDir
$PyBody = @'
import sys

from tools.client_registry import register_all

for label, status, detail in register_all(sys.argv[1], insert_only=False):
    if status == "skipped":
        print(f"  - {label}: not detected -- skipped")
    elif status == "error":
        print(f"  ! {label}: could not update ({detail}) -- add manually:")
        print(
            '      {"mcpServers": {"qa-agent-pro": {"command": "%s"}}}'
            % sys.argv[1]
        )
    else:
        print(f"  + {label}: {status} ({detail})")
'@
$PyFile = Join-Path $env:TEMP ("qa-connect-" + [guid]::NewGuid().ToString("N") + ".py")
# ASCII on purpose: Set-Content -Encoding UTF8 writes a BOM on PowerShell 5.1.
Set-Content -Path $PyFile -Value $PyBody -Encoding ASCII
try {
  & $Py $PyFile "$Start" 2>&1
} finally {
  Remove-Item $PyFile -Force -ErrorAction SilentlyContinue
}

# ---- Atlassian (Jira Cloud) ---------------------------------------------
# Same intent as connect.sh: write the entry so a tester never hand-edits
# mcpServers JSON. Writing it is NOT authorizing it -- OAuth is one click in
# the editor. QA_REGISTER_ATLASSIAN_MCP=false skips the whole block.
#
# Flag-off is exit 3, not 1: an uncaught ImportError exits 1, so `else 1`
# would report a broken venv as 'you turned this off'. Same reasoning as
# connect.sh -- keep the two in step.
#
# Every native call is preceded by `$LASTEXITCODE = 99`. That variable is
# only updated when a process actually RUNS, so a broken `claude` shim that
# PowerShell cannot launch would otherwise leave the PREVIOUS statement's 0
# in place and we would print 'already exists - left alone' having checked
# nothing. The sentinel makes 'did not run' land in the manual-instructions
# branch instead. ASCII only in here, like the rest of this file.
$AtlasUrl = "https://mcp.atlassian.com/v1/mcp/authv2"
$LASTEXITCODE = 99
& $Py -c "import config.settings; raise SystemExit(0)" 2>&1 | Out-Null
$AtlasRc = $LASTEXITCODE
if ($AtlasRc -eq 0) {
  Write-Host ""
  Write-Host "Connecting Atlassian (Jira Cloud) - one OAuth click is still yours:"
  if (Get-Command claude -ErrorAction SilentlyContinue) {
    # --scope user, exactly like the qa-agent-pro registration above: this
    # script runs with cwd=$InstallDir, so a default (local) scope would bind
    # the entry to the INSTALL directory and it would be invisible in the
    # tester's real project -- after we printed 'added'.
    $LASTEXITCODE = 99
    & claude mcp get atlassian 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
      Write-Host "  - Claude Code: an atlassian entry already exists (user"
      Write-Host "    or project scope) - left alone"
    } else {
      $LASTEXITCODE = 99
      & claude mcp add --scope user --transport http atlassian "$AtlasUrl" 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) {
        Write-Host "  + Claude Code: added - run /mcp and authenticate atlassian"
      } else {
        Write-Host "  ! Claude Code: could not add it - run manually:"
        Write-Host "      claude mcp add --scope user --transport http atlassian $AtlasUrl"
      }
    }
  }
  $AtlasBody = @'
from tools.client_registry import register_atlassian

for label, status, detail in register_atlassian():
    if status == "skipped":
        print(f"  - {label}: not detected -- skipped")
    elif status == "present":
        print(f"  - {label}: an atlassian entry already exists -- left alone")
    elif status == "error":
        print(f"  ! {label}: could not update ({detail}) -- add manually:")
        print(
            '      {"mcpServers": {"atlassian": {"type": "http", '
            '"url": "https://mcp.atlassian.com/v1/mcp/authv2"}}}'
        )
    else:
        print(f"  + {label}: {status} ({detail}) -- restart it to finish OAuth")
'@
  $AtlasFile = Join-Path $env:TEMP ("qa-atlas-" + [guid]::NewGuid().ToString("N") + ".py")
  Set-Content -Path $AtlasFile -Value $AtlasBody -Encoding ASCII
  try {
    & $Py $AtlasFile 2>&1
  } finally {
    Remove-Item $AtlasFile -Force -ErrorAction SilentlyContinue
  }
  Write-Host "  - Claude Desktop: claude.ai -> Settings -> Connectors -> Atlassian -> Connect"
} elseif ($AtlasRc -eq 3) {
  Write-Host ""
  Write-Host "Atlassian auto-connect is off (QA_REGISTER_ATLASSIAN_MCP=false)."
  Write-Host "  To read Jira tickets, add this under mcpServers yourself and restart:"
  Write-Host "      ""atlassian"": {""type"": ""http"", ""url"": ""$AtlasUrl""}"
} else {
  Write-Host ""
  Write-Host "Could not read your settings, so Atlassian was left alone (nothing"
  Write-Host "  was written). This is NOT a flag you set. To read Jira tickets,"
  Write-Host "  add this under mcpServers yourself and restart your editor:"
  Write-Host "      ""atlassian"": {""type"": ""http"", ""url"": ""$AtlasUrl""}"
}

Write-Host "Done. Restart your editor(s) to pick up the server."
