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

# Native commands are the trap on Windows: PowerShell converts anything a
# native exe writes to STDERR into a NativeCommandError record, and under
# $ErrorActionPreference='Stop' that record is TERMINATING. The Microsoft
# Store python3 stub ALWAYS writes its 'Python was not found' notice to
# stderr, so merely PROBING it aborted the whole installer -- v1.41.0 died
# exactly there on a real Windows 11 box, printing a NativeCommandError
# instead of the friendly 'install Python' message ten lines below.
#
# 2>$null does NOT prevent this; merging 2>&1 does, because the stderr text
# arrives as plain strings. The exit code stays the thing we judge on.
function Invoke-QaNative {
  param([string]$Exe, [string[]]$Argv, [switch]$Quiet)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & $Exe @Argv 2>&1
    if (-not $Quiet) { $out | ForEach-Object { Write-Host $_ } }
  } catch {
    return 1
  } finally {
    $ErrorActionPreference = $prev
  }
  return $LASTEXITCODE
}

# fastmcp needs Python 3.10+. Prefer the py launcher (it knows every
# per-user install), then a bare python3/python -- the Store alias included,
# probed like any other candidate and rejected on its non-zero exit.
function Test-QaPython {
  param([string]$Exe, [string[]]$Pre)
  if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
  $probe = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
  return ((Invoke-QaNative $Exe @($Pre + @("-c", $probe)) -Quiet) -eq 0)
}
# uv is what this project recommends for a NON-ADMIN Windows install, and
# it is the one interpreter neither PATH nor the py launcher can see: uv
# keeps managed CPythons under %LOCALAPPDATA%\uv\python\..., and uv's own
# installer does not touch the PATH of the shell you are standing in. So ask
# uv itself, and look for uv in ~/.local/bin even when it is not on PATH --
# that exact combination dead-ended the recommended route on a real machine.
function Find-QaUvPython {
  $uv = $null
  if (Get-Command uv -ErrorAction SilentlyContinue) { $uv = "uv" }
  else {
    $cand = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $cand) { $uv = $cand }
  }
  if (-not $uv) { return $null }
  foreach ($v in @("3.13", "3.12", "3.11", "3.10")) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $found = & $uv python find $v 2>&1 } catch { $found = $null }
    finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -eq 0 -and $found) {
      $exe = ($found | Select-Object -First 1).ToString().Trim()
      if (Test-Path $exe) { return $exe }
    }
  }
  return $null
}

$PyExe = $null
$PyPre = @()
foreach ($v in @("3.13", "3.12", "3.11", "3.10")) {
  if (Test-QaPython "py" @("-$v")) { $PyExe = "py"; $PyPre = @("-$v"); break }
}
if (-not $PyExe) {
  # python3.12-style names cover uv shims and several per-user layouts;
  # bare python3/python last, since that is where the Store alias sits.
  foreach ($n in @("python3.13", "python3.12", "python3.11", "python3.10",
                   "python3", "python")) {
    if (Test-QaPython $n @()) { $PyExe = $n; break }
  }
}
if (-not $PyExe) {
  $uvPy = Find-QaUvPython
  if ($uvPy -and (Test-QaPython $uvPy @())) {
    $PyExe = $uvPy
    Write-Host "Found a uv-managed interpreter: $uvPy"
  }
}
# Last resort before failing: if uv IS here but has no interpreter yet, use
# it. The tester already opted into installing this server, uv is already on
# their machine, and the download is per-user -- so the alternative is
# failing with instructions to run a command we could just run.
if (-not $PyExe) {
  $uv = $null
  if (Get-Command uv -ErrorAction SilentlyContinue) { $uv = "uv" }
  else {
    $cand = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $cand) { $uv = $cand }
  }
  if ($uv) {
    Write-Host "No Python found, but uv is installed -- fetching Python 3.12 ..."
    Invoke-QaNative $uv @("python", "install", "3.12") | Out-Null
    $uvPy = Find-QaUvPython
    if ($uvPy -and (Test-QaPython $uvPy @())) { $PyExe = $uvPy }
  }
}
if (-not $PyExe) {
  Write-Host "ERROR: Python 3.10 or newer is required (none found on PATH)."
  Write-Host "A bare python3/python that offers to open the Microsoft Store"
  Write-Host "is the App Execution Alias, not a real install -- it does not count."
  Write-Host "Install Python WITHOUT admin rights, either way:"
  Write-Host "  * python.org installer, leave 'Install for all users' UNCHECKED"
  Write-Host "  * or: powershell -ExecutionPolicy Bypass -c \"irm https://astral.sh/uv/install.ps1 | iex\""
  Write-Host "       then: uv python install 3.12"
  exit 1
}
$PyLabel = (@($PyExe) + $PyPre) -join ' '
Write-Host "Using Python interpreter: $PyLabel"

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
if ((Invoke-QaNative $PyExe @($PyPre + @("-m", "venv", ".venv"))) -ne 0) {
  Write-Host "ERROR: could not create the virtualenv."
  exit 1
}
$VenvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  Write-Host "ERROR: virtualenv creation failed ($VenvPy is missing)."
  exit 1
}
# pip upgrade is best-effort; the dependency install is not. pip routinely
# writes notices to stderr, which is the same NativeCommandError trap.
Invoke-QaNative $VenvPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip") -Quiet | Out-Null
if ((Invoke-QaNative $VenvPy @("-m", "pip", "install", "--quiet", "-e", ".")) -ne 0) {
  Write-Host "ERROR: dependency install failed (pip output above)."
  exit 1
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
# Lock code files read-only (the launcher re-locks + self-heals each start).
$LockCode = "from pathlib import Path; from tools.updater import lock_files; lock_files(Path('.'))"
Invoke-QaNative $VenvPy @("-c", $LockCode) -Quiet | Out-Null
Write-Host ""
Write-Host "Installed QA Agent Pro $tag to $InstallDir"
Write-Host ""
Write-Host "Registering with your AI editors ..."
Invoke-QaNative "powershell" @("-ExecutionPolicy", "Bypass", "-File",
                              (Join-Path $InstallDir "connect.ps1")) | Out-Null
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Restart Cursor / Claude, then ask it: run qa_setup_check"
Write-Host ""
Write-Host "No API key and no login are needed - your own chat model writes the"
Write-Host "test cases. Optional settings live in: $InstallDir\.env"
Write-Host ""
Write-Host "To re-register editors later, run: $InstallDir\connect.ps1"
