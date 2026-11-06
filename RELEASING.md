# Releasing

1. On a branch, bump the version:
   - `version` in `pyproject.toml` (the only source; `__version__` and `serverInfo.version` read it)
   - every `slash-pro-mcp==x.y.z` pin in `README.md` and `README.ko.md` (`grep -rn "slash-pro-mcp==" README*`)
2. Move the `CHANGELOG.md` entries under a new `## [x.y.z] - YYYY-MM-DD` heading.
3. If a tool now calls a new server route (or stopped calling one), refresh
   `tests/fixtures/route_scopes.json` from the server's API-token scope table: add only the routes
   the tools call, with their scope (see `tests/fixtures/README.md`).
4. Run `uv lock --check`, `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`, `uv build`.
5. Merge the PR into `main`, then tag the merge commit: `git tag vx.y.z && git push origin vx.y.z`.
   The tag must match the `pyproject.toml` version and point to a commit on `main` (the workflow checks both).
6. The `release.yml` workflow builds, tests and waits on the `pypi` environment: a maintainer approves it,
   and it publishes to PyPI with Trusted Publishing and build provenance.
7. Check `uvx slash-pro-mcp==x.y.z --version` from a clean machine.
