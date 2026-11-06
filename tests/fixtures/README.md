# Test fixtures

`route_scopes.json` is a **subset** of the Slash Pro server's API-token scope table
(`"METHOD /path/{param}"` → required token scope): only the routes this package's tools call.
The fake API in `tests/helpers.py` fails a test when a tool requests a route that is missing
here or has no scope.

Refresh it from the server's table whenever a tool starts calling a new route (add only that
route), and drop routes no tool calls anymore.
