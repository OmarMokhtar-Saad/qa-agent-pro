# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.123.0] - 2026-10-16

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

- The run record shows what each screen-unchanged probe cost, and
  wherever a skip of the extra read was possible but did not happen,
  the reason (no baseline, other screen, cheaper to read, screen
  changed, probe error). No extra device calls.

### Fixed

- A QA keyboard on the device signed with a different key is replaced
  once (our own package only), instead of failing the upgrade at every
  step. Any other failed keyboard setup is decided once per run, and the
  step reply names the reason and the one-line adb fix.
- A run the tester stops after its last case passed, with no failed
  case and no guard stop, is recorded as pass (stopped after goal)
  instead of unverified.
