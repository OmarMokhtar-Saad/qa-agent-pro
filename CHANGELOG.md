# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.122.0] - 2026-10-16

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

- The QA keyboard is now 1.1.0. Devices on 1.0.0 are upgraded in place
  and fast screen reading turns on.
- When a full screen read is slow, a step lets the screen-unchanged
  check finish before the next tap, so an unchanged screen is not read
  again (probe_skip). On fast reads the step does not wait.
- Answering a system dialog during settle reuses the screen read settle
  just took when nothing was tapped since.
- A wait for the screen to change keeps using the cheap frame check
  for its whole wait, so it reads the full screen only when the frame
  moved, instead of on every poll after the first unclear one.
- Each read in the run record names the code that asked for it.

### Fixed

- An App Tester start with no app name takes the app name the goal
  quotes in the same sentence as App Tester or install wording; if
  there is no single such name, the reply asks to re-call with
  app=<name>.
