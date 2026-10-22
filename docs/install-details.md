# Installation details

## Requirements

Python **3.10 or newer**, and `curl`. You do not have to install it yourself:
if the installer finds no suitable Python it provisions a private one with
[uv](https://docs.astral.sh/uv/) under your home folder — the same on macOS,
Linux and WSL, with no package manager and no `sudo`.

## Installing Python yourself (optional)

Only if you would rather manage the interpreter. Run the line for **your** OS —
`apt-get` does not exist on macOS, and `brew` does not exist on stock Ubuntu.

macOS ([Homebrew](https://brew.sh)):

```bash
brew install python@3.12
```

Ubuntu / Debian, including inside WSL:

```bash
sudo apt-get update && sudo apt-get install -y python3.12
```

Any of the three, with no admin rights — this is what the installer does for
you automatically:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

Interactive `zsh` (the macOS default) does not treat `#` as a comment, so never
paste a trailing `# ...` note onto any of these lines — it is passed on as an
argument and the command fails.


## Where things are installed

Everything lands in `~/qa-agent-pro` (`%USERPROFILE%\qa-agent-pro` on Windows).
Override with `QA_INSTALL_DIR` (`$env:QA_INSTALL_DIR`). Dependencies go into a
private virtualenv inside that folder; no system Python is modified and nothing
is written outside `$HOME`. `QA_FORCE=1` replaces the code from scratch while
still keeping `.env`, `data/` and `corpus/`.

