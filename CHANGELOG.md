# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.115.0] - 2026-10-15

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

- Learned flows are replayed by the server: when an explore turn starts
  on a saved flow's screen, the flow runs directly instead of only being
  hinted to the model, and hands back at the step where the screen
  differs (send `flow=none` to opt out). Flows keep taps by id only,
  leave out failed steps, save the part that worked, and older short
  flows that never reached the goal are dropped.
- App Tester opens the app's page by its Firebase link when the tester
  input gives the app id, remembers where each app sits in the list,
  drags slowly instead of flinging, treats the list as ended only when
  rows repeat, reads an empty screen again instead of stopping, and gives
  up after 90 s; the blind scroll back to the top is gone.
- Faster steps: a step reuses the screen it just read when nothing acted
  on the device since, a refused script takes no new screen read or
  screenshot, and a met wait-until-gone counts as the screen change the
  next wait asks for.
- The run's first timing row shows launch, foreground wait and first
  settle, so a slow start is visible.
- A pass needs a positive check (present text or a screen check); a
  run proven only by absent text is not accepted as passed, and checks
  whose read-back is unknown are counted as unconfirmed.
- The report header time is labelled "Elapsed", and the keyboard note is
  added only to keyboard-related reasons.

### Fixed

- `type` aimed at a field that is not found falls back to the only empty
  input on screen (such as an OTP box) and says so; otherwise the error
  suggests targeting the field by its label.
- Findings the run's steps do not back (such as typing with no type
  step, or a failed step) are marked unverified.
- A skipped install (app already installed) is said once in the start
  reply, which asks whether to reinstall or update.
