# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.103.3] - 2026-10-01

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

- A code split by dots, commas or slashes after a password word
  ("password 1.2.3.4") is now refused in an app note, as it already
  was after pin, otp, passcode and cvv.
- Only "pin" can be read as a screen measurement or a date, so
  "otp 123 456 pt" and "cvv 12/25" are refused, and digits after the
  unit ("pin 100 200 px 4321") are refused too.
- A date or a thousands number after "pin" ("pin 12/31 on the map",
  "pin 1,000 px") and a list after "token" ("token 1, 2, 3, 4
  appear") no longer block saving an app note.
