# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.119.0] - 2026-10-15

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

- Fewer screen reads per step: a cheap screen check (focused window
  plus a screenshot fingerprint) tells whether the screen moved, so
  settles, element and text waits, change waits and optional taps
  read the full screen only when it did. Secure screens that block
  screenshots fall back to full reads.
- A step no longer settles before a wait that reads the screen itself,
  a field's read-back is reused, switching keyboards keeps the last
  read, and a read cut short is kept for the next step.
- Dialog settles wait for the dialog to close instead of a fixed pause.

### Fixed

- A learned flow no longer breaks mid-replay when the app shows a
  screen from another learned flow (onboarding, a Face ID prompt):
  replay switches to that flow and comes back, at most twice a turn.
- A step that lands on a known next screen ends its landing wait at
  once, and a typed value followed by another text step is no longer
  reported as no change.
