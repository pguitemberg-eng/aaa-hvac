"""
mcp/mcp_client.py
Reusable wrapper for all MCP server calls via Anthropic API.
"""

import anthropic
from typing import Optional
from config import get_anthropic_api_key

MCP_SERVERS = {
    "hubspot": "https://mcp.hubspot.com/mcp",
    "gcal": "https://gcal.mcp.claude.com/mcp",
    "gmail": "https://gmail.mcp.claude.com/mcp",
}


def call_mcp(
    prompt: str,
    server_key: str,
    system: str = "You are an HVAC business assistant. Complete the task and return a brief confirmation.",
    max_tokens: int = 1024,
) -> Optional[str]:
    server_url = MCP_SERVERS.get(server_key)
    if not server_url:
        print(f"[MCP] Unknown server key: {server_key}")
        return None

    try:
        client = anthropic.Anthropic(api_key=get_anthropic_api_key())

        response = client.beta.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            mcp_servers=[
                {
                    "type": "url",
                    "url": server_url,
                    "name": server_key,
                }
            ],
            betas=["mcp-client-2025-04-04"],
        )

        text_parts = [
            block.text
            for block in response.content
            if hasattr(block, "text") and block.text
        ]
        result = "\n".join(text_parts).strip()
        print(f"[MCP:{server_key}] Done - {result[:80]}")
        return result

    except Exception as e:
        print(f"[MCP:{server_key}] Error: {e}")
        return None


def call_mcp_multi(
    prompt: str,
    server_keys: list[str],
    system: str = "You are an HVAC business assistant. Complete all tasks and return a brief confirmation.",
    max_tokens: int = 1024,
) -> Optional[str]:
    servers = [
        {"type": "url", "url": MCP_SERVERS[k], "name": k}
        for k in server_keys
        if k in MCP_SERVERS
    ]

    if not servers:
        print(f"[MCP] No valid servers found for: {server_keys}")
        return None

    try:
        client = anthropic.Anthropic(api_key=get_anthropic_api_key())

        response = client.beta.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            mcp_servers=servers,
            betas=["mcp-client-2025-04-04"],
        )

        text_parts = [
            block.text
            for block in response.content
            if hasattr(block, "text") and block.text
        ]
        result = "\n".join(text_parts).strip()
        print(f"[MCP:{'+'.join(server_keys)}] Done - {result[:80]}")
        return result

    except Exception as e:
        print(f"[MCP:{'+'.join(server_keys)}] Error: {e}")
        return None