# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-04

### Added
- First public release: a stdio MCP server (`slash-pro-mcp`, server name `slash-pro`) with 50 tools:
  - tickets, verification, handoffs and readiness questions
  - meetings, wiki pages, PRDs and projects
  - outcomes: `list_outcomes`, `get_outcome` (key or id), `create_outcome`, `update_outcome`, `add_outcome_evidence`, `add_ticket_to_outcome`, `remove_ticket_from_outcome`
  - ticket extras:
    - `replace_checklist`: agent rows only; people's rows and review results are preserved
    - `list_evidence`, `add_evidence`, and `delete_evidence` (this session's evidence only)
    - `update_note` / `delete_note` (only notes this agent wrote in the current session)
    - `delete_relation`
  - `search`; `get_meeting(part=overview|minutes|summary)`; `list_meetings(upcoming=True)`
  - `update_ticket` (status, assignee, due date); `update_handoff(action=claim|heartbeat|complete|block)`
  - AI doc updates: `list_doc_updates`, `resolve_doc_update(action=accept|revert|reject)`, `accept_all_doc_updates`, `refresh_doc_update`; page-changing actions go through the workspace-rules-page guard
- `SLASH_PRO_TOOLS`: register only the named tool groups (e.g. `tickets,wiki`); default all.
- Tool results and tool error messages are in Korean (identifiers, enum values, parameter names and ticket keys unchanged); tool descriptions stay in English.
- English tool descriptions limited to API semantics, MCP tool annotations (`readOnlyHint` / `destructiveHint` / `idempotentHint`), and `serverInfo.version` set to the package version.
- `add_verification` takes a typed report matching the server's schema (enums visible in the JSON schema).
- `create_ticket` / `create_prd` report the new key or id even when a follow-up step fails; `create_prd` fills the server's current skeleton headings.
- `canceled` cannot be set through MCP (it needs a reason, chosen in the web app); handoff claims and notes are recorded under the token's label; `list_handoffs` has no default recipient unless `SLASH_PRO_AGENT` is set and shows what blocks each handoff.
- API errors include the server's error `code` and up to 10 `candidates`.
- `list_tickets` covers one project or the whole workspace (one server-filtered call per status), with `assignee`, `blocked` and `limit` (default 50, max 200, "… 외 N개" footer), sorted newest-created first.
- `SLASH_PRO_*` configuration with `https://echo-api.slash.pro` as the default API URL.
- Security hardening:
  - HTTPS-only API URL (loopback HTTP allowed for development), UUID workspace id
  - `chmod 600` token files and token character validation
  - token redaction
  - no redirects, explicit timeouts and a 10 MB response cap
  - validated, percent-encoded path segments
  - the API host is limited to `echo-api.slash.pro` unless `SLASH_PRO_ALLOW_CUSTOM_API` names the exact host (dev/self-host; `=1` is refused); a custom host accepts only a literal `SLASH_PRO_TOKEN`, never a token file
  - tool results capped at 200,000 characters and API error text at 2,000 characters plus 20 field errors; server-returned ids, transport errors and server warnings never reach the agent unfenced or unredacted
  - proxy/CA environment variables ignored, compressed responses refused, a 60 s overall deadline per request
  - server-returned ids validated before use in paths
  - untrusted-content fencing (per-call random tag) for user-written content and server error messages
  - workspace rules (title and body) fenced behind a constant limiting preamble and read at startup within 3 s so the handshake is never held up; agent writes to the rules page refused by canonical UUID, looked up again before every guarded write and failing closed; `SLASH_PRO_WORKSPACE_RULES=0` keeps the body out but the page protected
  - loopback API hosts count as custom hosts (opt-in, literal token only)
  - write confirmations that include workspace text are fenced; a 120 s overall deadline per tool call; `https` only for outcome evidence URLs
  - client-side guards (own notes/evidence, checklist merge, no `canceled`) documented as a safety net, not a security boundary
