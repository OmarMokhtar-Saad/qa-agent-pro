"""Jira sibling/comment/image fetch plus device and mobile capture fields.

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class CaptureSettings(BaseModel):
    # 2026-08-03 (user-approved) -- the sibling USER STORIES under the same
    # parent, WITH their bodies. _extract_subtasks / _extract_issuelinks only
    # ever see {key, summary, status} from arrays that ride along with the issue
    # fetch, so the requirements WRITTEN IN a sibling story were invisible: a
    # sub-task inherits them and a tester reading the board sees them.
    # Default OFF since 2026-09-25 (Air onboarding test) -- this REVERSES the
    # "deliberate exception to the defaults-OFF rule" this comment used to
    # record (matching jira_fetch_parent above): unrequested sibling fetches
    # cost a tester extra, unexplained Jira calls. Category: operator-choice
    # (unchanged), documented in docs/FEATURE_FLAGS.md. Costs ONE extra
    # host-side searchJiraIssuesUsingJql call when turned ON, and only when a
    # parent exists. Set JIRA_FETCH_SIBLING_STORIES=true to opt in.
    jira_fetch_sibling_stories: bool = False
    # How many sibling stories may contribute a BODY. Deliberately its own knob
    # rather than reusing the 10-issue _MAX_RELATED_ISSUES list cap: listing ten
    # keys costs a line each, but ten BODIES split the character budget ten ways.
    # A live TICKET-5645 run made the difference concrete -- 3000/10 = 300 chars per
    # story cut every markdown table mid-row and grounded nothing, while the host
    # had to ship 83k characters so 3k could be kept. Five stories at ~600 chars
    # each carry a readable use-case instead. 0 disables the block.
    jira_max_sibling_stories: int = 5
    # Total character budget for the sibling block, ON TOP of
    # jira_max_parent_chars (the composed background block is capped at the sum,
    # so sibling prose can never displace the parent's own description). 0 means
    # "emit no sibling block", which is why this is deliberately NOT in
    # _POSITIVE_INT_FIELDS -- same convention as jira_max_parent_chars.
    jira_max_sibling_chars: int = 3000

    # Ticket comments. 2026-08-03 (user-approved): ON by default. The extra
    # REST call (/issue/{key}/comment) that justified defaulting OFF is GONE --
    # Jira is read through the calling agent's own Atlassian MCP connection,
    # where `comment` is one more entry in the `fields` list of the SAME
    # getJiraIssue call, so there is no second network call to avoid any more.
    # A Jira description is a snapshot taken at refinement while the CURRENT
    # requirements accumulate in the thread, which is exactly what a tester
    # needs. Deliberate exception to the defaults-OFF rule (see
    # docs/FEATURE_FLAGS.md); JIRA_FETCH_COMMENTS=false is the kill-switch.
    jira_fetch_comments: bool = True
    jira_max_comments: int = 5

    # --- Comment reconciliation (Batch 1) — DELETED 2026-08-15. -----------
    # Batch 8b-ii (2026-08-14) deleted QA_COMMENT_RECONCILE_ENABLED and
    # hardcoded the pipeline OFF -- this field's own code default AND the
    # value .env.example shipped, so no install changed -- and kept the six
    # qa_comment_reconcile_* knobs as revival tuning behind the named seam
    # tools/comment_reconciler.enabled(). Dead-code deletion batch D5
    # (2026-08-15) deleted the module that seam guarded, so all six lost their
    # last reader and went with it: _MAX_COMMENTS, _BOT_AUTHORS,
    # _FIELD_THRESHOLD, _DEDUP_THRESHOLD, _MAX_AMENDMENTS, _MAX_CHARS. They
    # also left BOTH validator lists above (_POSITIVE_INT_FIELDS and the
    # second field_validator, which hold different fields in a different
    # order) and the _coerce_reconcile_threshold list.
    #
    # A stale QA_COMMENT_RECONCILE_* line in an existing .env still loads --
    # model_config uses extra="ignore" -- and now does nothing at all.
    # JIRA_MAX_COMMENTS (above, default 5) is the ONLY comment bound left, and
    # tools/jira_mcp leaves the raw "## Comments" dump in the ticket text.
    # The revival contract, including the containment control that sanitised
    # attacker-writable comment text before it reached a model, is
    # docs/RETIRED_CAPABILITIES.md section 4.

    # Image attachments. 2026-08-03 (user-approved): ON by default -- but read
    # what it does and does NOT buy. On the MCP Jira path this yields attachment
    # METADATA ONLY ({filename, mime, size}): tools/jira_mcp.py makes no
    # outbound HTTP request by hard rule, the Atlassian MCP server returns no
    # attachment bytes, and the byte-fetching REST path
    # (jira_fetcher._fetch_jira_images) is ANTHROPIC_API_KEY-gated. So ON buys
    # two real things and NOT vision: the filenames themselves become grounding
    # ("the ticket has error_state.png"), and images_unavailable fires the
    # existing handler notice that NAMES them and asks the tester to attach the
    # screenshots to the chat -- where they ARE analysed, via IMAGE_JOB. That
    # notice is the point: OFF meant silently generating a suite that never saw
    # the screenshots and never saying so.
    jira_fetch_images: bool = True
    jira_max_images: int = 3
    jira_max_image_bytes: int = 5_000_000  # Anthropic's own per-image vision cap

    # --- Jira attachment BYTE retrieval -- REMOVED 2026-08-13. --------------
    # QA_JIRA_ATTACHMENT_FETCH_ENABLED was DELETED and the behaviour hardcoded
    # OFF (flag-surface reduction, batch 6): a kill-switch is no longer a
    # tester-editable .env value, because .env holds credentials and paths and
    # not behaviour. tools/jira_attachments.enabled() returns the False constant,
    # so nothing in this tree makes the credentialed
    # /rest/api/3/attachment/content/{id} request and a ticket's screenshots
    # reach a model only when a tester attaches them to the chat. See
    # docs/FEATURE_FLAGS.md.

    # Direct chat image uploads: `QA_MAX_CHAT_IMAGES` / `QA_MAX_CHAT_IMAGE_BYTES`
    # were DELETED on 2026-08-30 with `QA_CLASSIFIER_MODEL` above. They capped
    # what `tools/image_description.py` would read from an upload before handing
    # it to `llm.ask_vision()` on the `api` backend; that call site migrated to
    # the host on 2026-08-15 and the coroutine and every backend went in P2-G on
    # 2026-08-16, leaving both fields with no reader at all. Attached screenshots
    # now ride to the tester's OWN chat model as MCP image content, whose limits
    # are that client's, not this server's -- a cap here could not have been
    # enforced and telling an operator otherwise was the false part.
    # Mobile device capture -> test cases -- UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)): QA_MOBILE_CAPTURE was
    # DELETED and the behaviour hardcoded to the value the DISTRIBUTION ships
    # (`true`), NOT this field's code default (`False`) -- the same divergence,
    # and the same reasoning, batch 6 recorded for QA_SWAGGER_ENABLED. Testers
    # can always list attached Android/iOS devices, pick one, and capture
    # screens; handlers.mobile_capture() returns the True constant.
    # qa_capture_screens makes NO server-side vision call (the screens ride to
    # the tester's own chat model as MCP image content), so that path needs no
    # credential. CORRECTED 2026-08-30 (audit finding 8): this comment used to
    # add that "the Feature-Analysis mobile modes still call llm.ask_vision(),
    # which needs ANTHROPIC_API_KEY regardless of QA_LLM_BACKEND". All three
    # names are gone -- `image_description.describe_images` migrated to the
    # host on 2026-08-15, and P2-G deleted `ask_vision` and both settings on
    # 2026-08-16. NO mode of Feature Analysis reaches a server-side model, and
    # no key is needed for any of this. Device discovery/capture is bounded by
    # the two timeouts below. See docs/FEATURE_FLAGS.md.

    # Timeout (seconds) for device-discovery commands (adb devices / simctl list).
    qa_device_command_timeout: int = 20
    # Timeout (seconds) for a single screenshot capture (larger -- image transfer).
    qa_device_screenshot_timeout: int = 60
    # --- Mobile lane: HTTPS capture ----------------------------------------
    # KILL-SWITCH (flag policy category 1), default OFF forever. ON lets the
    # mobile lane download the HTTPS-capture proxy onto this machine and
    # install its CA certificate into a device's trust store -- effects that
    # land outside this process. It gates neither tool registration nor the
    # lane's device effects. Every download or install additionally requires
    # apply=true on the call, and a flag-OFF apply=true REFUSES BY NAME rather
    # than silently dry-running, exactly as mcp_handlers.handle_push_suite
    # does. NO ALIAS: extra="ignore" drops a stale key under the lane's former
    # name, so it enables nothing (tests/test_settings.py; the rename is in
    # docs/DECISIONS.md). See docs/FEATURE_FLAGS.md and tools/flag_registry.py,
    # which carry the same rationale verbatim.
    qa_mobile_https_capture_enabled: bool = False
    # App-log evidence for the mobile lane -- OPERATOR-CHOICE (flag policy
    # category 4), default ON. ON: while a case runs, the lane keeps the app's own
    # logcat slice and pulls its event log (`run-as`, debuggable builds only), both
    # scrubbed before disk, so the report can show the app's model round-trips and
    # endpoint calls beside each case. OFF: nothing from the app's own output is
    # written to this machine, even redacted -- a legitimate choice for a site
    # that must not persist health-shaped content locally. Both values are right
    # somewhere, which is what makes this a toggle and not a kill-switch; the
    # kill-switch above still gates every device effect. Only a package with a
    # profile in tools/mobile_evidence/profiles/ (or ~/.qa-agents/mobile/profiles/)
    # is ever captured. See docs/FEATURE_FLAGS.md.
    qa_mobile_app_evidence: bool = True
    # Shared cache root for everything the mobile lane downloads or writes
    # (SDK, JRE, the verified IME APK, run checkpoints, locks). Empty means
    # ~/.qa-agents/mobile. This is NOT a gate -- an unset value has a working
    # default -- which is why it carries no FLAG_REGISTRY entry.
    qa_mobile_cache_dir: str = ""
    # Seconds to wait for sys.boot_completed after starting an AVD. A cold
    # first boot of a Play-Store system image genuinely takes minutes, and
    # `adb wait-for-device` returns long before the launcher exists.
    qa_mobile_boot_timeout_s: int = 240
