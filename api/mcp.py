"""Embedded MCP server configuration endpoints."""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, status, Request

from middleware.user_context import get_current_user_id
from services.config_manager import config_manager
from services import mcp_tokens
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


# ── Personal MCP access tokens ──────────────────────────────────────────────
# Self-serve alternative to a Cloudflare Access service token: anyone who can
# already reach the app mints their own bearer credential for headless MCP
# clients here, instead of provisioning Access + hand-editing .env/.mcp.json.
# These endpoints sit behind the normal app auth (password / SSO), same as
# /api/shares; the token they hand out is separately scoped to /mcp only by
# the auth middleware in main.py.

@router.post(
    "/api/mcp/tokens",
    summary="Create a personal MCP access token",
    tags=["mcp"],
)
async def create_mcp_token(body: dict, user_id: Optional[str] = Depends(get_current_user_id)):
    """Mint a token. The plaintext is returned once and cannot be recovered."""
    name = (body or {}).get("name", "")
    return mcp_tokens.generate_token(user_id, name)


@router.get(
    "/api/mcp/tokens",
    summary="List my personal MCP access tokens",
    tags=["mcp"],
)
async def list_mcp_tokens(user_id: Optional[str] = Depends(get_current_user_id)):
    return {"tokens": mcp_tokens.list_tokens(user_id)}


@router.delete(
    "/api/mcp/tokens/{token_id}",
    summary="Revoke a personal MCP access token",
    tags=["mcp"],
)
async def revoke_mcp_token(token_id: str, user_id: Optional[str] = Depends(get_current_user_id)):
    if not mcp_tokens.revoke_token(token_id, user_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return {"revoked": True}


# Redirect bare /mcp (no trailing slash) to /mcp/ so MCP clients that use the old
# exported URL still work. Uses 307 to preserve the HTTP method (POST stays POST).
@router.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp_trailing_slash_redirect(request: Request):
    url = str(request.url)
    redirect_url = url.replace("/mcp?", "/mcp/?", 1) if "?" in url else url.rstrip("/") + "/"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(redirect_url, status_code=307)
