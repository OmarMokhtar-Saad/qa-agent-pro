# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.124.0] - 2026-10-16

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

- On a device where screen reads are fast, the extra screen-unchanged
  probe no longer runs: it cost about a second per action and never
  paid back. Slow devices keep it.
- The explore instructions and the rejection message say `sub_goal`
  goes inside the `script` JSON, not as a tool argument.

### Fixed

- Stopping a run, or a new run taking over an idle one, now turns the
  QA accessibility service off and restores the window animation
  scales, not just the keyboard.
- A code field that submits itself after the last digit is no longer
  read as a failed entry when the next screen cannot be read at once.
- A wait that ended early says how long it really waited instead of
  naming its full limit.
- A permission decline ("Not Allow", "Deny") is never learned as
  the way to dismiss a popup; the permission rule answers those.
