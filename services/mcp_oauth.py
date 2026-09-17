"""OAuth 2.1 resource-server surface for /mcp: metadata and the challenge.

The MCP authorization spec (2026-07-28 revision) makes an MCP server an
OAuth 2.1 *resource server*: it must publish RFC 9728 protected-resource
metadata naming its authorization server(s), answer an unauthenticated
request with a ``WWW-Authenticate: Bearer`` challenge that points at that
metadata, and validate the bearer tokens that come back for audience,
issuer, expiry and scope. Everything else - sign-in, consent, client
registration, token issuance - belongs to the authorization server, which
here is always the site's own IdP (services/identity.py explains why the
app never embeds one).

This module owns the two documents the spec puts on the resource server:
the metadata and the challenge header. Token validation itself lives in
``services/identity.py`` (``get_identity_verifier(path)``), so a bearer JWT
on /mcp resolves to exactly the identity a browser session would.

What the metadata does NOT include: a registration endpoint. Client
registration is the IdP's concern. Keycloak, Okta, Auth0 and Entra ID
each handle it differently (dynamic registration, Client ID Metadata
Documents, or an app the admin registers by hand); docs/IDENTITY.md walks
through each, and every client that matters accepts a pre-registered
client id when the IdP offers nothing automatic.
"""

from __future__ import annotations

from typing import Any, Optional

from starlette.requests import Request

from services.identity import MCPOAuthConfig, mcp_oauth_config

WELL_KNOWN_PATH = "/.well-known/oauth-protected-resource"


def resource_url(request: Request, config: Optional[MCPOAuthConfig] = None) -> str:
    """The canonical MCP URL (RFC 8707 resource identifier, no trailing slash).

    From settings when known. Otherwise from the request, honouring the
    proxy's ``X-Forwarded-Proto`` so a TLS-terminating front end advertises
    https - this derived form is only ever *advertised*; identity.py never
    accepts it as a token audience.
    """
    config = config or mcp_oauth_config()
    if config is not None and config.public_url:
        return config.public_url
    forwarded = request.headers.get("x-forwarded-proto", "")
    scheme = (forwarded.split(",")[0].strip() or request.url.scheme or "http").lower()
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}/mcp"


def metadata_url(request: Request, config: Optional[MCPOAuthConfig] = None) -> str:
    """RFC 9728 §3.1: the well-known path inserted between host and resource path."""
    from mcp.server.auth.routes import build_resource_metadata_url
    from pydantic import AnyHttpUrl

    return str(build_resource_metadata_url(AnyHttpUrl(resource_url(request, config))))


def protected_resource_metadata(request: Request) -> Optional[dict[str, Any]]:
    """The RFC 9728 document, or None when no authorization server applies."""
    config = mcp_oauth_config()
    if config is None:
        return None
    from mcp.shared.auth import ProtectedResourceMetadata
    from pydantic import AnyHttpUrl

    metadata = ProtectedResourceMetadata(
        resource=AnyHttpUrl(resource_url(request, config)),
        authorization_servers=[AnyHttpUrl(config.issuer)],
        scopes_supported=[config.scope] if config.scope else None,
        bearer_methods_supported=["header"],
        resource_name="Clio",
    )
    return metadata.model_dump(mode="json", exclude_none=True)


def challenge(
    request: Request,
    error: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """The ``WWW-Authenticate`` value for a refused request on /mcp.

    With an authorization server configured it is the RFC 9728 / MCP
    challenge - ``Bearer resource_metadata="..."``, plus ``scope`` when one
    is required and ``error`` when a token was presented and refused
    (``invalid_token``) or lacked the scope (``insufficient_scope``, sent
    with 403 so the client can step up). Without one it is a plain Bearer
    challenge: bearer-header clients still work, and there is nothing to
    discover.
    """
    config = mcp_oauth_config()
    parts: list[str] = []
    if config is None:
        parts.append('realm="Clio"')
    else:
        parts.append(f'resource_metadata="{metadata_url(request, config)}"')
        if config.scope:
            parts.append(f'scope="{config.scope}"')
    if error:
        parts.append(f'error="{error}"')
    if description:
        parts.append(f'error_description="{description}"')
    return "Bearer " + ", ".join(parts)


def summary(request: Request) -> dict[str, Any]:
    """What the Settings tab shows: whether connector clients can attach,
    and the one URL a person pastes into them."""
    config = mcp_oauth_config()
    if config is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "resource": resource_url(request, config),
        "authorization_server": config.issuer,
        "scope": config.scope,
        "metadata_url": metadata_url(request, config),
    }
