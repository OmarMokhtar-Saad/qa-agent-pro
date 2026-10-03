# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.104.1] - 2026-10-03

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

- Internal clean-up of test-case preparation, suite finalizing, Jira
  ticket reading and mobile reports. Nothing you do or see changes.

### Fixed

- When a saved route replay stops at the time limit, the reply no
  longer says the remaining steps are queued to run next: nothing is
  queued for a route, and the reply asks for the steps still needed.
- `qa-doctor` gives the QA keyboard check enough time on a slow device
  instead of reporting that it could not check; a check that does run
  out of time names its limit.
- `qa-doctor` says when the QA keyboard on a device is not the build
  this release ships; the next run that types replaces it for you.
