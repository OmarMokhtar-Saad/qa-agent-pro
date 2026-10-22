# Environment variables

Every variable the server reads, from the environment or `.env`. Names are case-insensitive. A blank default means unset. This file is generated from the code; see [configuration.md](configuration.md) for the common ones.

## User-facing

| Variable | Default |
|---|---|
| `ADB_SERVER_SOCKET` |  |
| `ADB_SOCKET_DISABLED` |  |
| `ANDROID_ADB_SERVER_PORT` |  |
| `JIRA_AC_FIELD` | empty |
| `JIRA_BASE_URL` | empty |
| `JIRA_FETCH_COMMENTS` | `True` |
| `JIRA_FETCH_IMAGES` | `True` |
| `JIRA_FETCH_PARENT` | `False` |
| `JIRA_FETCH_SIBLING_STORIES` | `False` |
| `JIRA_MAX_AC_CHARS` | `6000` |
| `JIRA_MAX_COMMENTS` | `5` |
| `JIRA_MAX_CONTEXT_CHARS` | `12000` |
| `JIRA_MAX_IMAGES` | `3` |
| `JIRA_MAX_IMAGE_BYTES` | `5000000` |
| `JIRA_MAX_PARENT_CHARS` | `2500` |
| `JIRA_MAX_SIBLING_CHARS` | `3000` |
| `JIRA_MAX_SIBLING_STORIES` | `5` |
| `QA_AC_ANCHORING_ENFORCE` | `False` |
| `QA_API_FRAMEWORK_PATH` | empty |
| `QA_API_FRAMEWORK_WRITE_DRY_RUN` | `True` |
| `QA_API_FRAMEWORK_WRITE_ENABLED` | `False` |
| `QA_API_PROJECTS_DIR` | empty |
| `QA_API_PROJECT_STORE_PATH` | `data/api-projects.db` |
| `QA_API_REGISTRY_STORE_PATH` | `data/api-registry.db` |
| `QA_API_TEMPLATE_REPO` | empty |
| `QA_API_TEST_ENABLED` | `False` |
| `QA_AUDIT_LOG_PATH` | `data/audit.db` |
| `QA_AUTO_FLOW_REPLAY` |  |
| `QA_CHECKLIST_MAX_ITEMS` | `200` |
| `QA_CHECKLIST_MAX_PROMPT_CHARS` | `32000` |
| `QA_CHECKLIST_MIN_GRANULARITY` | `0.6` |
| `QA_DEVICE_COMMAND_TIMEOUT` | `20` |
| `QA_DEVICE_SCREENSHOT_TIMEOUT` | `60` |
| `QA_DIST_MODE` | `False` |
| `QA_EXPORT_DIR` | `data/exports` |
| `QA_HOST_AMBIGUITY_REQUIRE_RESULT` | `True` |
| `QA_HOST_DEDUP_APPLY` | `False` |
| `QA_HOST_DEDUP_LOW_TEXT_RATIO` | `0.5` |
| `QA_HOST_DEDUP_MAX_GROUPS` | `50` |
| `QA_HOST_DEDUP_MAX_GROUP_SIZE` | `12` |
| `QA_HOST_DEDUP_MAX_REMOVAL_RATIO` | `0.35` |
| `QA_HOST_DUPLICATE_PREP_WINDOW_S` | `1800` |
| `QA_HOST_IMAGE_REQUIRE_RELEVANT` | `False` |
| `QA_JIRA_MCP_TOOL_PREFIX` | `mcp__atlassian__` |
| `QA_MCP_ENABLED` | `False` |
| `QA_MOBILE_APP_EVIDENCE` | `True` |
| `QA_MOBILE_BOOT_TIMEOUT_S` | `240` |
| `QA_MOBILE_CACHE_DIR` | empty |
| `QA_MOBILE_HTTPS_CAPTURE_ENABLED` | `False` |
| `QA_PREP_MAX_BYTES` | `4000000` |
| `QA_PREP_MAX_LIFETIME_S` | `14400` |
| `QA_PREP_TTL_S` | `3600` |
| `QA_RAG_MAX_ENTRIES` | `0` |
| `QA_RAG_RECENCY_HALF_LIFE_DAYS` | `0` |
| `QA_RAG_SIMILARITY_MODE` | `jaccard` |
| `QA_RAG_SIMILARITY_THRESHOLD` | `0.3` |
| `QA_RAG_STORAGE_PATH` | `corpus` |
| `QA_RAG_TOP_K` | `5` |
| `QA_SUITE_STORE_PATH` | `data/suites.db` |
| `QA_TELEMETRY_ID_PATH` | `data/telemetry-id` |
| `QA_TESTRAIL_PUSH_ENABLED` | `False` |
| `QA_UPDATE_REPO` | empty |
| `QA_UPDATE_REQUIRE_SIGNATURE` | `True` |
| `QA_UPDATE_TIMEOUT` | `10` |
| `QA_USER_EMAIL` | empty |
| `QA_XRAY_PUSH_ENABLED` | `False` |
| `TESTRAIL_API_KEY` | empty |
| `TESTRAIL_URL` | empty |
| `TESTRAIL_USER` | empty |
| `XRAY_BASE_URL` | `https://xray.cloud.getxray.app` |
| `XRAY_CLIENT_ID` | empty |
| `XRAY_CLIENT_SECRET` | empty |
| `XRAY_PROJECT_KEY` | empty |

## Other (tokens and integration keys read from the environment)

| Variable | Default |
|---|---|
| `GITHUB_TOKEN` | empty |
| `POSTHOG_API_KEY` | empty |
