#!/usr/bin/env bash
# One-line installer for QA Agent Pro (MCP server):
#   curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
#
# Safe to re-run: a complete install is UPDATED in place, an incomplete one is
# repaired, and .env / data/ / corpus/ are never touched. Needs no sudo and
# writes nothing outside $HOME. If no suitable Python is on PATH it provisions
# a private one with uv rather than asking you to find a package manager.
set -euo pipefail
REPO="OmarMokhtar-Saad/qa-agent-pro"
INSTALL_DIR="${QA_INSTALL_DIR:-$HOME/qa-agent-pro}"
# fastmcp needs 3.10+. PY_PREFERRED is the version we PROVISION when the
# machine has none -- the newest CPython line with the full wheel coverage
# this dependency set relies on, not necessarily the newest one released.
PY_PREFERRED="3.12"

die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

case "$(uname -s 2>/dev/null || echo unknown)" in
  Darwin | Linux | *BSD*) ;;
  MINGW* | MSYS* | CYGWIN*)
    die "This is the macOS/Linux/WSL installer, and you are in a Windows shell.
       Native Windows has its own one-liner -- run this in PowerShell:
         powershell -ExecutionPolicy Bypass -c \"irm https://raw.githubusercontent.com/$REPO/main/install.ps1 | iex\"" ;;
esac

command -v curl >/dev/null || die "curl is required and was not found on PATH."

py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }

# Newest suitable interpreter already on the machine wins; the venv it creates
# is what every later start uses, so this choice is made once.
PYBIN=""
# 3.12 first (newest line with full wheel coverage), 3.14 last.
for c in python3.12 python3.13 python3.11 python3.10 python3.14 python3; do
  if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then
    PYBIN="$(command -v "$c")"
    break
  fi
done

