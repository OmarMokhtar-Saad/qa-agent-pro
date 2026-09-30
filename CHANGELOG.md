# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.101.0] - 2026-09-30

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

- Mobile testing no longer loops on the login screen when a flow
  hands over from one app to another: the goal and the expected app
  follow the current step, and tester credentials are kept for the
  whole run instead of being asked for again.
- The wait for the screen to change now adapts to how slow each
  device is to read, and never runs past its own deadline.
- A screenshot or screen-read timeout is now reported as a timeout
  in the result and the audit log, instead of as success.
- Steps are faster: the screen is not re-read after actions that
  cannot change it, and each step reports its time per phase.
- qa_list_devices says an emulator is booting instead of 'No devices
  detected'.
- A slow host or emulator gets one warning per run, with a
  suggestion for a lighter emulator.
- The option picker falls back to the text menu clearly when it
  times out.
