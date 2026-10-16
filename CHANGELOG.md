# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.125.0] - 2026-10-16

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

- The skip of the screen-unchanged probe now compares against the
  speed of the read path the next read will use (fast or fallback),
  so one slow fallback read no longer keeps the probe running.
- A learned flow is split at a permission dialog: the permission
  answers become their own short flow. A new flow that repeats one
  already saved is skipped; one that contains an older flow replaces it.

### Fixed

- A wait no longer polls to its limit when a system dialog sits in
  front of an unchanged screen; it stops at once.
- A flow that taps a permission decline is not learned.
- (Needs the QA keyboard 1.2.0, shipped separately.) Screen reads
  include a permission or ANR dialog shown over the app, so the fast
  read path no longer falls back to the slow one there.
