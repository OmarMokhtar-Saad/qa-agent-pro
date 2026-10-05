# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.105.0] - 2026-10-05

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

- New mobile tools: qa_update_app updates an app in one call (resolve,
  current version, fetch, install, launch, verify the new version) and
  ends with a verdict; qa_app_info shows the installed versionName and
  versionCode; qa_mobile_stop stops a run, and a stopped run takes no
  more steps.

### Fixed

- Mobile runs never guess an app, package, device, build flavour or
  release: one exact match is used and named, otherwise you are asked.
- The device lock is released when a run ends or sits idle, so the next
  run no longer waits on a stale lock.
- Every run ends with a verdict and a per-step summary instead of
  'unverified', and refused cases keep their test-case id and title.
- Mobile scripts accept the step shapes models actually write (launch
  target, scroll direction, tap by resource id, long waits) instead of
  refusing them.
- App Tester installs drive the App Tester app on the device instead of
  opening an empty Play Store listing.
- Install, permission and App Tester dialogs are handled by the run,
  with no blind taps; install failures report the real cause.
- Faster runs: fewer duplicate screen dumps, and per-step timings
  (duration, dumps, waits) in the run record and report.
