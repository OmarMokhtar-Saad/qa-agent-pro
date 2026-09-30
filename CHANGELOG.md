# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.103.0] - 2026-09-30

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

- App notes refuse more secrets by name: API keys, tokens, card
  numbers, contact details, assigned credentials (`pin=...`) and
  spaced-out PINs. Verdicts such as "Pass: Yes" still save.
- The notes store is readable only by your user account, and package
  names that differ only in case get separate stores.

### Fixed

- A note sent with a mobile step is saved only after the step runs and
  keeps its run, and it is checked against the values the step typed.
- Loading or saving notes no longer blocks the server, and cancelling
  a run while notes load no longer leaves them half-applied.
- Retiring a note while another run reads it no longer races.
