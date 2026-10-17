"""Optional anonymous Parallel Search MCP backend for the synchronous CLI."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Implementation

_URL = "https://search.parallel.ai/mcp"
_UA = "claude-research-agent/0.1 (+https://github.com/artesuavee/claude-research-agent)"
_TIMEOUT = 15
# Reuse one identifier across searches in this process, including separate MCP connections.
_SESSION_ID = uuid4().hex


async def _search(query: str) -> CallToolResult:
    async with httpx.AsyncClient(headers={"User-Agent": _UA}, timeout=_TIMEOUT) as client:
        async with streamable_http_client(_URL, http_client=client) as (read, write, _):
            async with ClientSession(
                read, write,
                read_timeout_seconds=timedelta(seconds=_TIMEOUT),
                client_info=Implementation(name="claude-research-agent", version="0.1"),
            ) as session:
                await session.initialize()
                return await session.call_tool("web_search", {
                    "objective": query,
                    "search_queries": [query],
                    "session_id": _SESSION_ID,
                })


def search(query: str, max_results: int = 5) -> list[dict]:
    """Return the existing title/url/snippet shape; propagate errors to search_web."""
    if max_results < 1:
        raise ValueError("max_results must be positive")
    try:
        result = asyncio.run(asyncio.wait_for(_search(query), timeout=_TIMEOUT))
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Parallel search timed out after {_TIMEOUT} seconds") from exc
    text = "\n".join(b.text for b in result.content if b.type == "text")
    if result.isError:
        raise RuntimeError(f"Parallel search: {text}")
    payload = result.structuredContent or json.loads(text)
    return [
        {
            "title": r.get("title") or "",
            "url": r["url"],
            "snippet": "\n".join(r["excerpts"]),
        }
        for r in payload["results"] if r.get("url")
    ][:max_results]
