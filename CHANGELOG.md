# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.121.0] - 2026-10-16

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

- A screen keeps one identity when the app changes its activity, and
  screens learned before this release are matched to their new keys
  when knowledge loads.
- The screen-unchanged check ignores the status bar and tiny pixel
  changes (clock, cursor), so unchanged steps skip their extra read.
- A step before a wait, an optional tap, or a field already on screen
  skips its settle read.
- Learned flows remember how often they replay successfully; the best
  flow per goal is offered, failing and duplicate flows are pruned, and
  the model gets a short note on each replay.
- Screen recordings are pulled at the end of the run instead of after
  each step, and the installed app version is read faster.
- When the tester has already asked for an install, the assistant is
  now told to confirm it on the first call, skipping the preview.
- The start reply carries the timing line, the static instructions
  are sent once per run, and a repeat doctor call in the same run and
  workspace reuses its report (fix=true always rechecks).
- The run record keeps step marks, a per-read ledger and the replay
  trace.

### Fixed

- tester_inputs is accepted on the start call, and a wrong argument
  returns a hint naming the accepted keys with an example.
- The doctor flags a QA keyboard on the old protocol in yellow.
