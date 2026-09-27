# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.100.1] - 2026-09-28

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

### Fixed

- A confirmation after the destructive-action guard stops now releases
  only the exact control that was stopped, and only once. Confirming one
  control can no longer let a different control, or a second tap,
  through; a confirmation that does not match refuses by name.
- The QA keyboard is selected by the exact ID the device lists, and
  `qa_mobile_test` refuses a keyboard ID it cannot find instead of
  guessing.
- Typing waits until the QA keyboard is active, and when it cannot
  type, the reply names the blocker and stops instead of continuing.
- A typed field is read back after typing: a field that comes back
  empty is reported as not typed, and a turn that was blocked can no
  longer be reported as a pass.
- The agent is told never to report a screen state, field value or
  login result it did not read from the app, and never to fall back to
  raw adb when the server cannot act.
