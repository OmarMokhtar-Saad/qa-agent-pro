# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.116.0] - 2026-10-15

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

- A learned flow now replays from whatever step matches the current
  screen, not only from its first step, so a flow saved on a first
  launch (onboarding included) still replays on later runs. A chain of
  replays stops after two minutes and hands back to the model.
- App Tester asks for the app name before opening anything, remembers
  each app's Firebase id and list position on disk across server
  restarts, installs from the app page it already opened instead of
  opening App Tester again, skips App Tester when the installed version
  is already the latest it just read, and stops scrolling at the first
  page with nothing new.
- Faster steps: stopping and pulling the step's screen recording no
  longer counts as a screen change, so the last screen read is reused
  for the next step; a wait that saw the screen change keeps that read
  instead of reading again; and a tap followed by a change wait skips
  its extra settle read.

### Fixed

- A first App Tester call without an app name no longer scans the
  whole list before asking for it.
