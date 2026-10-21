# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.148.0] - 2026-10-21

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

- The launcher and the server each write a small wire log under
  `data/logs` (`wire-*.log`): one line per MCP message with direction,
  method, id and size, never the arguments. A request with no reply
  after 110 s is logged as "no reply for id ...", so a lost call can
  be located. A stall of the server's event loop over 2 s is logged as
  "event loop stalled", so a delayed call is told from a lost one.
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
- When a capture setup or teardown (another tool call in the same
  server) holds the device, `qa_mobile_status` and the refusal say so:
  it gives the device back when that call ends, so call
  `qa_mobile_test` again then.
- The "another run is using the device" text says how old the hold
  is and what ends it: the running setup call finishing, that server
  exiting, or quitting the other editor.
- The tool-call log marks a cancelled call "error cancelled" (it was
  logged "ok"), and a cancelled call no longer keeps its dispatch and
  in-flight slot, so later calls are not refused as busy.
- The mobile HTML report shows one screenshot at the end of each goal
  instead of a picture per step, and one video of the whole run.
- The whole run is recorded in segments of under three minutes that
  are joined into that one video, so a run longer than the device's
  three-minute recording limit is no longer cut short.
- Nothing is recorded while a step types a password or other
  credential, so the run video has a gap there.
- A device recorder that ignores its stop is now killed on the device,
  not left recording the steps that follow.
- After the server scrolls or swipes, the screen read waits for the
  device's next event and serves a tree that did not change (end of a
  list, a short scroll) as unchanged, instead of refusing it as stale.
- Deleting a recording segment on the device is retried once, and a
  delete that still fails is logged.
- A step clip recorded again under the same name is joined into the
  run video again, instead of the old video being reused.
- A failed kill of a stuck device recorder is now logged.
- A wait that finds the screen gone is refused only when three reads of
  the same screen, at least 5 s apart, were unchanged; the message says
  how long it was unchanged.
- A setup retry that changes the locale, reset, launch, permissions or
  capture flag drops the earlier setup and starts fresh, and says so;
  a retry that only changes the goal still adopts it.
- Step timing no longer reuses a step id across steps.
- Speed: waits that polled on a fixed sleep now wake on the screen
  event clock, so a step ends when the screen changes, not on a timer.
- Speed: a tap into another app is no longer reported as a stale
  accessibility read, and the accessibility clock refreshes its
  generation and caps its skips.
- Speed: the app launch wait wakes on the first screen event, the
  window-focus probe is cached per step (never without an event
  clock), and the picture taken when a run stops is saved in the
  background. Any later device call and the report wait for it.
- Each step logs when the server received the call and when it
  replied, and how much server time fell outside the step timer.
- The App Tester install waits out any installer screen that keeps
  changing (for example staging) and stops only when it stays
  unchanged; the stop message names the app being installed.
- After an install the server taps App Tester's Open when the run
  wants the app open, and tells the host what it saw (row found,
  version), so the host does not scroll the App Tester list again.
- The server reads the Firebase app id from an installed app's APK
  and from its App Tester page, so the next install opens the page
  by link with no list scan.
- App identity: a run that starts from the installer, launcher or App
  Tester is re-pinned to the real app under test, and the run is filed
  (manifest, title, version, log slice) under the app last in the
  foreground, not the installer. The setup template names the real
  package.
- Remembered apps are one row per app (package id, label alias). With
  several, the newest within its time limit is used and the reply says
  so; with none or an ambiguous label you get a pick-list.
- `qa_mobile_test` schema help now explains `launch`, `app` and
  `grant_permissions`; `app` defaults to the newest remembered app and
  the reply echoes it.
- Saved flows are keyed on the resolved target, and the lookup also
  scans packages visited earlier in the run, so an install and consent
  flow replays.
- Replay no longer stops at every secret or anchor wait: it chains
  across anchors while the screen key matches the recorded one, and
  stops only at an OTP or a divergence, listing options for the host.
  A paused replay resumes at its step in the host lane.
- The first packet lists the input names (secrets included) that a
  fitting flow still needs. Secrets stay in memory for the run only.
- Condition waits use a learned ceiling, max(2 s, 3 x p95) per app,
  screen and action, from at least 5 samples. It only tightens the
  declared max_s, never raises it. A wait over 2 x p95 is flagged as
  slower than usual with options for the host.
- Flows match on the app and the first screen key, so a reworded goal
  is no longer a new flow and the goal gate is gone. With several
  fitting flows the best-ranked one replays and the others are listed
  in the first packet. The flow's goal is shown beside its name in the
  replay note and in that other-flows list.
- A wait that times out at a learned limit says "learned limit X s,
  declared Y s".
- Release tooling: the wiring-mutants run stops only its own slot
  workers, the test gate streams per-test progress and records its run,
  and the gate and notes checks work in a scratch worktree without a
  sibling dist clone.
- A release check on a source branch first syncs with origin/main, so it
  no longer fails on a stale local main; the mypy baseline records the
  environment it was made in, and the release check refuses lost or
  reconstructed review files.
