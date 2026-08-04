# One-line installer for QA Agent Pro (MCP server) on native Windows:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.ps1 | iex"
#
# No administrator rights are needed anywhere in here: the install lands
# under your own user profile and writes nothing outside it. WSL is NOT
# required -- this is the native path.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is ~10x slower with it
$Repo = "OmarMokhtar-Saad/qa-agent-pro"
$InstallDir = if ($env:QA_INSTALL_DIR) { $env:QA_INSTALL_DIR }
              else { Join-Path $env:USERPROFILE "qa-agent-pro" }

# Windows PowerShell 5.1 can still default to TLS 1.0, which GitHub refuses.
try {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {}

# fastmcp needs Python 3.10+. Prefer the py launcher (it knows every
# per-user install), then a bare python3/python. A machine with no Python at
# all answers `python` with the Store stub, which exits non-zero -- so the
# probe below rejects it rather than believing it.
function Test-QaPython {
  param([string]$Exe, [string[]]$Pre)
  if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
  $probe = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
  & $Exe @($Pre + @("-c", $probe)) 2>$null | Out-Null
  return ($LASTEXITCODE -eq 0)
}
$PyExe = $null
$PyPre = @()
foreach ($v in @("3.13", "3.12", "3.11", "3.10")) {
  if (Test-QaPython "py" @("-$v")) { $PyExe = "py"; $PyPre = @("-$v"); break }
}
if (-not $PyExe) {
  foreach ($n in @("python3", "python")) {
    if (Test-QaPython $n @()) { $PyExe = $n; break }
  }
}
if (-not $PyExe) {
  Write-Host "ERROR: Python 3.10 or newer is required (none found on PATH)."
  Write-Host "Install it WITHOUT admin rights, either way:"
  Write-Host "  * python.org installer, leave 'Install for all users' UNCHECKED"
  Write-Host "  * or: powershell -ExecutionPolicy Bypass -c \"irm https://astral.sh/uv/install.ps1 | iex\""
  Write-Host "       then: uv python install 3.12"
  exit 1
}
Write-Host ("Using " + (& $PyExe @($PyPre + @("--version")) 2>&1))

if ((Test-Path $InstallDir) -and (-not $env:QA_FORCE)) {
  Write-Host "$InstallDir already exists."
  Write-Host "Updates are automatic every time your MCP client starts the server."
  Write-Host "Set QA_FORCE=1 to reinstall from scratch."
  exit 1
}

Write-Host "Fetching the latest release of $Repo ..."
$tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
$tmp = Join-Path $env:TEMP ("qa-agent-pro-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
  $zip = Join-Path $tmp "release.zip"
  Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/tags/$tag.zip" -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "x") -Force
  $src = (Get-ChildItem -Path (Join-Path $tmp "x") -Directory | Select-Object -First 1).FullName
  New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
  Copy-Item -Path (Join-Path $src "*") -Destination $InstallDir -Recurse -Force
} finally {
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Set-Location $InstallDir
Write-Host "Creating virtualenv + installing dependencies (a few minutes) ..."
& $PyExe @($PyPre + @("-m", "venv", ".venv"))
$VenvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  Write-Host "ERROR: virtualenv creation failed ($VenvPy is missing)."
  exit 1
}
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -e .
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
# Lock code files read-only (the launcher re-locks + self-heals each start).
& $VenvPy -c "from pathlib import Path; from tools.updater import lock_files; lock_files(Path('.'))"
Write-Host ""
Write-Host "Installed QA Agent Pro $tag to $InstallDir"
Write-Host ""
Write-Host "Registering with your AI editors ..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $InstallDir "connect.ps1")
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Restart Cursor / Claude, then ask it: run qa_setup_check"
Write-Host ""
Write-Host "No API key and no login are needed - your own chat model writes the"
Write-Host "test cases. Optional settings live in: $InstallDir\.env"
Write-Host ""
Write-Host "To re-register editors later, run: $InstallDir\connect.ps1"
