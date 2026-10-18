# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.138.0] - 2026-10-19

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

- App Tester now reads the screen with the fast accessibility reader:
  it is switched on before App Tester starts and handed to the run,
  and switched off again if the run stops before it begins.
- "App missing" and "app present but its version is unreadable" are
  now two separate reasons.
- An empty screen read is logged with details and tried once more
  when the read was quick.
- Start-up shows progress notes (accessibility on, App Tester scan,
  hand-off).
- A wait for the screen to change checks the fast reader against a
  full screen read every few seconds.
- A wait for text to appear now gives up early (not found) after 10 s
  of a quiet screen, unless a loading spinner shows; a wait for text to
  go away never ends early.
- Fewer slow device calls while the screen stays the same.
- A case whose only checks were "X is gone" now says so instead of
  "asserted nothing".
- A field that read back empty before the screen moved on counts as
  unconfirmed, not verified, in the header tally.
- Step labels no longer name the MCP middleware.
- Later case packets are about a third smaller: the action schema
  no longer carries internal docs and titles.

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
- On a device without the QA keyboard, the first run's App Tester
  scan still uses the slower uiautomator dump.
- A loading screen with no standard progress bar (custom animation
  or splash image) can still end a wait for text early after 10 s.
- Old saved routes that do nothing are cleaned up only when a new
  route is saved.
- The fallback keyboard cannot read the field back, so its fill proof
  says "?/N chars" instead of a count.
