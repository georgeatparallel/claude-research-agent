"""Real web tools for the research agent: web search + page fetching + notes."""
from __future__ import annotations

import json
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from config import settings

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TIMEOUT = 15
_notes: list[dict] = []


def search_web(query: str, max_results: int = 5) -> str:
    """Search with the configured provider. Returns JSON with a list of results."""
    if settings.search_provider == "parallel":
        try:
            from parallel_search import search

            results = search(query, max_results)
        except ImportError:
            return json.dumps({
                "query": query,
                "error": "Parallel search requires: pip install -r requirements-parallel.txt",
            })
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"query": query, "error": f"search failed: {exc}"})
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)

    if settings.search_provider != "duckduckgo":
        return json.dumps({
            "query": query,
            "error": "Unknown SEARCH_PROVIDER. Choose duckduckgo or parallel.",
        })

    try:
        raw = DDGS().text(query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"query": query, "error": f"search failed: {exc}"})

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        }
        for r in raw
        if r.get("href")
    ]
    return json.dumps({"query": query, "results": results}, ensure_ascii=False)


def fetch_page(url: str, max_chars: int = 5000) -> str:
    """Fetch a web page and return its readable text content."""
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return f"Could not fetch {url}: {exc}"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    if len(text) > max_chars:
        text = text[:max_chars] + " …[truncated]"
    return f"[{url}]\n{text}"


def save_note(text: str, tag: str = "general") -> str:
    """Save a research note."""
    note = {
        "id": len(_notes) + 1,
        "tag": tag,
        "text": text,
        "timestamp": datetime.now().isoformat(),
    }
    _notes.append(note)
    return f"Note #{note['id']} saved (tag: {tag})"


def get_notes() -> list[dict]:
    return _notes.copy()


TOOLS_SCHEMA = [
    {
        "name": "search_web",
        "description": "Search the web for information on a query",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max number of results",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": "Fetch the full readable content of a web page by URL",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Page URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "save_note",
        "description": "Save an important note during research",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Note text"},
                "tag": {"type": "string", "description": "Category tag", "default": "general"},
            },
            "required": ["text"],
        },
    },
]

HANDLERS = {
    "search_web": search_web,
    "fetch_page": fetch_page,
    "save_note": save_note,
}
