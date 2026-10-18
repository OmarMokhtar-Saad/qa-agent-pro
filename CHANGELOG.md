# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.136.0] - 2026-10-18

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

- After a tap the server waits for the screen's first change, then
  lets it settle (up to 10 s), instead of a fixed short wait.
- Before reporting "screen did not change" it re-reads the screen once
  more, fresh, so a stale read no longer shows up as no change. The
  "before" screen of a step is the last screen the model was shown.
  Some of this needs the next qa-ime release (1.2.2); older qa-ime
  still works.
- A run now installs the app, launches it and grants its permissions
  by default, and the permission step is kept in saved routes. Pass
  dry_run=true to only preview, or grant_permissions=false to skip
  granting.
- Arguments a tool does not know, or that were retired, are ignored
  and the reply names them in a note (names only, never values)
  instead of failing the call.
- A saved route that matches the goal is replayed by default;
  flow=none only counts when the goal itself asks for no replay. One
  best route is offered, and reworded goals share one saved route
  instead of piling up copies.
- A tap on an in-app pop-up is saved as optional, so a replay does not
  fail when the pop-up does not appear.
- Typing into a field now proves the fill: the reply names the field
  and gives an N/N chars check (also for OTP and secret fields).
- A run that ends without a verdict is shown as its own "no verdict"
  tile, not as unknown or passed, and says that done or assert is
  still due. Each case needs its own assertion; it no longer inherits
  an earlier one.
- Passwords, National IDs and other typed secrets are masked in every
  run file and in the step reply, and long numbers in any screen text
  show as ****last4.
- Preflight now records the lock state and the foreground app, answers
  a system dialog (up to 3) instead of aborting, and reports a dialog
  it cannot answer as a failed check with a fix.
- App Tester no longer reinstalls an app whose installed build is
  already the latest, and its screens wait for a change instead of
  fixed sleeps, so installs are faster.
- Runs are faster: one probe per step, the report is built only when
  needed, and the pre-run checks also run in the explore lane.
- The timing table no longer counts the same device time twice, and a
  device lease records its release time once.
- Tool replies reach the client once; clients that showed both the
  text and a structured copy no longer show every reply twice.
- An update restart now waits for a call in progress (with a time
  limit). A supervisor whose editor is gone exits on its own, and two
  updates can no longer run at the same time.
- Keyboard apps whose package name has a 'keyboard' part (such as
  Fleksy) are recognised as keyboards, and 16-character device serials
  are masked in saved goals.

### Known

- A count in the learned-mistakes table is how often the mistake was
  seen, including retries within one run, not how many runs saw it.
- A device or emulator lock file can still name its last holder after
  the lock is released; this is by design and does not block a run.
- A very large result (about 40 KB) is written to a file and the reply
  points to it; reading it back can make the next step slow to start.
- A screen that changes only in its pixels (video, canvas, maps) and
  sends no screen events still reads as unchanged.
- Notification permission is granted with pm grant only; the user-set
  flag and app-ops are not applied in this release.
- The permission step has not yet been checked on a real fresh
  install.
- The fallback keyboard cannot read the field back, so its fill proof
  says "?/N chars" instead of a count.
- Screen-change detection needs qa-ime 1.2.2 for the full fix; until
  then a fresh re-read covers most cases.
