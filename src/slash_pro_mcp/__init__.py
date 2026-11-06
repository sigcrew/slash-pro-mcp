"""SLASH MCP server — a local stdio adapter over the SLASH REST API.

This package talks to SLASH only through HTTP (httpx), only to the configured API URL.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("slash-pro-mcp")  # single source: pyproject.toml
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
