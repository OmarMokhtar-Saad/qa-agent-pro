# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.100.3] - 2026-09-28

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

- Mobile runs: a charter value the run does not know (for example
  depth: xyz) now refuses the run by name, listing the accepted
  values, instead of quietly using the default. happy_path is
  accepted as another name for happy.
- Mobile runs: a finished run's reply opens with a short verdict block
  (verdict, whether the requested reset landed, typed-field count) to
  relay as written, and the submit reply and report state how many
  fields were typed.
- Mobile runs: fewer round trips when the device is busy or a step
  runs out of time; actions not yet run when time runs out are kept
  for the next reply on both explore and scripted runs, with typed
  secret text never stored.
- Mobile runs: confirming a destructive action after resuming a
  paused explore run now works, and a cancelled device command no
  longer leaves an adb process running.
