"""MCP Prompt body: mobile run (leaf of tools/guidance.py)."""

from __future__ import annotations

_P_MOBILE = """\
Run test cases, or explore freely, on an Android emulator.

Nothing runs until the tester has agreed to it: every step that installs an
app, downloads an SDK or launches anything needs `apply=true`, and you send
that only after the tester has said go, on a later turn.

1. Call `qa_mobile_test` with no arguments. It answers with whatever the
   machine needs next -- a setup guide, an install menu, a preflight
   list with a fix per failed check, or the start menu.
2. A markdown menu means your client could not show a dialog. Put it to the
   TESTER, and re-call with the identifier THEIR option prints -- a backticked
   `key` in `source`, or an adb `serial` for a device. THIS lane never accepts
   a position, whatever your question UI shows: it may relabel the options
   a/b/c/d and it may reorder them, so a tester who answered "the fourth one"
   would start a different run. (Other tools DO take a number where their own
   reply asks for one in so many words -- read the reply, do not generalise
   from this rule.) Never choose for them.
3. Once a run starts you get ONE packet: a case (or an exploratory goal), the
   pruned screen, and the exact action vocabulary you may use. The budget is
   TURNS, not actions, so plan the WHOLE case -- or a whole conversational
   round -- as ONE script and send it straight back with
   `qa_submit_mobile_step`. A script may carry dozens of actions and the
   packet states its own ceiling; four scripts that each do one step of a
   round cost four times what one script costs. Do not echo the screen, and
   do not ask for a packet you already hold.
4. If a step comes back needing a credential, ask the TESTER for that ONE field
   in chat and pass the value in `tester_input` with `tester_input_field` set
   to the field name. It is typed into the app and stored nowhere -- not in the
   report, the checkpoint or the audit log. Never invent one, never reuse one
   from an earlier run, and never repeat it back in your own message.
5. If a step stops in front of something irreversible -- a confirm, a delete, a
   payment -- nothing was tapped. Say so, ask the tester, and resubmit the same
   script only if they agree.
6. Every case is checkpointed as it finishes. To continue in a NEW chat, call
   `qa_mobile_test` with the `run_id` and no session token: that takes the run
   over, and the other chat is told at its next call. `qa_mobile_status` reads
   the whole run back from disk and changes nothing.
7. A finished run's reply starts with a short verdict block (verdict, each
   requested step, the typed-field tally). Relay it to the tester word for
   word -- never upgrade, soften or summarise it into a plainer claim.

Never start, install to or drive a device yourself through a shell. This lane
owns the emulator: it spawns it detached so it outlives the call, keeps its
quickboot snapshot so a warm AVD restores instead of cold-booting, and hands
back a `booting` reply you poll with `qa_mobile_status`. A hand-rolled
`emulator -avd ...` from a tool shell dies with that shell and boots colder
and slower than the one this lane starts for you.

Device selection: if the tester already has an emulator running, pass its adb
serial (e.g. `emulator-5554`) in `serial` on your NEXT `qa_mobile_test` call
rather than letting the server guess or start a second one. Several booted
devices come back as a menu of serials -- put it to the tester and resend with
the `serial` they choose, not with its position in the list.

A reply naming "booting" is not something to retry by
calling `qa_mobile_test` again: call `qa_mobile_status` instead and keep
polling it until it reports ready, then continue with the `run_id`.

Do not start a second run of cases you have already started. If the same
cases are already an unfinished run of the same app, `qa_mobile_test` answers
with that run's id -- carry on with it by passing that `run_id`, rather than
re-sending the cases. `new_run=true` starts a fresh one and is for when the
tester asks for it.

Keep each script inside the budget the packet states: one submit replays for a
bounded time and the waits in one script have a total cap. If a reply comes
back saying the budget was reached, nothing was lost -- the actions that ran
are recorded and the screen you were given is the current one; send the rest
as a second, shorter script.

Wait for what the reply must SAY, not for a number: `wait_until_text` continues
the moment that text appears, `wait_until_gone` when a spinner or 'Loading'
leaves, `wait_until_changed` / `wait_until_idle` when the screen moves or
settles. `max_s` only bounds a condition that never comes; no wait is a fixed
sleep. Never sleep in a shell (sleep, Start-Sleep, timeout) between calls: the
phone is not watched while you do.

To check that the app REPLIED, use `assert new_text` (optionally with
`contains`), or `text_present` naming something the reply must say. Never
`screen_changed`: it only says the screen moved, which any navigation does.

The short element ids belong to the screen you were given and are renumbered
every time the server re-reads the screen. Use one for the FIRST action of a
script; for anything after it, target by `role` (send, voice, input, back,
close), by `rid`, or by `label`.

Resuming works on an UNFINISHED run. A run whose cases have all reached a
verdict is complete, and `unverified` and `blocked` are verdicts -- so resuming
it hands back the report, not more work. To attempt such a case again, start a
new run for it, and give the script an `assert`: `unverified` means nothing was
checked, and a script that checks nothing cannot pass however many times it is
run.

7. When the run finishes, this server writes a self-contained report FOLDER
   next to the run's own files and opens it: `index.html` beside a `media`
   directory holding the recordings and frames. Relay the PATH it names and
   say it is a folder: to send it on, zip the WHOLE folder and send that.
   The html on its own opens with every picture missing. A report can be
   built at any time, mid-run included, with `qa_mobile_status` and
   `report_now=true`.

A reply that says the call stopped short of its own time budget is not an error
either: no step was half-done, and calling `qa_mobile_test` again with the run
id carries on from the same place.

A step that says the emulator is still starting, or an install is still
running, is not an error: those outlive a single tool call by design. Call
`qa_mobile_status` and continue when it reports ready.
"""
