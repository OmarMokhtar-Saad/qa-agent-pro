# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.100.4] - 2026-09-28

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

- Device listing and screenshots use the same adb as mobile runs, so
  qa_list_devices finds a device even when adb is not on the editor's
  PATH.
- Mobile runs: a status reply for a run that is still going no longer
  says to stop, and a run whose time extension is used up says so.
- Mobile runs: a script with a single action gets a hint to batch
  several actions per reply, saving round trips.
- Mobile runs: a goal that asks to reset the app, sent without a
  destructive setting, is told which setting allows it.
- Mobile runs: a package id that is not installed now suggests the
  closest installed package ids.
- Mobile runs: a script may wait for on-screen text for up to 40
  seconds in total (was 25), so two ordinary waits are accepted.
- Mobile runs: the app resolved for a goal is remembered for up to a
  week, so rerunning the same goal skips finding the app again; the
  device is still checked before anything runs.
