# Mobile testing

Optional: test-case generation, Excel export and Jira reading all work without
it. `adb` is only needed to list Android devices and capture their screens.

## Installing adb


macOS:

```bash
brew install --cask android-platform-tools
```

Ubuntu / Debian / WSL:

```bash
sudo apt-get install -y android-tools-adb
```

Windows — `--scope user` installs it for you alone and needs no admin:

```powershell
winget install --id Google.PlatformTools -e --scope user
```


## Running test cases on an Android emulator

**HTTPS capture is off by default; the lane itself is not.** The emulator lane
runs your suite against a real Android app: it plans each case from the screen,
replays it, asks you when a screen needs a credential, and ends with a
self-contained HTML report that opens in your browser.

The mobile tools (`qa_mobile_test`, `qa_submit_mobile_step`, `qa_mobile_status`
and friends) are always registered; say `run mobile test` to start. To capture
the app's HTTPS traffic too (a proxy download and a device certificate), add
this to `.env` and restart your editor:

```
QA_MOBILE_HTTPS_CAPTURE_ENABLED=true
```

What to expect the FIRST time:

- If Android Studio is already installed, it uses that SDK and downloads nothing
  but a small (~300KB) QA keyboard.
- If not, it downloads a JRE, the command-line tools and one system image — up
  to ~2.2GB, several minutes — into `~/.qa-agents/mobile/`. It checks free disk
  first and refuses rather than filling the drive.
- Every install, download and launch also needs `apply=true` on the call, so
  nothing large happens because you asked a question.

Two things worth knowing:

- **Credentials are never stored.** When a screen needs one you are asked in
  chat, the value is typed straight onto the device, and it is masked in the
  report, the run files and the audit log.
- **A run resumes in any chat.** Keep the run id; a second chat that resumes
  takes over and the first is told to stop.

> [!WARNING]
> **Windows is untested for this lane.** The code has Windows paths and flags,
> but no Windows machine has run it end to end. On macOS it is tested against a
> real emulator.

## Windows

`adb` on Windows: use the `winget` line above (`--scope user` needs no
Administrator rights; drop it only for a machine-wide install). `qa-doctor`
lists the optional tools and prints the install command for whatever is
missing on your OS. Inside WSL, `adb` must be reachable from inside WSL (see
[Windows and WSL](windows.md)).

**Windows is untested** for the emulator lane. The code has Windows paths and
flags, but no Windows machine has run it end to end. On macOS it is tested
against a real emulator.
