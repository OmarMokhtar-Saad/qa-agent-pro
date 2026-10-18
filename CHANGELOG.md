# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.134.0] - 2026-10-18

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

- Arming the screen reader behind a system dialog no longer hangs: its
  check read no longer waits on the read it was started from.
- When the screen reader answers 'no window' right after a tap, it is
  asked again for up to 400 ms (less once the run has timed its plain
  screen reads) before falling back to the slower plain read.
- After a tap, an unchanged screen is confirmed without a second, slow
  screen read when the keyboard app reports no screen events since the
  last read (qa-ime 1.2.1 or later).
- The screen-read timing shown in step notes names the kind of read it
  comes from, or 'any' when it is drawn from all reads.
- When the keyboard app is too old to report screen events or quiet
  waits, the run notes it once instead of silently doing without.
- A recorded route that reached its goal is saved up to 9 parts long
  (other routes stay at 4); leftover parts of a broken chain that never
  ran successfully are pruned.
- Automatic replay never picks a saved route that failed every one of
  2 or more tries and never reached its goal, or that reached its goal
  but replayed ok 0 times in 4 or more tries;
  routes saved before run counts existed start at zero runs, and old
  routes are pruned after every save that reached its goal.
- Only one saved route is kept per goal: when a new route repeats or
  extends an existing one, the better one stays (reached its goal,
  then longer, then replayed ok more often).
- The automatic-replay note names the steps it ran (for example
  'steps 4-9 of 9'); 'all landed' only means the whole route ran.
- When the call names the app's package, the mobile test preview lists
  its runtime permissions that are not granted and tells you to pass
  grant_permissions=true unless you are testing the permission prompt.
- A case that stops on a permission dialog names grant_permissions=true
  as the fix for the rerun.
- Before a mobile run, the App Tester app is also skipped when an
  earlier scan already matched the app name to a package that is
  installed and up to date.
- The fast screen reader's start check reads the screen again while the
  app is still loading, so a screen that changes during the check no
  longer turns the fast reader off for the whole run.
- qa_mobile_test takes launch=false: a goal or explore run starts on the
  screen the device already shows instead of opening the app first
  (default: launch). reset_app=true and scripted case runs still open
  the app.
- When a case ends blocked and the run stays open, the reply says to
  clear the blocker or call qa_mobile_stop to end the run.
- The fast screen reader's start check is remembered per device build
  and keyboard build for 7 days, so later runs skip the slow first
  screen reads (about 20 s); a failed check forgets it.
- A goal run that reached its goal saves its route even when it
  declined a permission sheet in the app; steps before the app first
  showed, and declines on system dialogs, are left out of the route.
- A saved route that kept failing is tried again after 7 days.
- Screenshots and recordings taken before a case is written are now
  linked to that case.
- Reading the App Tester build list waits for it to finish loading
  (up to 20 s) instead of reporting 0 builds.
- A one-time code that submits itself and moves to the next screen
  reads as unknown, not as not typed.
- When the server already ran the App Tester step or opened the app,
  the start reply says so, so the chat model does not do it again.
- An app named without quotes (named exactly X) is recognised.
- A goal that starts from the home screen no longer opens the app
  first when no install step ran.
- A turn that declares the app it moves to (App Tester, Play Store,
  Settings, Chrome or a package id) is no longer cut after one action
  for leaving the app.
- Explore cases still waiting on the model are closed when the run
  ends, so none stay open.
- An unknown screen is not treated as another app by the flow gate.
- Held screenshots and recordings are capped and dropped when the run
  ends; a failed case write keeps them.
- The fast screen reader's remembered start check is written safely,
  ignores corrupt or future-dated entries, and is reset when its
  screen-read rules change.
- A turn is cut for leaving the app or for a system dialog only when
  its own action moved the screen; an unchanged screen never cuts.
- A replayed route that reaches its end is no longer counted as a
  divergence; replay stops before a step that is not a dialog answer
  while a dialog is open, and the replay note lists the dialog answers
  it made.
- A goal that checks the home screen opens the app only when it
  should, and the start reply says whether the app was opened.
- App names of two or more words match even when written loosely;
  a run with no build source picks App Tester when it is the only one.
- A finished run releases its device lease when the report is sent.
- The step time table lists time not covered by any named part as
  'unitemised', and device commands are timed as 'adb'.
- Screen waits check less often while nothing changes (up to once a
  second), so they use less device time.
- When the fast screen reader is found stale, the slow screen read that
  proved it is reused, so the screen is read once instead of twice.
- A goal that checks the home screen before installing or opening the
  app gets that check done by the server first, so the app is no longer
  closed and opened again; a check after the launch says a launch follows.
- A stray goal_reached argument on a tool call is ignored, not rejected.
- A replayed route stops its optional popup steps once its next screen
  is reached, and does not chain routes that repeat the same popups.
- Missing optional taps share one calm-screen proof instead of waiting
  about a second each; any new screen event ends that proof.
- Waiting for an idle screen uses the fast reader's event clock or two
  equal screen reads instead of three screenshots.
- A slow screen read that proved the fast reader stale stays valid until
  the next action, not two seconds.
- The start check waits for the screen to go quiet before a slow screen
  read, and slow reads no longer stretch later wait deadlines.
- The step time table folds older rows and names replay time.

### Known

- A count in the learned-mistakes table is how often the mistake was
  seen, including retries within one run, not how many runs saw it.
- A device or emulator lock file can still name its last holder after
  the lock is released; this is by design and does not block a run.
- A qa_mobile_test call without apply=true only returns the preview;
  nothing runs until the call is repeated with apply=true.
- A very large result (about 40 KB) is written to a file and the reply
  points to it; reading it back can make the next step slow to start.
- A screen that changes only in its pixels (video, canvas, maps) and
  sends no screen events still reads as unchanged.
- Notification permission is granted with pm grant only; the user-set
  flag and app-ops are not applied in this release.
