# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.137.0] - 2026-10-18

### Added

- MCP (Model Context Protocol) server over stdio for Cursor, Claude Code
  and Claude Desktop.
- Test-case generation from a feature description, Jira ticket URL, web
  page URL, Swagger/OpenAPI link, or live mobile screens.
- Suite exports: Excel, CSV, TestRail, Gherkin, Playwright.
- Background updates: new releases install while the editor is running
  (GitHub Releases); the server then restarts itself to load them, which
  can interrupt a call in progress, and tells your editor what changed
  when it reconnects.
- One-command editor registration (`connect.sh`) for Cursor, Claude Code
  and Claude Desktop.
- RAG corpus and interactive wizard dialogs enabled by default (with
  automatic text-menu fallback on clients without elicitation support).
- BM25 corpus ranking with recency boost, per-feature filtering on
  qa_search_corpus, and automatic corpus pruning.
- Release-manifest integrity self-heal and read-only code lock.
- Anonymous, opt-out usage analytics (telemetry). See the README
  'Telemetry & privacy' section; disable with DO_NOT_TRACK=1.
- Desktop app: an in-app Update button installs the latest release, and
  Force update installs it again even when you are already on it.
- Each desktop release now ships a `qa-agents-desktop-<version>.dmg.sha256`
  file next to the DMG (`<hex>  <name>`), so you can check the download.

### Changed

- Updates: the keyboard (qa-ime) release no longer hides a newer
  server release; the updater picks the highest version tag.
- After an update the reply is flushed before the restart, so the
  client sees it.
- A fill step now shows its proof ("field 'X': N/N chars") in the
  step and case detail.
- Dialog notes name only the button actually tapped; skipped dialog
  steps no longer add a note.
- Case titles use the sub-goal submitted in the same turn.
- A wait that comes after an action no longer resets the "new text"
  baseline.
- OTP notes are clearer when the field reads empty but the screen
  moved on.
- A blank screen read no longer counts as "text is gone".
- Waits for text to appear end early on a still screen.
- Step timings no longer add up to more than the step itself.
- The run manifest records the qa-ime version.
- Saved routes that only wait or tap optional buttons are no longer
  saved, offered or replayed; routes are ranked by how well they fit
  the run's goal.
- Fills into ID or password fields are saved as secret steps.
- A replay skips a first wait for text that is not on the screen.
- App Tester skips the reinstall when the device already has the
  newest build, and its slow screen reads no longer count toward
  the run's timing.

### Known

- A count in the learned-mistakes table is how often the mistake was
  seen, including retries within one run, not how many runs saw it.
- A device or emulator lock file can still name its last holder after
  the lock is released; this is by design and does not block a run.
- A very large result (about 40 KB) is written to a file and the reply
  points to it; reading it back can make the next step slow to start.
- A screen that changes only in its pixels (video, canvas, maps) and
  sends no screen events still reads as unchanged.
- Notification permission is granted with pm grant only; the user-set
  flag and app-ops are not applied in this release.
- The permission step has not yet been checked on a real fresh
  install. A route that only taps through a permission pop-up is no
  longer saved; the pop-up is answered by the dialog handler on replay.
- App Tester still reads the screen through the slower uiautomator
  dump; it is skipped when the build is already current.
- Old saved routes that do nothing are cleaned up only when a new
  route is saved.
- The fallback keyboard cannot read the field back, so its fill proof
  says "?/N chars" instead of a count.
