# Changelog

All notable changes to QA Agent Pro are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.98.0] - 2026-09-26

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

### Fixed

- Closed a path-traversal defect in server-side staging: a crafted
  stage part name could write outside the intended app-data root.
- Provenance is now recorded on every call that contributed cases to
  a suite, not only the one that finalises it. Categories submitted
  from one client and finalised from another are reported as a
  union, and the unrecognised-client warning fires when it should.
- An oversized Jira payload no longer produces a garbled composite
  refusal. The message now names the fetch tool to call and walks
  through staging the ticket part by part.
- The non-deterministic-oracle check now also flags escape-hatch
  phrasing -- "if applicable", "where present", or an "unless it is
  visible/available" clause -- which lets a step pass whatever
  happens. It previously needed a disjunctive "or" to notice.

### Security

- Release notes are gated at build time: a build whose CHANGELOG.md
  body is byte-identical to the previous release's, or to the
  generic placeholder text shipped from v1.79.9 through v1.97.0, is
  refused on `--push`.
