# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.99.1] - 2026-09-26

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

- The chat model is now told to drive an Android emulator or device
  only through `qa_mobile_test`: ad-hoc steps go in `goal="..."` with
  `apply=true`, and it should never run raw adb, uiautomator or ADB
  Keyboard commands. A raw command skipped the destructive-action
  guard, the keyboard restore, the run folder and the screenshots.
  The rule appears in the `qa_mobile_test` and `qa_mobile_status`
  descriptions, the server instructions and the qa-doctor mobile
  section.
- `qa_mobile_test` now says plainly that it cannot reset app data,
  so the model tells you so instead of running `pm clear` around it.
