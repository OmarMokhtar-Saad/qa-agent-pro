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
  foreach ($v in @("3.12", "3.13", "3.11", "3.10", "3.14")) {
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
foreach ($v in @("3.12", "3.13", "3.11", "3.10", "3.14")) {
  if (Test-QaPython "py" @("-$v")) { $PyExe = "py"; $PyPre = @("-$v"); break }
}
if (-not $PyExe) {
  # python3.12-style names cover uv shims and several per-user layouts;
  # bare python3/python last, since that is where the Store alias sits.
  foreach ($n in @("python3.12", "python3.13", "python3.11", "python3.10",
                   "python3.14", "python3", "python")) {
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

# An existing folder is a reason to UPDATE, not a reason to refuse. Three
# cases, and only the third ever needed you to delete anything by hand:
#   complete install -> update it in place
#   partial/leftover -> repair it by overlaying the release
#   $env:QA_FORCE=1  -> replace the code, still keeping your data
$Mode = "install"
if (Test-Path $InstallDir) {
  if ($env:QA_FORCE) { $Mode = "reinstall" }
  elseif ((Test-Path (Join-Path $InstallDir "start.cmd")) -and
          (Test-Path (Join-Path $InstallDir "VERSION"))) { $Mode = "update" }
  elseif (Get-ChildItem -Path $InstallDir -Force -ErrorAction SilentlyContinue) {
    $marked = @("start.sh", "start.cmd", "VERSION", "launcher.py", "MANIFEST.sha256") |
      Where-Object { Test-Path (Join-Path $InstallDir $_) }
    if (-not $marked) {
      Write-Host "ERROR: $InstallDir is not empty and is not a QA Agent Pro install. Pick another QA_INSTALL_DIR."
      exit 1
    }
    $Mode = "repair"
    Write-Host "$InstallDir exists but is not a complete install -- repairing it."
  }
}

Write-Host "Fetching the latest release of $Repo ..."
# Highest published vX.Y.Z: GitHub's 'latest' can be a non-server tag.
$rel = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=100"
$tag = ($rel | Where-Object { -not $_.draft -and -not $_.prerelease -and $_.tag_name -match '^v\d+\.\d+\.\d+$' } |
  Sort-Object { [version]$_.tag_name.Substring(1) } | Select-Object -Last 1).tag_name
if (-not $tag) { Write-Host "ERROR: no published release found."; exit 1 }
if ($Mode -eq "update") {
  $have = "v" + ((Get-Content (Join-Path $InstallDir "VERSION") -Raw).Trim())
  if ($have -eq $tag) {
    Write-Host "Already on the latest release ($tag). Refreshing dependencies + editor registration ..."
  } else {
    Write-Host "Updating $have -> $tag (your .env, suites and corpus are kept) ..."
  }
}
$tmp = Join-Path $env:TEMP ("qa-agent-pro-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
  $zip = Join-Path $tmp "release.zip"
  Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/tags/$tag.zip" -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "x") -Force
  $src = (Get-ChildItem -Path (Join-Path $tmp "x") -Directory | Select-Object -First 1).FullName
  # Verify BEFORE anything is written: MANIFEST.sig (Ed25519, keys embedded here,
  # pure-Python verifier inlined here) then every file hash. Not the release's code.
  $verifyPy = Join-Path $tmp "qa_verify.py"
  $verifySrc = @'
"""Pure-Python Ed25519 signature VERIFY (RFC 8032, stdlib only, no signing).

Used by install.sh / install.ps1, which run before any venv (so before
``cryptography``) exists: the installer inlines this file's source. Slow
(tens of ms) but fine for one check per install.
"""


import hashlib
from typing import Optional

P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)
MASK = (1 << 255) - 1
Point = tuple  # extended coordinates (X, Y, Z, T)


def _add(a: Point, b: Point) -> Point:
    """Unified twisted-Edwards addition (valid for doubling too)."""
    aa, bb = (a[1] - a[0]) * (b[1] - b[0]) % P, (a[1] + a[0]) * (b[1] + b[0]) % P
    cc, dd = 2 * a[3] * b[3] * D % P, 2 * a[2] * b[2] % P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(scalar: int, point: Point) -> Point:
    """Double-and-add scalar multiplication."""
    acc: Point = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            acc = _add(acc, point)
        point = _add(point, point)
        scalar >>= 1
    return acc


def _same(a: Point, b: Point) -> bool:
    """Projective equality of two points."""
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    """The x for this y and sign bit, or None when no such curve point exists."""
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P or (x == 0 and sign):
        return None
    return P - x if (x & 1) != sign else x


def _decode(raw: bytes) -> Optional[Point]:
    """Decompress a 32-byte point; None when it is not on the curve."""
    word = int.from_bytes(raw, "little")
    y = word & MASK
    x = _recover_x(y, word >> 255)
    return None if x is None else (x, y, 1, x * y % P)


def _base() -> Point:
    """The RFC 8032 base point."""
    y = 4 * pow(5, P - 2, P) % P
    x = _recover_x(y, 0)
    if x is None:
        raise ValueError("no base point")
    return (x, y, 1, x * y % P)


def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """True only when ``signature`` is a valid Ed25519 signature of ``message``."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a, r = _decode(public_key), _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= Q:
        return False
    digest = hashlib.sha512(signature[:32] + public_key + message).digest()
    k = int.from_bytes(digest, "little") % Q
    return _same(_mul(s, _base()), _add(r, _mul(k, a)))

"""Install-time release verification: MANIFEST.sig, then every file hash (stdlib only).

install.sh / install.ps1 inline this file (after tools/ed25519_pure.py) into the
installer and run it BEFORE install_apply.py touches the install directory, with
the release public keys passed on the command line. The trust root is therefore
the installer itself, never code inside the downloaded release.

    release_verify.py TREE KEY_HEX [KEY_HEX ...]    exit 0 = verified, 1 = refuse
"""


import base64
import hashlib
import sys
from pathlib import Path

if __package__:
    from tools.ed25519_pure import verify

# == install_apply.PROTECTED: the only paths the installer never copies. Nothing else
# is exempt, bytecode included (a stray .pyc runs unsigned code on import).
SKIP_TOP = {".git", ".env", "data", "corpus", "backups", ".venv"}
SIDECARS = {"MANIFEST.sha256", "MANIFEST.sig"}


class Refused(Exception):
    """The release must not be installed; the message says why."""


def _any_key(keys: list, sig: bytes, payload: bytes) -> bool:
    """True when any hex key in ``keys`` verifies ``sig``; bad entries are skipped."""
    for key in keys:
        try:
            if verify(bytes.fromhex(key), sig, payload):
                return True
        except ValueError:
            continue
    return False


def check_signature(tree: Path, keys: list) -> bytes:
    """Verify MANIFEST.sig over MANIFEST.sha256; return the manifest bytes."""
    sig_file, manifest = tree / "MANIFEST.sig", tree / "MANIFEST.sha256"
    if not sig_file.is_file() or not manifest.is_file():
        raise Refused("the release has no MANIFEST.sig (unsigned release)")
    try:
        sig = base64.b64decode(sig_file.read_text(encoding="utf-8").strip())
    except ValueError:
        raise Refused("MANIFEST.sig is not valid base64") from None
    payload = manifest.read_bytes()
    if not _any_key(keys, sig, payload):
        raise Refused("MANIFEST.sig does not verify against the release keys")
    return payload


def parse_manifest(payload: bytes) -> dict:
    """``{relative posix path: sha256 hex}``; unsafe paths are refused."""
    entries = {}
    for line in payload.decode("utf-8").splitlines():
        digest, _, rel = line.partition("  ")
        parts = rel.split("/")
        unsafe = rel.startswith("/") or ".." in parts or "\\" in rel or ":" in rel
        if len(digest) != 64 or not rel or unsafe:
            raise Refused(f"malformed MANIFEST line: {line[:60]!r}")
        entries[rel] = digest
    return entries


def check_hashes(tree: Path, entries: dict) -> None:
    """Every manifest entry must exist with exactly the signed hash."""
    for rel, digest in entries.items():
        path = tree / rel
        if not path.is_file():
            raise Refused(f"{rel} is listed in MANIFEST but missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise Refused(f"{rel} does not match its MANIFEST hash")


def check_unlisted(tree: Path, entries: dict) -> None:
    """No file the installer would copy may be absent from the signed manifest."""
    for path in tree.rglob("*"):
        rel = path.relative_to(tree).as_posix()
        if not path.is_file() or rel in SIDECARS or rel in entries:
            continue
        if rel.split("/")[0] in SKIP_TOP:
            continue
        raise Refused(f"{rel} is in the release but not in the signed MANIFEST")


def verify_release(tree: Path, keys: list) -> None:
    """Signature first, then hashes; raises ``Refused`` on any problem."""
    entries = parse_manifest(check_signature(tree, keys))
    check_hashes(tree, entries)
    check_unlisted(tree, entries)


def main(argv: list) -> int:
    """CLI entry: 0 when the release verifies, 1 (with a message) when it does not."""
    try:
        verify_release(Path(argv[1]), argv[2:])
    except (Refused, OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"release verification FAILED: {exc}\n")
        return 1
    sys.stdout.write("Release signature and file hashes verified.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'@
  [IO.File]::WriteAllText($verifyPy, $verifySrc, (New-Object Text.UTF8Encoding($false)))
  $keys = "78a4e6d773483159637930e01db8de2448112117706aa908172e87211e0ac2d9 4c43769703fb44da543f15402a88c590990f0b0f7c0574caa935c6e16353beff" -split " "
  $vArgs = @($PyPre + @("-I", $verifyPy, $src) + $keys)
  if ((Invoke-QaNative $PyExe $vArgs) -ne 0) { throw "release $tag failed signature/hash verification; nothing was installed." }
  New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
  # Backup + rollback + REMOVED_PATHS prune via the release's own helper; .env,
  # data/, corpus/ and backups/ are never touched.
  $aArgs = @($PyPre + @("-I", (Join-Path $src "tools\install_apply.py"), "apply", $src, $InstallDir))
  if ((Invoke-QaNative $PyExe $aArgs) -ne 0) { throw "could not apply release $tag; the previous files were restored." }
} finally {
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Set-Location $InstallDir
$VenvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
# Reuse a healthy venv -- an update should not re-download every wheel --
# and rebuild it when it is missing, broken, or on a Python now too old.
$VenvProbe = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
$VenvOk = (Test-Path $VenvPy) -and
         ((Invoke-QaNative $VenvPy @("-c", $VenvProbe) -Quiet) -eq 0)
if ($VenvOk) {
  Write-Host "Updating dependencies in the existing virtualenv ..."
} else {
  Write-Host "Creating virtualenv + installing dependencies (a few minutes) ..."
  Remove-Item .venv -Recurse -Force -ErrorAction SilentlyContinue
  if ((Invoke-QaNative $PyExe @($PyPre + @("-m", "venv", ".venv"))) -ne 0) {
    Write-Host "ERROR: could not create the virtualenv."
    exit 1
  }
}
if (-not (Test-Path $VenvPy)) {
  Write-Host "ERROR: virtualenv creation failed ($VenvPy is missing)."
  exit 1
}
# pip upgrade is best-effort; the dependency install is not. pip routinely
# writes notices to stderr, which is the same NativeCommandError trap.
Invoke-QaNative $VenvPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip") -Quiet | Out-Null
if ((Invoke-QaNative $VenvPy @("-m", "pip", "install", "--quiet", "--upgrade", "-e", ".", "-c", "constraints.txt")) -ne 0) {
  Write-Host "ERROR: dependency install failed (pip output above)."
  exit 1
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
# Lock code files read-only (the launcher re-locks + self-heals each start).
$LockCode = "from pathlib import Path; from tools.updater import lock_files; lock_files(Path('.'))"
Invoke-QaNative $VenvPy @("-c", $LockCode) -Quiet | Out-Null
Write-Host ""
switch ($Mode) {
  "update"    { Write-Host "QA Agent Pro is up to date at $tag in $InstallDir" }
  "repair"    { Write-Host "Repaired QA Agent Pro $tag in $InstallDir" }
  "reinstall" { Write-Host "Reinstalled QA Agent Pro $tag in $InstallDir" }
  default     { Write-Host "Installed QA Agent Pro $tag to $InstallDir" }
}
Write-Host ""
Write-Host "Registering with your AI editors ..."
Invoke-QaNative "powershell" @("-ExecutionPolicy", "Bypass", "-File",
                              (Join-Path $InstallDir "connect.ps1")) | Out-Null
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Restart Cursor / Claude, then ask it: run qa-doctor"
Write-Host ""
Write-Host "No API key and no login are needed - your own chat model writes the"
Write-Host "test cases. Optional settings live in: $InstallDir\.env"
Write-Host ""
Write-Host "To re-register editors later, run: $InstallDir\connect.ps1"
Write-Host "To update later, just re-run this installer (or let the server self-update)."
