# Contributing

Thanks for helping! Issues and pull requests are welcome.

- Setup: `uv sync`. Tests: `uv run pytest -q`. Lint: `uv run ruff check && uv run ruff format --check`.
- Keep the server thin: one tool per REST call. The API is the source of truth for permissions; the few client-side guards (own notes and evidence, checklist merge, no `canceled`) are a safety net for well-behaved agents, not a security boundary.
- Tool descriptions are English and describe API semantics only; team process rules belong in the workspace rules page.
- Do not add tools for human-only actions (re-parenting, merges, outcome judgements, answering questions, cancelling, token management).
- Every path segment that comes from a tool argument goes through `client.path_segment`. User-written content returned to the model goes through `render.untrusted`.
- Add a test with the httpx `MockTransport` fake (`tests/helpers.py`) for every behaviour change. Use obviously fake ids (`00000000-0000-7000-8000-0000000000xx`), `example.com` addresses and placeholder names.
- Never commit tokens, real workspace ids or internal URLs. CI runs gitleaks.
- Security issues: see [SECURITY.md](https://github.com/sigcrew/slash-pro-mcp/blob/main/SECURITY.md). Please don't open a public issue for them.
