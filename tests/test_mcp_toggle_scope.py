"""ENABLE_MCP gates the external /mcp endpoint only — never in-app chat tools.

The chat tool loop executes the mcp_server tool functions in-process
(services/structured_chat.py lazy-imports them). A per-tool enabled check
used to raise "MCP is disabled in Settings" into every chat tool call when
the /mcp endpoint toggle was off; the HTTP-layer gate (ToggleableMCPApp's
503) is the only place that setting may act.
"""

import inspect

import pytest

import services.mcp_server as mcp


def test_no_per_tool_enabled_gate_exists():
    # The helper itself is gone; only ToggleableMCPApp consults enable_mcp.
    assert not hasattr(mcp, "_ensure_enabled")
    src = inspect.getsource(mcp)
    assert "_ensure_enabled" not in src


def test_toggleable_http_app_still_gates(monkeypatch):
    import asyncio

    monkeypatch.setattr(mcp.settings, "enable_mcp", False)
    sent = []

    async def receive():  # pragma: no cover - never called for a 503
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    app = mcp.ToggleableMCPApp(None)
    asyncio.run(app({"type": "http", "path": "/", "method": "POST"}, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 503


def test_tool_function_runs_with_mcp_disabled(monkeypatch):
    # A cheap registry-backed tool must not raise the old RuntimeError when
    # the endpoint toggle is off. Any error it raises must not be about MCP
    # being disabled.
    monkeypatch.setattr(mcp.settings, "enable_mcp", False)
    try:
        result = mcp.list_collections()
    except RuntimeError as e:
        pytest.fail(f"tool raised with MCP disabled: {e}")
    assert isinstance(result, dict)
