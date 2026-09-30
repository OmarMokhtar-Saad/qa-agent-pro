# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.102.0] - 2026-09-30

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

- Mobile testing can now remember things about one app. Save a note
  with the `note` parameter of qa_mobile_test or qa_submit_mobile_step:
  a `wait` note waits for a named text or element before acting on a
  matching element; an `avoid` note refuses an action and hands the
  decision back to the chat.
- New qa_mobile_notes tool lists an app's saved notes and retires one.
  Notes stay on this machine, one file per app, and are never uploaded.
- A note that contains a value typed into the run, a long number or a
  credential is refused by name. A note can only add a wait or a
  refusal: the destructive-action guard still runs after it.