- A goal's ID-shaped numbers and the values after password, PIN, OTP,
  ID or username words are hidden when the run is created, in the
  manifest, charter and cases, even if the run never types them.
- The host's drive option after an install stop names the real app
  package, and the App Tester APK read prefers the google_app_id
  string over the first id-shaped value.
- A learned wait limit now matches the wait's own target, so it takes
  effect; it still only tightens the declared limit.
- Learned knowledge and saved flows now follow one scope rule: system
  apps (Play Store, browsers, Google services, settings) and the
  installer, App Tester and launcher surfaces never get a knowledge
  store or flows, and never auto-replay a flow; the run goes on with
  knowledge off and its start reply says so. Stores that already exist
  for such packages are dropped once.
- Learning now runs when a run closes (done, stop, idle, server died),
  exactly once. An open run is never learned or marked finished. The
  status and the report show Knowledge: learned, pending (run still
  open) or skipped with the reason; starting the next run only learns
  closed runs that were missed.
- A long tool call now sends a 'still working: <tool> N s' ping every
  20 s, so a quiet call is no longer cut off by the host's timeout.
- A call waiting on a setup that is still running answers after 90 s
  with when it started, its age and the options. A call on a busy device
  names the holder run, the running tool, its idle time, the auto-release
  time and the next calls (qa_mobile_stop target=, qa_mobile_test
  run_id=). qa_mobile_stop answers within 60 s and never waits on the
  lease; the stop itself goes on in the background. A pending stop is
  headed 'Stop pending', a stop without a target is never capped while
  it asks which run, and a failure after the cap is logged. The busy
  answer also says when the holder run started.
- Any tool call that carried an argument the tool does not take now
  names it in the reply ('ignored argument(s) ...: query').
- A run that ends with turns still open now marks them failed (a wait
  timed out, the app was left, a system dialog) or abandoned, never
  done. Media recorded for a case that never landed a step is kept as an
  abandoned case instead of being dropped.
- A run nobody drives for 300 s closes itself as idle_timeout, and a run
  whose server died is closed on the next start; the report says why.
- screen_changed after only waits is judged by the wait's own result,
  and the reply says it is weak and to use assert text_absent.
- Programs the server starts (adb, the screen recorder, ffmpeg) no
  longer share its input with the editor, so they cannot swallow tool
  calls any more. A tool call that gets no answer within 110 s is now
  answered with an error instead of the editor waiting out its own
  timeout and retrying blind. When the call asked for progress and got
  none, the error says "lost, not slow" and to send it again once;
  otherwise it says to check the current state before resending once.
  Progress sent for a call keeps it from being declared lost, and a
  late real answer to a call already answered this way is dropped.
- A run closed by the idle timer counts its committed turns apart from
  low-level actions, and says where it stopped and how to resume it.
- A run is closed as server_died only when the server that owned it is
  really gone; a run whose server is still alive is reported as
  orphaned. Runs of dead servers are closed when the next server starts,
  not when the next run is created.
- `assert screen_changed` after a tap and a wait passes when that wait
  saw the screen change or the screen's content changed, even if the
  screen id stayed the same (for example, a download that started).
- A wait no longer ends a case as "abandoned (another app)" when an
  installer's Open button starts the app under test.
- Learning now covers each app a run really tested (ranked by its own
  steps), runs one learn job per run, and the scope purge only drops
  system-app stores and is logged once.
- A step result on a screen showing a progress bar or percentage says
  so and to wait for the finished state.
- If a goal needs a login, the tool text tells the host to ask the user
  for the ID, password or OTP and pass them as tester_inputs, never in
  the goal.
- A version-drift restart waits while a mobile run is live (at most
  30 min). qa-doctor rows that need no action are marked with a neutral
  square instead of a warning sign, and the host is told to report only
  blockers.
- A wait's screen re-read while the screen is held is capped at 0.5 s,
  so a stuck event clock cannot hide the button it waits for, and text
  matching in waits respects word boundaries.

### Known

- Reading the app id from an App Tester detail page is unproven (no
  real page dump had an id); only the APK read is proven. The Open
  wait after an install re-reads the screen every 0.25 s (up to 6 s),
  not on a screen event.
- A learned wait limit only ever shortens a step's declared limit;
  it never waits longer than the flow declares.
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
  the flow's goal does not match.
- After a tap the screen is still read afresh twice in some cases
  (re-read, then auto-settle); this costs about 0.2 s and is kept so
  a stale screen is never reused.
- The final run packet now carries a secret scan of the report, logs and
  evidence text (clean or file:line; PNG/video and the run's own
  manifest/case records are skipped, and secrets under 6 characters are
  counted as not checked) and a claim-vs-evidence table; script fields
  sent as tool arguments are moved into the script; the doctor shows an
  Update trust row (releases stay unsigned by design).
- The case json keeps how a turn ended (ended_as) next to its status; a
  reply says when no assert was replayed. The first reply is smaller:
  the mobile brief is an argument list (18.2k to 13.1k chars, 28%) and
  the vocabulary notes shrink 7.7k to 4.9k (36%).
