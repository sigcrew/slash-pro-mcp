# slash-pro-mcp

[한국어](https://github.com/sigcrew/slash-pro-mcp/blob/main/README.ko.md)

An [MCP](https://modelcontextprotocol.io) server for [SLASH](https://app.slash.pro). It lets AI coding agents (Claude Code, Codex, Cursor, VS Code Copilot and others) read and write your workspace's tickets, meetings, PRDs and documents.

It is a thin local **stdio** client. Each tool makes one REST call (or a few) to the SLASH API, authenticated with **your personal API token**. The server enforces authorization; this package adds no permissions of its own, and its few extra limits are a safety net, not a security boundary (see [Security model](#security-model)).

## Requirements

- [uv](https://docs.astral.sh/uv/) (provides `uvx`), or Python 3.12+ with `pipx`
- A SLASH workspace and a personal API token (`et_…`), issued in the web app's settings
- Your workspace id, which is the UUID in the web app URL

## Quick start

1. Save the token to a file only you can read. This prompts for the token without showing it or putting it in your shell history:

   ```bash
   (umask 077; mkdir -p ~/.config/slash-pro; printf 'Paste token, then Enter: '; read -rs T; echo; printf '%s' "$T" > ~/.config/slash-pro/token); ls -l ~/.config/slash-pro/token
   ```

   The last line should start with `-rw-------`. On macOS and Linux the server refuses to start if the token file is accessible by group or others. Windows has no such permission check, so keep the file in your user profile.

2. Register the server with your agent (see [Client setup](#client-setup)). With Claude Code:

   ```bash
   claude mcp add slash-pro -s user \
     -e SLASH_PRO_WORKSPACE_ID=00000000-0000-0000-0000-000000000000 \
     -- uvx slash-pro-mcp==0.1.0
   ```

3. Ask your agent something like *"list my in-progress tickets"*.

Check the install without an agent: `uvx slash-pro-mcp==0.1.0 --help`.

The snippets pin the version (`slash-pro-mcp==0.1.0`) so a new release never runs without you choosing it. Bump the pin deliberately after reading the [changelog](https://github.com/sigcrew/slash-pro-mcp/blob/main/CHANGELOG.md).

## Configuration

Set these environment variables in the agent's MCP config.

| Variable | Default | Meaning |
|---|---|---|
| `SLASH_PRO_WORKSPACE_ID` | — (required) | Workspace UUID |
| `SLASH_PRO_TOKEN` | — | Personal API token. If you leave it unset, the token is read from a file |
| `SLASH_PRO_TOKEN_FILE` | `~/.config/slash-pro/token` | File holding the token. Must be `chmod 600` |
| `SLASH_PRO_API_URL` | `https://echo-api.slash.pro` | API root. Must be `https://echo-api.slash.pro` (any path) unless a custom host is allowed. The host must be lowercase ASCII |
| `SLASH_PRO_ALLOW_CUSTOM_API` | — | **Development / self-hosting only.** Set to the exact host to allow, e.g. `localhost` or `api.example.com` (`1` is refused). Loopback (`localhost`, `127.0.0.1`, `[::1]`) also needs this and is the only place plain `http://` is allowed. The token must then be a literal `SLASH_PRO_TOKEN`; no token file (default or `SLASH_PRO_TOKEN_FILE`) is ever read for a custom host |
| `SLASH_PRO_WORKSPACE_RULES` | `1` | Set to `0` to keep the workspace rules page out of the server instructions. The page stays protected from agent writes |
| `SLASH_PRO_TOOLS` | all | Comma-separated tool groups to register, to keep the tool list (and its context cost) small: `projects`, `tickets`, `ticket_extras`, `questions`, `meetings`, `meeting_agenda`, `documents` (old name `wiki` still accepted), `doc_updates`, `prds`, `outcomes`, `search`. Unknown names stop the server |
| `SLASH_PRO_AGENT` | — | Recipient label for `list_handoffs` when `to` is not given (aliases match, e.g. `codex` = `Codex`). Unset: `list_handoffs` shows handoffs to every recipient. Claims and notes are always recorded under the token's own label |
| `SLASH_PRO_AGENT_MODEL` | — | Model alias, sent as `X-Agent-Model` so the activity log records which model acted |
| `SLASH_PRO_REQUESTED_BY` | — | Overrides the requester shown on writes. By default the server derives it from the token owner |

Prefer `SLASH_PRO_TOKEN_FILE` (or the default file) over `SLASH_PRO_TOKEN`. That keeps the secret out of config files that are often committed or synced.

> **Never put a literal token in a project-level config file** such as `.cursor/mcp.json`, `.vscode/mcp.json` or `.mcp.json`. Those files get committed. Use the token file, an `${env:…}` / `${input:…}` reference, or your user-level config.

## Client setup

Every client runs the same command, `uvx slash-pro-mcp==0.1.0`. Some GUI apps don't inherit your shell's `PATH`. If yours can't find `uvx`, use its absolute path (run `which uvx` to find it).

### Claude Code

```bash
claude mcp add slash-pro -s user \
  -e SLASH_PRO_WORKSPACE_ID=<workspace-uuid> \
  -- uvx slash-pro-mcp==0.1.0
claude mcp list
```

### Codex CLI — `~/.codex/config.toml`

```toml
[mcp_servers.slash-pro]
command = "uvx"
args = ["slash-pro-mcp==0.1.0"]
# The first run downloads the package; give it more than Codex's 10 s default.
startup_timeout_sec = 30
# Forward the token from your shell instead of writing it here (optional):
# env_vars = ["SLASH_PRO_TOKEN"]

[mcp_servers.slash-pro.env]
SLASH_PRO_WORKSPACE_ID = "<workspace-uuid>"
SLASH_PRO_AGENT = "Codex"
```

The CLI equivalent is `codex mcp add slash-pro --env SLASH_PRO_WORKSPACE_ID=<workspace-uuid> -- uvx slash-pro-mcp==0.1.0` (then add `startup_timeout_sec` to the generated entry).

### Cursor — `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global)

```json
{
  "mcpServers": {
    "slash-pro": {
      "type": "stdio",
      "command": "uvx",
      "args": ["slash-pro-mcp==0.1.0"],
      "env": {
        "SLASH_PRO_WORKSPACE_ID": "<workspace-uuid>",
        "SLASH_PRO_AGENT": "Cursor"
      }
    }
  }
}
```

To pass the token from your environment instead of the token file, add `"SLASH_PRO_TOKEN": "${env:SLASH_PRO_TOKEN}"`.

### VS Code / GitHub Copilot — `.vscode/mcp.json`

VS Code uses a top-level **`servers`** key, not `mcpServers`. An `inputs` entry with `"password": true` makes VS Code prompt for the token once and store it securely, so it never lands in the file:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "slash-pro-token",
      "description": "SLASH personal API token (et_...)",
      "password": true
    }
  ],
  "servers": {
    "slash-pro": {
      "type": "stdio",
      "command": "uvx",
      "args": ["slash-pro-mcp==0.1.0"],
      "env": {
        "SLASH_PRO_WORKSPACE_ID": "<workspace-uuid>",
        "SLASH_PRO_TOKEN": "${input:slash-pro-token}",
        "SLASH_PRO_AGENT": "Copilot"
      }
    }
  }
}
```

### Claude Desktop — `claude_desktop_config.json`

On macOS the file is `~/Library/Application Support/Claude/claude_desktop_config.json`. On Windows it is `%APPDATA%\Claude\claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "slash-pro": {
      "command": "/absolute/path/to/uvx",
      "args": ["slash-pro-mcp==0.1.0"],
      "env": {
        "SLASH_PRO_WORKSPACE_ID": "<workspace-uuid>",
        "SLASH_PRO_AGENT": "Claude Desktop"
      }
    }
  }
}
```

### Gemini CLI — `~/.gemini/settings.json`

```json
{
  "mcpServers": {
    "slash-pro": {
      "command": "uvx",
      "args": ["slash-pro-mcp==0.1.0"],
      "env": {
        "SLASH_PRO_WORKSPACE_ID": "<workspace-uuid>",
        "SLASH_PRO_AGENT": "Gemini CLI"
      }
    }
  }
}
```

Gemini CLI expands `$VAR` / `${VAR}` in `env`, so `"SLASH_PRO_TOKEN": "$SLASH_PRO_TOKEN"` also works. The CLI equivalent is `gemini mcp add -e SLASH_PRO_WORKSPACE_ID=<workspace-uuid> slash-pro uvx slash-pro-mcp==0.1.0`.

### Windsurf — `~/.codeium/windsurf/mcp_config.json`

```json
{
  "mcpServers": {
    "slash-pro": {
      "command": "uvx",
      "args": ["slash-pro-mcp==0.1.0"],
      "env": {
        "SLASH_PRO_WORKSPACE_ID": "<workspace-uuid>",
        "SLASH_PRO_AGENT": "Windsurf"
      }
    }
  }
}
```

### Any other stdio client

Run `uvx slash-pro-mcp==0.1.0` (or `python -m slash_pro_mcp` inside an environment where the package is installed) with the environment variables above. The server speaks MCP over stdin/stdout and logs only to stderr.

## Tools

Tools return markdown. Output that contains text from the workspace is wrapped in an untrusted-content fence (see [Security model](#security-model)). Server errors come back as tool errors carrying the API's message, error code and candidates (`SLASH API 오류:` + `HTTP 409: …`), also fenced. Each tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`). Tool results and tool error messages are in Korean, the product's primary language (identifiers, enum values, parameter names and ticket keys stay as they are, e.g. `알 수 없는 status: merged (허용: backlog, …)`); tool descriptions and parameter schemas are in English.

| Area | Tool | Kind | What it does |
|---|---|---|---|
| Tickets | `list_tickets` | read | Tickets for one project or the whole workspace, filtered by status, assignee, blocked and PRD (one server-filtered call per wanted status, merged). Newest-created first (ties by id); at most `limit` rows (default 50, max 200) plus a "… 외 N개" line |
| | `get_ticket` | read | Full working context for `KEY-N`: description, acceptance, evidence, relations, handoffs, activity |
| | `create_ticket` | write | Create a ticket, sub-ticket (`parent`) or follow-up (`follows`). If a step after creation fails, the result still names the new key |
| | `update_ticket` | write | Status, assignee and due date. Status: `backlog` · `todo` · `in_progress` · `review` · `done` (`canceled` needs a reason: web app). Who may move tickets to `done` is a workspace rule, not enforced here |
| | `add_note` | write | Timeline note |
| | `relate_ticket` | write | `follows` / `blocks` relation |
| | `mark_deployed` | write | Idempotent `vX으로 배포됨` ("deployed in vX") system note |
| | `delete_relation` | write | Remove a `follows` / `blocks` relation to another ticket |
| | `replace_checklist` | write | Replace the agent-owned checklist rows (`owner: agent`, written by any agent); people's and unowned rows and review results are kept, and dropping or rewording a reviewed row is refused |
| | `list_evidence` | read | The ticket's evidence thread (pinned transcript excerpts) |
| | `add_evidence` · `delete_evidence` | write | Pin a transcript excerpt / remove evidence this agent added in the current session |
| | `update_note` · `delete_note` | write | Edit a plain note / delete any note this agent wrote in the current session. Handoff and verification bodies can't be edited; the server allows author edits only within 10 minutes |
| Outcomes | `list_outcomes` · `get_outcome` | read | Outcomes of a project; one outcome by key (`CORE-45`) or id |
| | `create_outcome` · `update_outcome` | write | Create (`draft` / `executing` / `paused`) or edit fields (revision checked) |
| | `add_outcome_evidence` | write | Evidence for an outcome |
| | `add_ticket_to_outcome` · `remove_ticket_from_outcome` | write | Ticket membership (`tickets:read`, `tickets:write` and `document:read`); `move=True` moves a ticket from another outcome |
| Search | `search` | read | Workspace search over tickets, outcomes, docs and meetings (titles and content) |
| Verification | `start_verification` | write | Pin commits and answers before verifying |
| | `add_verification` | write | Structured verification report (typed schema matching the server's) |
| Handoffs | `add_handoff` | write | Structured handoff to another agent or role |
| | `list_handoffs` | read | Handoff inbox for `to` / `SLASH_PRO_AGENT`, or for everyone when neither is set; shows what blocks each |
| | `update_handoff` | write | Handoff lifecycle: `claim` · `heartbeat` · `complete` · `block` (claims are atomic and leased) |
| Readiness | `ask_questions` | write | Ask the ticket owner up to 4 questions |
| | `list_questions` | read | Questions on one ticket (any state), or the open questions a person should answer |
| | `readiness_refresh` | write | Re-judge remaining assumptions (rate-limited) |
| Meetings | `list_meetings` | read | Find meetings by date, title, series or attendee; `upcoming=True`: scheduled meetings, soonest first |
| | `get_meeting` | read | `part=overview`: summary, attendees, transcript and analysis status; `minutes`: latest minutes as markdown; `summary`: executive summary and check points |
| | `find_transcript_excerpts` | read | Transcript passages that support a statement (server-side LLM call) |
| | `add_todo` | write | Add an approved to-do to a meeting |
| | `add_agenda_item` | write | Agenda line for the next occurrence of a recurring meeting |
| Documents | `document_tree` | read | A project's page tree |
| | `get_document` | read | Page body (plain text or markdown) |
| | `search_documents` | read | Documents ranked by the server (title/body match, recent use), optionally in one project; or matching lines in one page |
| | `write_document` | write | Append to or replace a page body |
| | `create_document` | write | New page under a section root |
| | `list_doc_updates` | read | AI doc-update proposals for a page or produced by a meeting (by meeting: `document:read` and `meetings:read`) |
| | `resolve_doc_update` · `accept_all_doc_updates` | write | Accept, revert or reject one AI doc update (checked against its page) / accept all of a page's (never on the workspace rules page) |
| | `refresh_doc_update` | write | Re-check a proposal against the latest document |
| PRDs | `list_prds` · `get_prd` | read | PRDs and their tickets |
| | `create_prd` | write | New PRD (the server's skeleton sections or markdown) |
| | `assign_ticket_prd` | write | Attach a ticket to a PRD or detach it |
| Projects | `create_project` · `update_project` | write | Needs the `projects:write` scope |

Some actions are **human-only on purpose**, and no tools exist for them: re-parenting tickets, merges, outcome stage transitions, stop reasons and judgements, outcome related tickets and documents, promoting tickets to outcomes, answering readiness questions, cancelling tickets, archiving or deleting projects, and token management.

## Security model

- **Your token is the boundary.** The server checks every call against the token's scopes and the owner's workspace membership. Issue a token with only the scopes your agent needs:
  - read-only: `tickets:read`, `document:read`, `meetings:read`
  - typical agent: add `tickets:write` and `document:write`
  - `projects:write` lets the token rename or re-describe **every** project in the workspace. Grant it only when needed.
  - `meetings:write` and `recordings:write` can trigger AI and transcription cost. This package doesn't need them for most tools.

  Use one token per agent and label it with the agent's name. Never share tokens. Tokens expire, so revoke and reissue them in the web app.
- **Token handling.** The token is read from the environment or a `chmod 600` file. On macOS and Linux the server refuses group/world-accessible token files (Windows has no such check); it refuses tokens with whitespace or control characters everywhere. The token is never logged, never put in `repr`, error text or tool output, and is redacted if a response ever includes it. httpx, httpcore and mcp logging is kept at WARNING.
- **Where the token can go.** Only `echo-api.slash.pro` is accepted as the API host. Any other host, loopback included, needs `SLASH_PRO_ALLOW_CUSTOM_API=<that exact host>` (development / self-hosting only) and then accepts only a literal `SLASH_PRO_TOKEN` — token files are never read for a custom host, so editing the URL in a config file cannot send your saved token file elsewhere. Residual risk: a project-level config that sets a custom host could still reference a `SLASH_PRO_TOKEN` you exported in your shell (for example `${env:SLASH_PRO_TOKEN}`), so prefer the token file over exporting the token, and review project MCP configs before trusting them. Hosts must be lowercase ASCII (no IDN, backslashes, `;`, spaces or credentials). Configuration errors show only `scheme://host`.
- **Transport.** HTTPS only (plain HTTP works only for loopback during development), and TLS verification is always on with no option to turn it off. Proxy and custom-CA environment variables (`HTTPS_PROXY`, `SSL_CERT_FILE`, …) are **ignored on purpose** (`trust_env=False`), so the environment cannot reroute or intercept the token. Redirects are never followed. Each request has a 10 s connect timeout, a 30 s read timeout and an overall 60 s deadline; each tool call has an overall 120 s deadline. Responses are requested uncompressed (a compressed reply is refused before its body is read) and capped at 10 MB.
- **Input hygiene.** Ids and ticket keys are validated and percent-encoded before they go into a URL path — both those from tool arguments and those returned by the server. A model can't inject path traversal (`../`) or query strings (`?x=`). The workspace id must be a UUID.
- **Prompt injection.** Tickets, documents, minutes, transcripts, titles, handoffs, questions, server warnings and member names are written by people, including meeting participants outside your team. Every tool result that includes such text (read results and write confirmations that include titles, names or warnings) is returned inside a `<untrusted-workspace-content-XXXXXXXX source="…">` fence, with a fresh random suffix on every call so content can't forge the closing tag. Results made only of ids, keys and statuses (`add_note`, `add_handoff`, `add_evidence`, `update_note`, `mark_deployed`, the delete tools) are not fenced; ids the server returns are shown only if they are UUIDs or plain id strings, otherwise fenced, and an invalid id in a URL path is reported without repeating it. Server error messages (`source="api error"`, at most 2,000 characters plus 20 field errors), transport error details (`source="transport error"`) and member names in assignee-lookup errors (`source="members"`) are fenced too. A tool result longer than 200,000 characters is cut with a `… truncated` note. The server instructions tell the agent to treat fenced text as data, not instructions. This reduces the risk but does not remove it. Review what an agent writes back to your workspace, and keep write scopes off tokens used by agents that also read untrusted sources.
- **Workspace rules.** At startup the server reads the workspace's designated "agent instructions" document into the MCP `instructions`, giving up after 3 seconds so the client handshake is never held up (it then starts without the rules and logs a warning to stderr). Because every agent reads that text, the page title and body are placed together in a fence after a constant preamble: the rules are team process conventions for tickets and notes, and can never authorize running shell commands, installing software, reading or sending credentials or files, or contacting other hosts. This server refuses agent writes to that page (`이 페이지는 웹 앱에서 사람만 고칠 수 있음`): before every guarded write (`write_document`, `create_document` for the product page, `create_prd` with markdown, and accepting, reverting or rejecting AI doc updates) it looks the rules page up again and compares canonical UUIDs, so a page designated after startup or an alternate spelling of the id doesn't slip through. If that lookup fails, the write is refused (fail closed). `SLASH_PRO_WORKSPACE_RULES=0` keeps the rules out of the instructions; the page stays protected.
- **Client-side guards are a safety net, not a security boundary.** This client adds a few limits of its own (for example, it only edits or deletes notes and evidence it created, keeps people's and reviewed checklist rows, and refuses `canceled`) so a well-behaved agent doesn't damage people's work by mistake. Authorization is decided by the SLASH API from the token's scopes. Scope tokens narrowly, keep write scopes off agents that also read untrusted sources, and don't give untrusted agents shell access to the token file or environment.
- **No telemetry.** The only network destination is the configured API URL.
- **Releases.** Releases are published from tags by GitHub Actions with PyPI Trusted Publishing and build provenance attestations; the release job checks that the tag points to a commit on `main`. Maintainers should require reviewers on the `pypi` environment and protect `v*` tags.

See [SECURITY.md](https://github.com/sigcrew/slash-pro-mcp/blob/main/SECURITY.md) to report a vulnerability.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check && uv run ruff format --check
uv build
```

The tests fake the API with an httpx `MockTransport` and need no network or account.

## License

[MIT](https://github.com/sigcrew/slash-pro-mcp/blob/main/LICENSE) © SIG Inc.
