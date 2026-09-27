# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.100.0] - 2026-09-27

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

- The mobile run report is redesigned: Issues, then the Journey
  (goal, turn and step, with before/after pictures and a lightbox),
  then the scripted cases, then a collapsed Diagnostics part. Each
  result is one of pass, fail, blocked or unfinished. A scripted case
  card shows each moment once, and on a phone the section links stay
  as a row you can scroll sideways. A scripted-suite page is about
  half its old size.
- The report masks credentials written into prose: a value after a
  national ID, iqama, password, passcode, PIN or OTP, and any value
  the run typed into a secret field, wherever it reappears.
- Fetching a Jira sub-task no longer also fetches its parent story
  and sibling stories by default. Turn `JIRA_FETCH_PARENT` or
  `JIRA_FETCH_SIBLING_STORIES` on to get them.

### Fixed

- `qa_mobile_test` checks that the QA keyboard really became the
  active one, and retries a bounded number of times, instead of
  trusting a command that can report success while refusing.
- App data can be cleared: `qa_mobile_test(reset_app=true)` before a
  run, or a `clear_app_data` step, which the destructive-action guard
  always stops to confirm. Both need `apply=true` and touch only the
  run's own app, never a system package.
- Answering the install menu with an installed app no longer gets
  misread as a run-menu choice; the chosen app is launched first.
- After an update, the server's instructions mention the reload even
  before qa-doctor runs, and qa-doctor still reports it.
- A repeated category-job reply within a few seconds, or a repeated
  generate call within 30 seconds, returns the first answer instead
  of running twice.
- Test-case IDs written as `tc-1`, `TC_001` and similar are accepted.
- The launcher warns when the install it last ran from has been
  deleted or moved, a sign an editor is still pointed at the old
  folder.
