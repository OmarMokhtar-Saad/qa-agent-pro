"""Facade of the Jira MCP directive layer (tools.jira_mcp_* hold the code).

Hard rules (AGENTS.md) the modules keep: nothing here raises to a caller; the
server holds no Jira credential and makes no outbound request; it returns a
fetch DIRECTIVE the calling agent runs against its own Atlassian MCP tool; the
host payload is untrusted and passes through tools.untrusted. This module only
re-exports the names handlers, tools/jira_fetcher.py and the tests use.
"""

from __future__ import annotations

import json  # noqa: F401
import secrets  # noqa: F401

import llm  # noqa: F401
from tools.jira_mcp_ac import (  # noqa: F401
    _extract_ac_from_description,
    extract_ac_from_uc_table,
)
from tools.jira_mcp_ac_field import (  # noqa: F401
    _ac_field_discovery_on,
    ac_field_discovery_on,
    resolve_ac_field,
    usable_ac_text,
)
from tools.jira_mcp_access import access_verdict  # noqa: F401
from tools.jira_mcp_adf import (  # noqa: F401
    _MAX_ADF_DEPTH,
    _as_text,
    _extract_adf_text,
    _keyless_node_text,
    adf_text_with_depth_flag,
)
from tools.jira_mcp_attachments_scan import (  # noqa: F401
    _MAX_IMAGE_ATTACHMENTS_SCANNED,
    _extract_image_attachments,
    _ranked_attachments,
    _screen_reference_advisory,
)
from tools.jira_mcp_comments import (  # noqa: F401
    _comment_lines,
    _effective_comment_cap,
    _extract_comment_records,
)
from tools.jira_mcp_connect import (  # noqa: F401
    _CONNECT_CLOSING,
    _CONNECT_STEP_CLAUDE_CODE,
    _CONNECT_STEP_CURSOR,
    _CONNECT_STEP_GEMINI_CLI,
    _CONNECT_STEPS_BY_CLIENT,
    connect_steps,
    jira_page_without_issue_message,
    not_connected_message,
)
from tools.jira_mcp_context import _build_parent_context, _ParentBg  # noqa: F401
from tools.jira_mcp_directive import (  # noqa: F401
    build_fetch_directive,
    jira_mcp_required_result,
)
from tools.jira_mcp_directive_parts import _directive_head  # noqa: F401
from tools.jira_mcp_errors import _NO_IDENTITY, _sanitize_echo  # noqa: F401
from tools.jira_mcp_hint import connect_hint_line  # noqa: F401
from tools.jira_mcp_identity import (  # noqa: F401
    _has_identity,
    _identity_candidates,
    _identity_label,
    _identity_value,
    _unwrap_mcp_content,
)
from tools.jira_mcp_images import (  # noqa: F401
    _LABEL_LOOKBEHIND_CHARS,
    _count_image_refs,
    _image_ref_labels,
)
from tools.jira_mcp_keys import (  # noqa: F401
    _valid_issue_key,
    bare_issue_key,
    issue_key_from_url,
    issue_url_for_key,
    selected_issue_key,
)
from tools.jira_mcp_local_config import (  # noqa: F401
    _local_config_paths,
    _workspace_config_paths,
    atlassian_entry_configured,
    local_atlassian_entry_exists,
)
from tools.jira_mcp_nodes import (  # noqa: F401
    _extract_names,
    _extract_priority,
    _issue_fields,
)
from tools.jira_mcp_normalize import (  # noqa: F401
    PUBLIC_CALLABLES,
    normalize_issue_payload,
    verify_jira_access,
)
from tools.jira_mcp_parent import _linked_parent_ref, _parent_body  # noqa: F401
from tools.jira_mcp_payload import (  # noqa: F401
    MAX_PAYLOAD_BYTES,
    load_payload,
)
from tools.jira_mcp_related import (  # noqa: F401
    _MAX_RELATED_ISSUES,
    _extract_issuelinks,
    _extract_parent_ref,
    _extract_subtasks,
)
from tools.jira_mcp_sibling_count import _count_sibling_candidates  # noqa: F401
from tools.jira_mcp_siblings import _extract_sibling_bodies  # noqa: F401
from tools.jira_mcp_tables import _flatten_table_text  # noqa: F401
from tools.jira_mcp_tool_names import (  # noqa: F401
    access_tool_name,
    fetch_tool_name,
    probe_provenance,
    probe_provenance_accepted,
    tool_prefix,
    verify_tool_name,
)
from tools.jira_mcp_urls import (  # noqa: F401
    _strip_urls,
    is_jira_host,
    looks_like_jira_url,
)
from tools.jira_mcp_verify import verify_outcome, verify_result_message  # noqa: F401
from tools.jira_mcp_verify_directive import verify_directive  # noqa: F401
from tools.jira_mcp_verify_parse import _MAX_VERIFY_CHARS  # noqa: F401
