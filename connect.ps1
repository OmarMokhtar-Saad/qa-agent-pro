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
  & claude mcp remove --scope user qa-agent-pro 2>$null | Out-Null
  & claude mcp add --scope user qa-agent-pro -- "$Start" 2>$null | Out-Null
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
  & $Py $PyFile "$Start"
} finally {
  Remove-Item $PyFile -Force -ErrorAction SilentlyContinue
}

Write-Host "Done. Restart your editor(s) to pick up the server."
