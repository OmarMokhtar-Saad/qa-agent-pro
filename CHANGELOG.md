# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.143.0] - 2026-10-20

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
- Desktop app: an in-app Update button installs the latest release, and
  Force update installs it again even when you are already on it.
- Each desktop release now ships a `qa-agents-desktop-<version>.dmg.sha256`
  file next to the DMG (`<hex>  <name>`), so you can check the download.

### Changed

- A mobile setup call the chat cancels (device being picked, booted
  or the app installed) gives the device back at once. Its clean-up
  (accessibility and keyboard restore) finishes in the background, so
  the next `qa_mobile_test` in the same chat is not refused with
  "another run is using the device".
- A setup hold left by this same server after its setup call ended
  (a stale hold) is given back on the next `qa_mobile_test` instead
  of blocking until the editor restarts.
- `qa_mobile_status` lists a device this server is setting up (no run
  id yet): the serial, the owner, how old the hold is, and whether
  its setup call is still running or has ended.
- The "another run is using the device" text says how old the hold
  is and what ends it: the running setup call finishing, that server
  exiting, or quitting the other editor.
- The tool-call log marks a cancelled call "error cancelled" (it was
  logged "ok"), and a cancelled call no longer keeps its dispatch and
  in-flight slot, so later calls are not refused as busy.

### Known

- If the server process is killed, the QA keyboard stays selected
  until the next run's clean-up gives the tester's keyboard back.
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
  install. A route that only taps through a permission pop-up is no
  longer saved; the pop-up is answered by the dialog handler on replay.
- On a device without the QA keyboard, the first run's App Tester
  scan still uses the slower uiautomator dump.
- A custom loading animation or splash image that is not marked as
  progress, spinner, Lottie, shimmer or skeleton can still end a wait
  for text early after 10 s.
- A screen recording can still come back empty when the device has
  not finished writing it; the evidence note now says so.
- A screen-list line longer than 400 characters is shortened.
- An element off the left or right edge of the screen is counted in
  the "more below" line even though scrolling down does not reach it.
- Old saved routes that do nothing are cleaned up only when a new
  route is saved.
- The fallback keyboard cannot read the field back, so its fill proof
  says "?/N chars" instead of a count.
- The words that turn auto-replay off (no saved flow, from scratch)
  are English only. Matching a case title to a flow goal compares word
  stems, so a short goal matches a longer title (login also matches
  login with a wrong password), and a title in another language than
  the flow's goal does not match. A flow learned while exploring has
  a long goal and rarely replays on its own.
