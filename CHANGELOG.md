# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.111.0] - 2026-10-14

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

- A typed field that reads empty afterwards is reported as unconfirmed,
  not as an error: the run first waits, bounded, for the screen to move
  on (an auto-submitting one-time code clears its field).
- Learning keeps only what a run proved: flows are saved per app package,
  steps on another app's screens are skipped, an unconfirmed outcome is
  never learned, and a mistake the run moved past is dropped.

### Fixed

- Dialog guard: destructive words match as whole words, so release notes
  that mention a removal are no longer refused as a Remove button; short
  labels, labels that open with a destructive word, Arabic labels and
  camelCase ids such as btnDelete are refused.
- A run marks a device lease released only when it held that lease, and
  the setup phase before a run no longer restores keyboard, animation
  and accessibility settings it never changed.
