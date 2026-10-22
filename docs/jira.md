# Connect Jira

No API token and no `.env` entry — Jira Cloud is read through your own Atlassian
MCP connection (OAuth), the same way this server is connected to your editor.

1. Add the Atlassian MCP server in your client's MCP settings
   (`https://mcp.atlassian.com/v1/mcp/authv2`). Claude Code / Desktop: `.mcp.json`
   or Settings → Connectors. Cursor: Settings → Features → MCP. Gemini CLI:
   `gemini mcp add --transport http`.
2. Approve the one-time OAuth consent in the browser tab it opens.
3. Paste a ticket URL. The agent fetches it through that connection and nothing
   is stored on this machine.

If your client has no Atlassian connection yet, the agent replies with these
exact steps rather than failing silently or inventing ticket content.
