# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.107.1] - 2026-10-07

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

- qa_mobile_knowledge import now checks rows for every kind of app
  knowledge, not only notes. Imported rows start with no evidence, so
  they must prove themselves on your own runs before they are used.
- Import now takes only notes, lessons, timings, popups and elements.
  Shortcuts, screens, edges and mistakes are learned only from your own
  runs; the rest of an export still imports.
- Imported rows may fill at most half of a table's limit, so imports
  never leave your own runs unable to save what they learn.

### Fixed

- An import with one damaged row skips that row instead of failing as a
  whole. Rows whose step or locator data is unreadable are skipped too.
- An imported popup whose dismiss button looks destructive (for example
  delete or sign out) is refused, as it is when learned on a run.
- Refused writes to the app-knowledge store are capped per app, so
  repeated bad imports can no longer grow it without limit.
- A saved route with malformed steps is skipped instead of stopping
  shortcut learning for that app.
