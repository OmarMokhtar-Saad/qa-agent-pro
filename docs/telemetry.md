# Telemetry and privacy

QA Agent Pro sends anonymous usage analytics so we can see which features are
used and on which platforms, and fix crashes faster. It is **on by default**,
as in most developer CLIs.

**Collected:**

- the tool name invoked (for example `qa_generate_test_cases`)
- the app version, your OS and CPU architecture
- the MCP client name and version (for example which editor is connected)
- an anonymous hashed machine id
- call duration, and success or failure (on failure, only the Python exception
  class name)
- content-free tool properties: test-case count, export format, and source type
  (feature text, Jira, Swagger, mobile)
- crash stack traces for issue grouping: function names, project-relative file
  names and line numbers. Exception messages and absolute paths are scrubbed
  before sending
- per-call AI-generation metrics (model, backend, token counts, latency)
- an email address, only if you set `QA_USER_EMAIL` yourself (optional)

**Never collected:** feature descriptions, Jira/URL/page content, generated test
cases, LLM prompts or completions, exception messages, absolute file paths, or
secrets.

**Opt out** at any time in `~/qa-agent-pro/.env`, with the cross-tool standard
variable:

```
DO_NOT_TRACK=1
```

With telemetry off nothing is sent, not even an opt-out ping.
