# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.110.0] - 2026-10-14

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

- A mobile task stated in the first message is started directly, without
  the start menu; the install source is inferred from the app text (App
  Tester, an .apk path, a download link or the Play Store) and asked only
  when unclear.
- A connected physical phone is adopted for the run, and an app name
  that was already resolved to a package is remembered.
- Faster replay: screens are settled with a cheap focus probe and one full
  read instead of repeated full reads.
- Learning: a successful run now saves its steps as a reusable flow for
  the same app; popups seen in 3 runs are dismissed automatically.

### Fixed

- App Tester: the app list is scrolled to find the requested app.
- A value is no longer typed into the wrong field when the field label
  floats above the input (for example an ID typed into the password box).
- An exploration run is no longer failed by early steps that only warned;
  its closing pass decides, and an explicit fail still wins.
- A finished run releases its device lease, so it is no longer seen as a
  live run that stopped beating.
- A one-time code that the app submits by itself no longer fails the step.
- Typed values no longer appear in learned mistake notes.
