# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.127.0] - 2026-10-16

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

- qa_mobile_test now double-checks a fast screen read. When the read
  right after a step is identical to the one before it, the run confirms
  it with a slow, reliable read; if the two disagree, fast reading is
  switched off for the rest of the run instead of acting on old content.
  Set QA_DUMP_SOURCE=uiautomator to always use the slow reader.
- A step whose screen tree did not change but whose frame moved, or
  whose re-read failed, is now reported as "unverified" instead of
  "no change", so a step that may have worked is no longer treated
  as a dead tap; its report asks you to look at the screenshot.
- A flow-parameter mismatch error now lists the expected keys. A typed
  field that reads empty but whose screen then advanced is noted as
  "confirmed by screen advance".
