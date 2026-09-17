"""Connect to /mcp with the MCP Python SDK's reference OAuth client.

Exercises the client side of the MCP authorization spec as the SDK
implements it: 401 challenge -> protected-resource metadata -> authorization
server metadata -> dynamic client registration -> PKCE authorization-code
flow with `resource` -> bearer token -> initialize, list tools, call one.

    python scripts/oauth_smoke/sdk_client.py [http://localhost:8473/mcp]

The browser step opens your default browser at the IdP. Sign in, and paste
the URL the IdP redirects you to (it starts with http://localhost:8765/
and will not load - that is expected) back into this prompt. With the
Keycloak realm from keycloak_realm.py, sign in as analyst / analyst.
"""
import asyncio
import sys
import urllib.parse
import webbrowser

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

SERVER = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8473/mcp"


class MemoryStorage(TokenStorage):
    def __init__(self):
        self.tokens = None
        self.client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info: OAuthClientInformationFull):
        self.client = info


captured = {}


async def redirect_handler(url: str) -> None:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    print("authorization request -> client_id", q.get("client_id"), "resource", q.get("resource"),
          "pkce", q.get("code_challenge_method"), "scope", q.get("scope"))
    webbrowser.open(url)
    pasted = await asyncio.to_thread(input, "Paste the redirect URL here: ")
    rq = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted.strip()).query)
    captured["code"] = rq["code"][0]
    captured["state"] = rq.get("state", [None])[0]


async def callback_handler():
    return captured["code"], captured["state"]


async def main():
    storage = MemoryStorage()
    provider = OAuthClientProvider(
        server_url=SERVER,
        client_metadata=OAuthClientMetadata(
            client_name="Asymptote OAuth smoke client",
            redirect_uris=["http://localhost:8765/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    async with streamablehttp_client(SERVER, auth=provider) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("initialized:", init.serverInfo.name)
            tools = await session.list_tools()
            print("tools:", len(tools.tools))
            result = await session.call_tool("list_collections", {})
            print("list_collections ->", (result.content[0].text if result.content else result)[:400])
    print("registered client_id:", storage.client.client_id if storage.client else "(pre-registered)")
    print("token type:", storage.tokens.token_type if storage.tokens else None)


if __name__ == "__main__":
    asyncio.run(main())
