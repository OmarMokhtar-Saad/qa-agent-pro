# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.114.0] - 2026-10-15

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

- Optional popup checks (`tap_text_if_present`) return as soon as the
  screen is settled instead of polling for the whole wait; text found on
  several controls picks the clickable one in the top dialog, or exact
  text, and otherwise skips with a note instead of stopping the script.
- Polled waits no longer add a pause after a screen read that was already
  slow.
- App Tester stops at the app's row when the package is known, no longer
  scrolls up first, scrolls back exactly to the row it found, and waits
  for the app page by watching the screen instead of a fixed pause.
- A learned flow longer than one script is saved whole as ordered parts
  (name-p1, -p2, ...) to replay in order, instead of being cut at 10
  steps; past 4 parts nothing is saved and the reason is recorded.
- Learning saved before 1.113.0 is used again: it is moved to the current
  screen id when exactly one current screen matches, as that app is
  learned; old rows that match several screens go stale 30 days after
  that app's first move, and rows no screen matches or of other apps
  are left alone.
- The report says "is gone" for absent-text checks, labels the header time
  "Elapsed" and step times "on device", and names fills by their label or
  id.
- The drive tool tells the model to report only tool-proven results, never
  mark an unexercised check passed, ask before skipping a goal step, and
  prove a destination with present text or a screen check.

### Fixed

- Typing into a field the app submits by itself (such as an OTP) no longer
  stops the script or shows as "refused": the step is ok with a "screen
  advanced" note and the following waits run.
- A `fill` sent with `field` but no `text` gets an error that says exactly
  what to send.
- Learning no longer picks up screens of other apps such as App Tester,
  and skips runs whose app is unknown or matches no screen seen; a version
  read of "null" counts as unknown.
- A run's saved learn state matches the knowledge store, a fill known only
  by its label stays in saved flows as a parameter named after the label
  (no value stored; credential-like labels are not saved), and mistakes
  with no target get a plain note.
