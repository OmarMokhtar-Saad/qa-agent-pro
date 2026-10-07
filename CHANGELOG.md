# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.107.0] - 2026-10-07

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

- Mobile runs now learn about each Android app as they go: screen
  timings, popups and how to dismiss them, working locators, shortcuts
  between screens and past mistakes. Later runs on the same app reuse
  what was learned, and the run report gains an app-knowledge section.
- New qa_mobile_knowledge tool: list, show, confirm, reject, edit, export
  or import what was learned about one app.
- Notes saved with the `note` argument of qa_mobile_test can name their
  app with `app=`. The run report shows which notes applied at each step.

### Fixed

- An avoid note no longer refuses assert or wait steps. A wait whose
  condition already holds is reported as already met.
- qa_mobile_notes says how many notes it is showing out of the total, and
  old note history is pruned once it reaches its cap.
- Stored run history, learning markers and rollback records are capped,
  so the per-app data no longer grows without limit.
