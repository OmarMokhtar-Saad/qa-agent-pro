# Configuration

Optional. Edit `~/qa-agent-pro/.env` (created from `.env.example`) and restart
your editor. The most useful variables:

| Variable | Purpose |
|---|---|
| `QA_EXPORT_DIR` | Folder auto-exported Excel files are saved to (`data/exports`; persists across sessions and updates) |
| `QA_INSTALL_DIR` | Install location, read by the installer (default `~/qa-agent-pro`) |
| `QA_UPDATE_INTERVAL_MINUTES` | How often a running server checks for a new release (default 15) |
| `QA_MOBILE_HTTPS_CAPTURE_ENABLED` | Let the Android emulator lane capture the app's HTTPS traffic (downloads a proxy, installs a device certificate); off by default |
| `QA_UPDATE_REQUIRE_SIGNATURE` | On by default: updates without a valid Ed25519 signature are refused. Set to `false` only as an emergency escape hatch |
| `DO_NOT_TRACK` | Set to `1` to disable telemetry |

The complete list of variables the server reads is in
[environment.md](environment.md).

There is nothing to configure for generation itself: no LLM credential, no
backend, no Jira token, no switch for Swagger ingestion, mobile screen capture,
corpus learning or the interactive pickers. Those are always on, and the model
that writes your cases is the one in your editor.