# Nothing suitable: provision one under $HOME with uv. This is deliberately not
# `brew` / `apt-get` / `dnf` -- picking the wrong one of those is the single
# most common way this install fails, and it is the only step that would want
# admin rights. uv is identical on macOS, Linux and WSL.
if [ -z "$PYBIN" ]; then
  echo "No Python 3.10+ found on PATH."
  UV=""
  if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
  elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"
  else
    echo "Installing uv (per-user, no sudo, into ~/.local/bin) ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
      || die "could not install uv. Install Python $PY_PREFERRED yourself, then re-run."
    [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
  fi
  [ -n "$UV" ] || die "uv installed but was not found at ~/.local/bin/uv."
  echo "Fetching Python $PY_PREFERRED with uv (nothing is installed system-wide) ..."
  "$UV" python install "$PY_PREFERRED" >/dev/null 2>&1 \
    || die "uv could not fetch Python $PY_PREFERRED."
  PYBIN="$("$UV" python find "$PY_PREFERRED" 2>/dev/null || true)"
  [ -n "$PYBIN" ] && [ -x "$PYBIN" ] && py_ok "$PYBIN" \
    || die "uv fetched Python $PY_PREFERRED but it could not be located."
fi
echo "Using $("$PYBIN" --version) at $PYBIN"

# An existing directory is a reason to UPDATE, not a reason to refuse. Three
# cases, and only the third ever needed you to delete anything by hand:
#   complete install -> update it in place
#   partial/leftover -> repair it by overlaying the release
#   QA_FORCE=1       -> replace the code, still keeping your data
MODE="install"
if [ -e "$INSTALL_DIR" ]; then
  [ -d "$INSTALL_DIR" ] || die "$INSTALL_DIR exists and is not a directory."
  # Never overlay a folder that is not a QA Agent Pro install.
  if [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    _m=0
    for f in start.sh start.cmd VERSION launcher.py MANIFEST.sha256; do
      [ -e "$INSTALL_DIR/$f" ] && _m=1
    done
    [ "$_m" = 1 ] || die "$INSTALL_DIR is not empty and is not a QA Agent Pro install. Pick another QA_INSTALL_DIR."
  fi
  if [ -n "${QA_FORCE:-}" ]; then
    MODE="reinstall"
  elif [ -f "$INSTALL_DIR/start.sh" ] && [ -f "$INSTALL_DIR/VERSION" ]; then
    MODE="update"
  elif [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    MODE="repair"
    echo "$INSTALL_DIR exists but is not a complete install -- repairing it."
  fi
fi

echo "Fetching the latest release of $REPO ..."
# Highest published vX.Y.Z: GitHub's 'latest' can be a non-server tag.
AUTH=""; [ -z "${GITHUB_TOKEN:-}" ] || AUTH="Authorization: Bearer $GITHUB_TOKEN"
TAG=$(curl -fsSL --connect-timeout 15 --max-time 60 ${AUTH:+-H "$AUTH"} \
  "https://api.github.com/repos/$REPO/releases?per_page=100" \
  | "$PYBIN" -c "import json,re,sys; r=[x for x in json.load(sys.stdin) if not x.get('draft') and not x.get('prerelease') and re.fullmatch(r'v[0-9]+[.][0-9]+[.][0-9]+', str(x.get('tag_name')))]; r.sort(key=lambda x: tuple(map(int, x['tag_name'][1:].split('.')))); print(r[-1]['tag_name'] if r else '')") \
  || die "could not reach the GitHub releases API. Check your network and retry."
[ -n "$TAG" ] || die "the GitHub releases API returned no tag."

if [ "$MODE" = "update" ]; then
  HAVE="v$(tr -d ' \t\n\r' < "$INSTALL_DIR/VERSION" 2>/dev/null || true)"
  if [ "$HAVE" = "$TAG" ]; then
    echo "Already on the latest release ($TAG). Refreshing dependencies + editor registration ..."
  else
    echo "Updating $HAVE -> $TAG (your .env, suites and corpus are kept) ..."
  fi
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/release.zip" "https://github.com/$REPO/archive/refs/tags/$TAG.zip" \
  || die "could not download release $TAG."
"$PYBIN" - "$TMP" <<'PYEOF'
import sys, zipfile
from pathlib import Path
tmp = Path(sys.argv[1])
zipfile.ZipFile(tmp / "release.zip").extractall(tmp / "x")
PYEOF
SRC=$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -n "$SRC" ] || die "release archive $TAG was empty."

# Verify the release BEFORE anything is written (inlined Ed25519 verifier + embedded keys, not release code).
"$PYBIN" -I - "$SRC" 78a4e6d773483159637930e01db8de2448112117706aa908172e87211e0ac2d9 4c43769703fb44da543f15402a88c590990f0b0f7c0574caa935c6e16353beff <<'RELEASE_VERIFY_EOF' \
  || die "release $TAG failed signature/hash verification; nothing was installed."
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
RELEASE_VERIFY_EOF

mkdir -p "$INSTALL_DIR"
# Overlay with backup, rollback and REMOVED_PATHS prune (the release's own
# tools/install_apply.py). .env, data/, corpus/ and backups/ are never touched.
"$PYBIN" -I "$SRC/tools/install_apply.py" apply "$SRC" "$INSTALL_DIR" \
  || die "could not apply release $TAG; the previous files were restored."

cd "$INSTALL_DIR"
# Reuse a healthy venv (an update should not re-download every wheel); rebuild
# it when it is missing, broken, or built on a Python that is now too old.
if [ ! -x .venv/bin/python ] || ! py_ok .venv/bin/python; then
  echo "Creating virtualenv + installing dependencies (a few minutes) ..."
  rm -rf .venv
  "$PYBIN" -m venv .venv
else
  echo "Updating dependencies in the existing virtualenv ..."
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet --upgrade -e . -c constraints.txt
[ -f .env ] || cp .env.example .env
# Lock code files read-only (the launcher re-locks + self-heals each start).
.venv/bin/python -c "from pathlib import Path; from tools.updater import lock_files; lock_files(Path('.'))"
chmod +x start.sh connect.sh
echo ""
case "$MODE" in
  update)    echo "QA Agent Pro is up to date at $TAG in $INSTALL_DIR" ;;
  repair)    echo "Repaired QA Agent Pro $TAG in $INSTALL_DIR" ;;
  reinstall) echo "Reinstalled QA Agent Pro $TAG in $INSTALL_DIR" ;;
  *)         echo "Installed QA Agent Pro $TAG to $INSTALL_DIR" ;;
esac
echo ""
echo "Registering with your AI editors ..."
"$INSTALL_DIR/connect.sh" || true
echo ""
echo "Next steps:"
echo "  1. Restart Cursor / Claude, then ask it: run qa-doctor"
echo ""
echo "No API key and no login are needed - your own chat model writes the"
echo "test cases. Optional settings live in: $INSTALL_DIR/.env"
echo ""
echo "To re-register editors later, run: $INSTALL_DIR/connect.sh"
echo "To update later, just re-run this installer (or let the server self-update)."
