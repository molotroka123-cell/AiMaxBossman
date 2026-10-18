"""Public-web access for Bossman agents through the String MCP server.

policy.py      - URL/query rules, deny-list, rate limit, size cap, redaction, audit line
string_mcp.py  - endpoint template, secret name, key store, guarded search/fetch
web_learn.py   - bounded `web_learn` task: candidate lessons only, never auto-promoted
"""
