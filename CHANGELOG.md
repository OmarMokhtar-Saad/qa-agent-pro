# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.100.5] - 2026-09-28

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

- Mobile runs: starting a run with no source shows the run menu at
  once, before touching the device.
- Mobile runs: launching the app no longer waits for Android's full
  launch report; a short foreground check replaces it.
- Mobile runs: each step reply ends with a timing line showing where
  the time went (screen reads, evidence, replay).
- Mobile runs: an emulator image too heavy for this machine gets a
  note saying so before the run starts.
- Mobile runs: logging in takes about 4 calls instead of ~20; a
  package that is not installed lists the installed apps and the one
  used last on that device.
- Device listing is cached for 10 seconds for internal lookups;
  qa_list_devices and Rescan always read the devices fresh.
- qa-doctor finds adb the same way mobile runs do.
- Slow adb calls are logged with their duration.
