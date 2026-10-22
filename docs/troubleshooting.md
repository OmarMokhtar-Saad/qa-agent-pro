# Troubleshooting

| What you see | What it means | Fix |
|---|---|---|
| `sudo: apt-get: command not found` | `apt-get` is Debian/Ubuntu; you are on macOS. | Use `brew`, or let the installer provision Python. |
| `sh: #: No such file or directory` then `curl: (56)` | Interactive `zsh` does not enable comments, so a trailing `# ...` became arguments to `sh`. | Re-run the command with the comment removed. |
| `<...>: no such file or directory` from `git clone` | You pasted a placeholder such as `<REPO_URL>` literally; `zsh` read `<` as a redirect. | Use the real URL, or the one-liner in the README, which needs no clone. |
| `ENOENT ... /qa-agent-pro/start.sh` in your editor's MCP log | The server is registered but the install is missing or incomplete. | Re-run the installer; it repairs the folder in place. |
| `... already exists` | An install is already there. Updates are automatic. | Nothing to do. Re-running the installer updates in place; on Windows `$env:QA_FORCE=1` reinstalls from scratch. |
| `'sh' is not recognized ...` | You ran a macOS/Linux command in CMD or PowerShell. | Use the Windows one-liner in the README. |
| `Python was not found; run without arguments to install from the Microsoft Store` | Windows' App Execution Alias, not a real Python. | Nothing to do: the installer fetches Python via `uv`. |
| `'uv' is not recognized ...` right after `uv` installed fine | `uv`'s installer does not update the PATH of an open window. | Ignore it; the installer looks for `uv` directly. |
| `The requested operation requires elevation` | That is `wsl --install`, the one command here that needs admin. | You do not need WSL: use the native Windows path. |

Still stuck? `run qa-doctor` in your editor reports the version, the mobile
tooling, connected devices and the exact install command for anything missing on
your OS. Windows specifics: [windows.md](windows.md).
