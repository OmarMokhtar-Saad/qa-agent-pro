"""Prep lifetime, audit, MCP, update and telemetry fields.

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class RuntimeSettings(BaseModel):
    # Hard ceiling (seconds) on a prep's TOTAL lifetime under the sliding TTL.
    # Lenient never-raise int coercion like the rest; <=0 -> the default.
    qa_prep_max_lifetime_s: int = 14400

    # Append-only audit log (LT-1 ph2). SQLite file recording key events (suite
    # generated, exported, pushed, bug reported) so multi-team deployments have a
    # trail. Never-raise; a failure degrades to no-audit, logged.
    qa_audit_log_path: str = "data/audit.db"

    # MCP server (mcp_server.py) exposing the QA agents/tools to Claude
    # Desktop / Claude Code / Cursor over stdio — the product's primary
    # surface. Off by default like every other feature gate: turning it on
    # lets an MCP client drive generation, exports, and (dry-run-defaulted)
    # device runs -- each MCP tool call is separately audited.
    qa_mcp_enabled: bool = False

    # Guided, choice-driven MCP wizard (qa_wizard) + missing-parameter prompts
    # via MCP elicitation (ctx.elicit) -- UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)): QA_MCP_ELICIT_ENABLED
    # was DELETED and the behaviour hardcoded to the value the DISTRIBUTION
    # ships (`true`) -- as do this repo's own .mcp.json and .cursor/mcp.json --
    # NOT this field's code default (`False`). The per-CLIENT limitation that
    # made a switch look necessary is handled WITHOUT one: Claude Desktop
    # advertises no elicitation capability, so ctx.elicit raises there, the
    # callback reports UNAVAILABLE and the caller falls back to the markdown
    # menu exactly as it did with the flag off. the elicit seams were deleted (WD) and the ON path is inlined; every
    # dialog stays bounded by the existing per-call elicitation budget. See
    # docs/FEATURE_FLAGS.md.

    # GitHub-Release startup self-update (launcher.py -> tools/updater.py) --
    # REMOVED 2026-08-13 (flag-surface reduction, batch 6): QA_AUTO_UPDATE_ENABLED
    # was DELETED and hardcoded OFF, so a developer checkout's `python
    # launcher.py` never opts itself into a self-update. This does NOT change
    # the distribution: its generated launcher calls
    # run_update_check(force=True, ...), and that force argument bypasses the
    # deleted field exactly as it always did. See docs/FEATURE_FLAGS.md.
    # "owner/name" of the (private) GitHub repo to check for releases. Empty
    # disables the check even when a caller forces it.
    qa_update_repo: str = ""
    # GitHub token for the Releases API + zipball download. REQUIRED for a
    # private repo (fine-grained PAT, read-only Contents scope). Sent only as an
    # Authorization: Bearer header, never logged. .env only.
    github_token: str = ""
    # Bounded network timeout (seconds) for the release check + download.
    qa_update_timeout: int = 10
    # Integrity self-heal + read-only code lock (distribution installs) --
    # REMOVED 2026-08-13 (flag-surface reduction, batch 6): QA_CODE_LOCK_ENABLED
    # was DELETED and hardcoded OFF for a developer checkout. The distribution
    # is unaffected: its launcher passes lock_override=True, which takes
    # precedence exactly as it always did, so startup still verifies every
    # MANIFEST.sha256 entry, re-downloads locally-modified files and chmods code
    # read-only there. See docs/FEATURE_FLAGS.md.

    # Release-signature enforcement for auto-updates (Ed25519). When ON, an
    # update or self-heal is REFUSED unless the release ships a MANIFEST.sig
    # that verifies against ANY public key in the keyring embedded in
    # tools/updater_keys.RELEASE_PUBLIC_KEYS_HEX. Default ON: every release is
    # signed by scripts/release.py. An INVALID signature is ALWAYS rejected
    # regardless of this flag. Emergency escape hatch (e.g. a lost signing key):
    # QA_UPDATE_REQUIRE_SIGNATURE=false in .env accepts an unsigned release
    # while still rejecting a bad one; see docs/HANDOFF.md. Lenient never-raising
    # bool coercion.
    qa_update_require_signature: bool = True

    # --- Usage analytics (telemetry) - opt-out; ON only in the dist. ---
    # Anonymous usage metrics via tools/telemetry.py (PostHog). Sends only
    # when a PostHog key is present (POSTHOG_API_KEY / the dist's baked
    # default) AND no opt-out is set. It stays inert in the private checkout
    # (no key) and is turned ON only by the distribution build, with README
    # disclosure.
    #
    # 2026-08-13, and this one deserves reading twice: QA_TELEMETRY_DISABLED was
    # DELETED and hardcoded to its default, False (flag-surface reduction,
    # batch 6). There is now exactly ONE opt-out, the cross-vendor standard
    # DO_NOT_TRACK=1 environment variable, which tools/telemetry._opted_out()
    # still honours. Every document that offered two must name only that one --
    # an opt-out that silently does nothing is worse than no opt-out at all.
    # See docs/FEATURE_FLAGS.md.
    # Optional operator-set identity (known teams). When set it becomes the
    # PostHog distinct_id and person email; otherwise an anonymous install
    # UUID (qa_telemetry_id_path) is used. No other PII is ever collected.
    qa_user_email: str = ""
    # Where the anonymous install UUID is persisted (update-protected data dir).
    qa_telemetry_id_path: str = "data/telemetry-id"
    # PostHog project API key (a WRITE-ONLY public ingest key). Empty in the
    # private checkout; the distribution build bakes a default. Env overrides.
    posthog_api_key: str = ""
