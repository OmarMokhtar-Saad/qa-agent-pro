# Updates, versioning and integrity

- **Versioning** — [Semantic Versioning](https://semver.org/); every release is
  a git tag (`vX.Y.Z`) with notes in [CHANGELOG.md](../CHANGELOG.md).
- **Automatic and live** — releases are checked at startup and every 15 minutes
  while the server runs (tune with `QA_UPDATE_INTERVAL_MINUTES`). A new release
  installs in the background and takes effect once no tool is running: the
  server restarts itself and transparently replays the MCP handshake, so you
  normally do not restart your editor (see the exception below). This works even when a *different* editor sharing
  the same install applied the update.
- **On demand** — running `qa-doctor` always checks for and installs the newest
  release immediately, then reloads seamlessly. Re-running the installer does
  the same from a terminal.
- **Crash resilient** — if the server process dies, the launcher respawns it and
  your editor session continues.
- **Rare exception** — releases that change tool *definitions* need one editor
  restart (editors cache definitions and ignore refresh notifications).
  `qa-doctor` tells you explicitly when that is the case; otherwise no
  restart is needed.
- **Signed releases** — every release ships `MANIFEST.sig`, an Ed25519
  signature over the manifest. An update without a valid signature is refused.
  `QA_UPDATE_REQUIRE_SIGNATURE=false` is an emergency escape hatch only.
- **Read-only by design** — code files are hash-verified against
  `MANIFEST.sha256` and chmod'ed read-only
  on every start. Manual or AI-editor edits fail to save, and anything
  force-edited is restored on the next start. This repo is a build artifact:
  changes land here only through releases.
