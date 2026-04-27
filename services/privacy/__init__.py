"""PII redaction layer for Asymptote.

Intercepts all MCP tool output before it exits Asymptote to redact
personally identifiable information using Microsoft Presidio (runs
100% locally, no cloud dependency).

All imports are lazy -- the module is safe to import even when Presidio
is not installed. The engine will log a warning and pass data through
unredacted if the dependencies are missing.
"""
