"""Embedded MCP server configuration endpoints."""

import logging

from fastapi import HTTPException, status, Request

from services.config_manager import config_manager
from services.mcp_server import (
    MCP_CONFIG_FIELDS,
    get_mcp_settings_payload,
)

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/mcp/config",
    summary="Get MCP configuration",
    tags=["mcp"],
)
async def get_mcp_config():
    """Get the embedded MCP server configuration."""
    return get_mcp_settings_payload()


@router.post(
    "/api/mcp/config",
    summary="Update MCP configuration",
    tags=["mcp"],
)
async def update_mcp_config(updates: dict):
    """Update embedded MCP settings without restarting the app."""
    filtered_updates = {
        key: value
        for key, value in updates.items()
        if key in MCP_CONFIG_FIELDS
    }
    if not filtered_updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MCP configuration fields were provided.",
        )

    return config_manager.update_config(filtered_updates)


# Redirect bare /mcp (no trailing slash) to /mcp/ so MCP clients that use the old
# exported URL still work. Uses 307 to preserve the HTTP method (POST stays POST).
@router.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp_trailing_slash_redirect(request: Request):
    url = str(request.url)
    redirect_url = url.replace("/mcp?", "/mcp/?", 1) if "?" in url else url.rstrip("/") + "/"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(redirect_url, status_code=307)
