# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.104.0] - 2026-10-02

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

- Faster start: the server checks its files locally and starts at once;
  the update check runs in the background after your editor connects.
  A tampered install is still healed before any of its code runs.
- Mobile waits never sleep a fixed time: a wait returns as soon as its
  condition holds, and stops at once, naming the cause, when a dialog,
  a call, another app, an error page or a dead device takes the screen.
  A wait on a saved app note that is interrupted no longer counts
  against the note.
- Faster mobile runs: animations are turned off for a run and restored
  afterwards, device checks run in parallel, a screenshot goes to the
  chat only when it is needed, and an unchanged screen is sent as a
  short fingerprint.
- Shorter tool descriptions; the longer guidance arrives with the
  first reply that needs it.
- Fill a field by its label: a mobile step can name the label a tester
  sees next to a field instead of its id. A label that matches no
  field, or more than one, is asked back and never guessed.
- Saved flows: a script that replayed cleanly can be saved for an app
  and run again with different typed text. Passwords are never stored,
  only the name of the field they go in. `qa_mobile_flows` lists,
  shows and deletes them.
- Saved routes: the way to a screen can be saved for an app. A replay
  checks every screen on the way and hands back to the chat as soon as
  the app is somewhere else.
- A quick look at the screen: `qa_capture_screens(peek=true)` returns
  the screen as text, with no screenshot and no tap.
- Emulator management: `qa_mobile_test(emulator=list|boot|create|delete)`
  lists, starts, creates and deletes Android emulators. Nothing is
  downloaded; create uses a system image you already installed. Boot,
  create and delete need `apply=true`, and delete also needs
  `confirm_destructive=true`.
- Firebase App Tester: when App Tester is on screen or was just
  installed, the reply explains how to update a build through it.
  Signing in stays your own step.
- `qa-doctor` shows its Jira lines only when Jira is in use.

### Fixed

- `emulator=list` shows every installed system image; a stray file
  among the image folders no longer ends the list early.
- An emulator name with a non-ASCII character is refused with a clear
  message, and a system-image folder with an unusual name is skipped
  and counted.
- A code on the line after "pin" in an app note is refused for every
  kind of line break (CR, vertical tab, form feed, U+2028 and others),
  not only LF and CRLF.
- A file is checked before it is installed on a device: it must end
  in `.apk`, start like an APK and be no larger than 512 MiB. A file
  that fails is refused with the reason and nothing is sent.
- The QA keyboard on the device is compared with the published build
  before it is used. A different app under the same name is replaced
  with the published build; when the device cannot be asked, the run
  goes on and the server log says so.
- Saved app notes, flows and routes are read and written through the
  app's own folder, so a folder swapped for a link while the server
  runs cannot send them elsewhere (macOS, Linux and WSL).
- When a long script stops at the time limit, a value typed at a
  password or code field is no longer kept in the saved remainder.
  The reply names the steps that were not kept and asks for them
  again.
