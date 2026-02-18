"""
Custom HTTP transport for MCP servers that use plain JSON-RPC POST.

The standard MCP Python SDK's streamablehttp_client expects SSE (text/event-stream)
responses, but many Go-based MCP servers return plain JSON (application/json).
This module provides a compatible transport layer that bridges the gap.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class DirectMCPClient:
    """A lightweight MCP client that communicates via plain HTTP JSON-RPC POST.

    This is used instead of the SDK's streamablehttp_client when the MCP server
    only supports plain JSON responses (not SSE/streamable HTTP).
    """

    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._request_id = 0
        self._initialized = False

    async def initialize(self):
        """Initialize the connection and perform MCP handshake."""
        self._client = httpx.AsyncClient(timeout=self.timeout, trust_env=False)

        # Send initialize request
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "xhs-python-client",
                "version": "1.0.0"
            }
        })

        if result:
            self.session_id = result.get("_session_id")  # From response headers
            server_info = result.get("serverInfo", {})
            logger.info(
                f"MCP handshake successful: {server_info.get('name', 'unknown')} "
                f"v{server_info.get('version', '?')}"
            )

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})
            self._initialized = True
        else:
            raise RuntimeError("MCP initialize handshake failed")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools from the server."""
        result = await self._send_request("tools/list", {})
        return result.get("tools", []) if result else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the MCP server."""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        return result

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request and return the result."""
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params
        }

        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        try:
            response = await self._client.post(self.url, json=payload, headers=headers)

            # Store session ID from response headers
            if "mcp-session-id" in response.headers:
                self.session_id = response.headers["mcp-session-id"]

            response.raise_for_status()
            data = response.json()

            if "error" in data:
                error = data["error"]
                raise RuntimeError(f"MCP error {error.get('code')}: {error.get('message')}")

            return data.get("result")

        except httpx.HTTPStatusError as e:
            logger.error(f"MCP HTTP error: {e.response.status_code} for {method}")
            raise
        except Exception as e:
            logger.error(f"MCP request failed: {e}")
            raise

    async def _send_notification(self, method: str, params: Dict[str, Any]):
        """Send a JSON-RPC notification (no response expected)."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }

        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        try:
            response = await self._client.post(self.url, json=payload, headers=headers)
            # Notifications may return 202 or 200
            if response.status_code not in (200, 202, 204):
                logger.warning(f"Notification {method} returned: {response.status_code}")
        except Exception as e:
            logger.warning(f"Notification {method} failed: {e}")

    async def cleanup(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._initialized = False
