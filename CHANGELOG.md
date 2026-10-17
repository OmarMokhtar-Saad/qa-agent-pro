# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.129.0] - 2026-10-17

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

- Before a mobile run, the App Tester app is skipped when the app is
  installed and its build is the newest one listed, or no listing is
  known yet.
- A saved flow that was replayed automatically and has no assert of its
  own no longer counts as a pass.
- Long numbers you type, such as national IDs, are masked in the report,
  case titles and findings; passwords and codes are redacted when
  results are written.
- A wait that missed before 90% of its time limit had passed is no
  longer learned as a timeout.
- A recorded route with one step read from a degraded screen key is now
  saved, cut before that step or with that key blanked, instead of
  being rejected.
- Once a run has timed its screen reads, the screen-change probe is
  skipped when a full screen read is cheaper than the probe.
- The screen-read timing used to size waits no longer counts
  double-check reads or the first, warm-up read.
- A wait-until-the-screen-is-quiet step is built in but stays off
  until the next keyboard app release (qa-ime 1.2.1).
