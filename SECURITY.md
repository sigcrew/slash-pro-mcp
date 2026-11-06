# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/sigcrew/slash-pro-mcp/security/advisories/new)
("Security" tab → "Report a vulnerability"). Include steps to reproduce, the version, and the impact you expect.

We aim to acknowledge reports within 3 business days and to ship a fix or mitigation for confirmed issues as quickly as their severity warrants. We will credit you in the advisory unless you prefer otherwise.

Never include a real API token in a report. If you think a token was exposed, revoke it in the SLASH web app first.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x (latest) | ✅ |
| older | ❌ |

## Release integrity

Releases are built and published only by the `release.yml` workflow from `v*` tags on commits that are on `main`, via PyPI Trusted Publishing (no stored PyPI token) with build provenance attestations. Maintainers should require reviewers on the `pypi` GitHub environment and protect `v*` tags so a single compromised account cannot publish.

## Scope

This repository covers the local MCP client. Problems with the SLASH service itself (API authorization, the web app) can be reported the same way, and we will route them.
