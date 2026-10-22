<div align="center">

# QA Agent Pro

**An AI QA agent that lives in your editor.**

[![Release](https://img.shields.io/github/v/release/OmarMokhtar-Saad/qa-agent-pro?label=release)](https://github.com/OmarMokhtar-Saad/qa-agent-pro/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](docs/install-details.md)
[![MCP](https://img.shields.io/badge/protocol-MCP-6E56CF)](https://modelcontextprotocol.io)
[![Platforms](https://img.shields.io/badge/macOS%20%7C%20Linux%20%7C%20Windows%20%7C%20WSL-supported-informational)](#install)
[![License](https://img.shields.io/badge/license-All%20rights%20reserved-lightgrey)](LICENSE)

</div>

QA Agent Pro is an [MCP](https://modelcontextprotocol.io) server for **Cursor**,
**Claude Code** and **Claude Desktop**. It turns a feature description, a Jira
ticket, a web page, a Swagger/OpenAPI link or live mobile screens into a
professional test-case suite, as Excel / CSV / TestRail for manual teams and
Gherkin / Playwright skeletons for automation.

There is no web UI: your AI editor is the interface. There is **no API key to
buy and nothing to log in to**. The test cases are written by the model you are
already chatting with, on the plan and schema this server hands it.

## Install

Run **one** command. It installs the server, its private virtualenv and Python
itself if needed, then registers the server with every editor it finds. Nothing
is written outside your home folder, and a native install needs no administrator
rights.

**macOS, Linux, WSL** in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
```

**Windows** in PowerShell (native, no WSL):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.ps1 | iex"
```

Then **restart your editor** and ask it:

> run qa-doctor

> generate test cases for our new login page

There is no third step: no API key, no login, nothing to paste into `.env`.
Re-running the installer updates an existing install in place and never touches
your `.env`, generated suites or corpus.

This app sends anonymous usage analytics (no test content, no file paths). Set
`DO_NOT_TRACK=1` to turn it off. Details: [Telemetry](docs/telemetry.md).

## Supported editors

| Editor | Registered by the installer |
|---|---|
| Cursor | yes |
| Claude Code | yes |
| Claude Desktop | yes |

Other MCP clients can launch `start.sh` (`start.cmd` on Windows) over stdio. See
[Connect your editor](docs/connect-editor.md).

## What goes in, what comes out

| Input | Output |
|---|---|
| Feature description in chat | Structured test suite with a saved `suite_id` |
| Jira ticket URL | Suite grounded in the ticket and its siblings |
| Web page URL | Suite based on the page content |
| Swagger / OpenAPI link | API test cases |
| Phone or emulator screens | Suite grounded in the real screens |
| A saved suite | `csv`, `xlsx`, `testrail`, `gherkin` or `playwright` export |

## Tools

| Tool | What it does |
|---|---|
| **Generate** | |
| `qa_configure_jira` | Deprecated: Jira needs no configuration |
| `qa_generate_test_cases` | Feature text, Jira URL, web page or Swagger/OpenAPI link to a test suite |
| `qa_get_category_job` | Hand the model its next category *(called by the model, not by you)* |
| `qa_prep_status` | Report preparation progress to the model *(called by the model, not by you)* |
| `qa_prepare_test_cases` | Prepare the plan and schema the model fills in before writing cases |
| `qa_stage_jira` | Stage Jira ticket context for the model *(called by the model, not by you)* |
| `qa_submit_category` | Receive one category of cases *(called by the model, not by you)* |
| `qa_submit_suite` | Receive the finished suite and persist it *(called by the model, not by you)* |
| **Export** | |
| `qa_export_suite` | Export a suite as csv, xlsx, testrail, gherkin or playwright |
| `qa_search_corpus` | Search past generated suites |
| **Mobile** | |
| `qa_app_info` | Describe the installed app under test |
| `qa_capture_screens` | Capture phone or emulator screens for grounded generation |
| `qa_list_devices` | List connected Android/iOS devices, emulators and simulators |
| `qa_mirror_hold` | Keep the device mirror open between steps |
| `qa_mobile_flows` | List saved app flows |
| `qa_mobile_knowledge` | Show what was learned about an app |
| `qa_mobile_notes` | Read and add notes on an app |
| `qa_mobile_status` | Show the state of a mobile run |
| `qa_mobile_stop` | Stop a running mobile test |
| `qa_mobile_test` | Run test cases on a device or emulator |
| `qa_network_watch` | Watch the app's network traffic during a run |
| `qa_setup_capture` | Set up HTTPS capture for the emulator lane |
| `qa_submit_mobile_step` | Receive the next step in a mobile run *(called by the model, not by you)* |
| **Diagnose** | |
| `qa-doctor` | Verify this machine and update to the newest release |
| `qa_host_check` | Check what the editor host supports |
| `qa_machine_report` | Report machine and tooling details |
| `qa_selfcheck` | Run the built-in self-check |
| `qa_update_app` | Check for and install the newest release |

### Example prompts

> Generate test cases for our new login page with email + password fields

> Generate test cases from https://yourcompany.atlassian.net/browse/SHOP-123

> Generate API test cases from https://api.example.com/v3/api-docs

> Generate test cases from the screens on my connected Android device, then
> export the suite to xlsx

## Documentation

| Topic | Page |
|---|---|
| Install details and requirements | [docs/install-details.md](docs/install-details.md) |
| Connect your editor | [docs/connect-editor.md](docs/connect-editor.md) |
| Connect Jira | [docs/jira.md](docs/jira.md) |
| Windows and WSL | [docs/windows.md](docs/windows.md) |
| Mobile testing | [docs/mobile.md](docs/mobile.md) |
| Configuration and environment variables | [docs/configuration.md](docs/configuration.md) |
| Updates, versioning and integrity | [docs/updates.md](docs/updates.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Telemetry and privacy | [docs/telemetry.md](docs/telemetry.md) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) |
| Contributing, security | [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) |

Still stuck? `run qa-doctor` in your editor reports the version, mobile tooling,
connected devices and the exact install command for anything missing on your OS.

## Uninstall

Remove the install folder (`~/qa-agent-pro` by default) and delete the
`qa-agent-pro` entry from your editor's MCP configuration. Nothing else is
written outside that folder.

## License

Copyright (c) 2026 Omar Mokhtar Saad. All rights reserved. The source is visible
for transparency. Use, redistribution or modification requires the author's
written permission. See [LICENSE](LICENSE).
