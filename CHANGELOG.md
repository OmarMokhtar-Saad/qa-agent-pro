# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.113.0] - 2026-10-15

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

### Changed

- Mobile runs are faster: the next step reuses the screen the last step
  already read instead of reading it again, and a tap already waits for the
  screen to settle, so a script can batch a whole form and its submit in one
  turn.
- New script op `tap_text_if_present`: taps the element whose text matches,
  and does nothing and continues if it is absent; use it for optional popups
  instead of a separate read turn.
- Waits for a condition can now run up to 120 seconds; fixed waits stay capped
  at 40 seconds.
- An older QA keyboard on the phone is upgraded in place. If its signature
  does not match, you get a one-line uninstall command; the keyboard is never
  removed for you. The fastest screen reading needs a newer QA keyboard
  release than the one this version installs.

### Fixed

- An OTP or PIN field that cannot be read back after typing is no longer
  reported as a device error that fails the case; its result is unknown.
- A `fill` whose label matches no field fills the only empty input field on
  the screen whose name fits the label, or one with no name; otherwise it
  lists the empty fields so the next step can name the right one.
- The login and OTP screens are no longer mistaken for the same screen, and a
  tap is only reported as having done nothing once the screen settled
  unchanged.
- Exploration runs report stumbles as warnings, not failures, and a field
  that failed and was then typed successfully is not counted as failed.
- After a run the device lease shows as released, the stop text says the run
  already finished when it recorded an end, and the reset hint no longer
  fires on unrelated text.
