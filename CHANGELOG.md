# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.108.0] - 2026-10-11

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

- Internal restructure: the MCP tool handlers now live in their own
  `handlers/` package and the shared tools are split along clean layer
  boundaries. Tool names, arguments and replies are unchanged.
- Swagger/OpenAPI documents above 32 million characters are refused
  before parsing, and parsing no longer blocks the server.

### Fixed

- `.env` self-heal fixes `KEY = value` lines, writes atomically, and keeps
  one backup with the `.env`'s own permissions instead of a new one every
  run.
- Closing a host task twice no longer runs it twice; local stores close
  their database on setup errors and wait on a busy database.
- Timed-out or cancelled device commands now stop their whole process
  group instead of leaving adb/xcrun children running.
- A device lock that fails to move to a new owner no longer leaves locks
  split between two owners.

### Security

- Mobile `open_url` accepts only plain URL characters, so a URL can no
  longer inject device-shell arguments or redirects.
- The Jira payload size cap can no longer be bypassed; device
  UI XML with DOCTYPE/ENTITY is refused; imported suites must stay inside
  the chosen folder and not follow symlinks.
- Spreadsheet exports neutralise more formula-injection prefixes; exports
  refuse the install folder and hidden folders; untrusted-content
  markers resist more spoofing variants.
