# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.112.0] - 2026-10-15

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

- `source=app_tester` now installs the app itself: it finds the app in
  App Tester, taps its Download or Update button, finishes the system
  installer and continues the run once the new version is installed.
  The newest release is taken without a second question.

### Fixed

- App Tester no longer opens the wrong app. It goes back to the app list
  when it reopens on another app's page, and only list rows count: every
  word of the name you give must match the row's name or package id.
  Release notes are never read as apps, and several matching apps are
  asked about.
- App Tester's one-time setup for AAB releases (Get started) now stops
  the install and asks you to finish it on the phone.
