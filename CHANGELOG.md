# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.117.0] - 2026-10-15

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

- Learned flows no longer keep change waits that saw no change when
  they were recorded, and replaying an older flow skips such waits.
- A replay stops and hands back to the model when a step leaves the
  app or lands on a screen the flow never recorded, and says which.
- A screen key prefers the screen's own activity when the screen
  carries one, and replay also matches keys stored by older flows.
- Explore turns that replayed a learned flow name the flow and the
  steps replayed in their title.
- Faster steps: a typed value the keyboard confirmed is not read back
  again before the next field, a tap is not settled again when the
  next field to type into is already on screen, and a masked field
  (password, OTP) is checked with one screen read after the focus
  moves instead of repeated reads.
- App Tester remembers an app's row and list position after any
  successful find, and after the Update tap it watches the foreground
  app instead of reading the full screen on every check.

### Fixed

- A screen read that hangs on an animating screen times out after
  20 s and is retried once.
- Calls rejected for unexpected arguments log the argument names (never
  the values).
