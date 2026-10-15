# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.118.0] - 2026-10-15

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

- A replayed step no longer pauses for its learned wait: it waits only
  until its target is on screen, with the learned time as the limit,
  and starts at once when the target is already there. A wait that
  ran out is not learned back, so waits no longer grow run by run;
  waits learned by older versions are relearned automatically.
- The step timing line shows the time spent in learned waits.
- A learned flow replays in one batch, checking each step's screen on
  the screen read it already has and reading again only when it does
  not match; an unmatched screen hands back to the model.
- Faster typing: confirmed fill, clear and type steps in a row are not
  read back between fields, the QA keyboard stays selected for the
  whole run (your keyboard comes back when the run ends, is stopped,
  fails or hands over to you), and switching keyboards no longer
  forces a new screen read.

### Fixed

- A script rejected for its format returns the reason at once instead
  of reading the screen and replaying flows first.
