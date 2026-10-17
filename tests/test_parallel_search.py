"""Provider and real MCP SDK transport tests; no API keys or network required.

Run after installing requirements-parallel.txt:
    python -m unittest discover -s tests -v
"""
import asyncio
import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import parallel_search
import tools
from agent import ResearchAgent
from claude_client import ClaudeClient


class ParallelSearchTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.closed = False
        self.cancelled = False
        self.mode = "structured"
        self.payload = {"results": [
            {"title": "Python docs", "url": "https://docs.python.org/3/library/asyncio.html",
             "excerpts": ["Asyncio provides async I/O.", "Use async and await."]},
            {"title": None, "url": "https://example.org/async", "excerpts": ["More details."]},
            {"title": "No source", "url": "", "excerpts": []},
        ]}
        client_type = httpx.AsyncClient
        owner = self

        class ObservedClient(client_type):
            async def aclose(self):
                owner.closed = True
                await super().aclose()

            async def __aexit__(self, *args):
                owner.closed = True
                return await super().__aexit__(*args)

        def client_factory(**kwargs):
            return ObservedClient(transport=httpx.MockTransport(self.respond), **kwargs)

        self.client_patch = patch.object(parallel_search.httpx, "AsyncClient", client_factory)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.provider_patch = patch.object(
            tools, "settings", replace(tools.settings, search_provider="parallel")
        )
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    async def respond(self, request):
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        message = json.loads(request.content)
        method = message["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            if self.mode == "timeout":
                try:
                    await asyncio.sleep(60)
                finally:
                    self.cancelled = True
            if self.mode == "http_error":
                return httpx.Response(503, text="Service unavailable")
            if self.mode == "tool_error":
                result = {"isError": True, "content": [{"type": "text", "text": "Rate limit reached"}]}
            elif self.mode == "malformed":
                result = {"content": [{"type": "text", "text": "not json"}]}
            elif self.mode == "text":
                result = {"content": [{"type": "text", "text": json.dumps(self.payload)}]}
            else:
                result = {"structuredContent": self.payload, "content": []}
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                              headers={"mcp-session-id": "fixture-session"})

    def call(self, max_results=5):
        return json.loads(tools.HANDLERS["search_web"]("Python asyncio documentation", max_results))

    def test_sdk_dispatch_normalizes_and_caps_results(self):
        result = self.call(max_results=1)
        self.assertEqual(result["query"], "Python asyncio documentation")
        self.assertEqual(result["results"], [{
            "title": "Python docs", "url": "https://docs.python.org/3/library/asyncio.html",
            "snippet": "Asyncio provides async I/O.\nUse async and await.",
        }])
        self.assertTrue(self.closed)
        self.assertTrue(any(r.method == "DELETE" for r in self.requests))
        for request in self.requests:
            self.assertEqual(str(request.url), "https://search.parallel.ai/mcp")
            self.assertEqual(request.headers["user-agent"], parallel_search._UA)
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("x-api-key", request.headers)
        messages = [json.loads(r.content) for r in self.requests if r.method == "POST"]
        self.assertEqual(messages[0]["params"]["clientInfo"]["name"], "claude-research-agent")
        call = next(m for m in messages if m["method"] == "tools/call")
        self.assertEqual(call["params"], {
            "name": "web_search", "arguments": {
                "objective": "Python asyncio documentation",
                "search_queries": ["Python asyncio documentation"],
                "session_id": parallel_search._SESSION_ID,
            },
        })
        self.assertTrue(all(r.extensions["timeout"]["read"] == 15 for r in self.requests))

    def test_text_response_and_nullable_title(self):
        self.mode = "text"
        self.assertEqual(self.call()["results"][1], {
            "title": "", "url": "https://example.org/async", "snippet": "More details.",
        })

    def test_empty_results(self):
        self.payload = {"results": []}
        self.assertEqual(self.call()["results"], [])

    def test_session_identity_is_reused(self):
        self.call()
        self.call()
        calls = [json.loads(r.content) for r in self.requests if r.method == "POST"]
        ids = [m["params"]["arguments"]["session_id"] for m in calls if m["method"] == "tools/call"]
        self.assertEqual(ids, [parallel_search._SESSION_ID] * 2)

    def test_tool_error_is_visible_without_fallback(self):
        self.mode = "tool_error"
        with patch.object(tools, "DDGS") as ddgs:
            self.assertIn("Rate limit reached", self.call()["error"])
            ddgs.assert_not_called()

    def test_http_and_malformed_responses_are_errors(self):
        for mode in ("http_error", "malformed"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.assertIn("error", self.call())
                self.assertTrue(self.closed)

    def test_timeout_cancels_request_and_closes_client(self):
        self.mode = "timeout"
        with patch.object(parallel_search, "_TIMEOUT", 0.05):
            result = self.call()
        self.assertIn("timed out", result["error"])
        self.assertTrue(self.cancelled)
        self.assertTrue(self.closed)

    def test_cancellation_propagates_and_closes_client(self):
        self.mode = "timeout"

        async def exercise():
            task = asyncio.create_task(parallel_search._search("Python asyncio documentation"))
            while not any(r.method == "POST" and json.loads(r.content)["method"] == "tools/call"
                          for r in self.requests):
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(exercise())
        self.assertTrue(self.cancelled)
        self.assertTrue(self.closed)

    def test_research_loop_consumes_search_output(self):
        search_call = SimpleNamespace(type="tool_use", name="search_web", id="search-1",
                                      input={"query": "Python asyncio documentation", "max_results": 1})

        def answer(**kwargs):
            if kwargs["messages"][-1]["role"] == "user" and isinstance(kwargs["messages"][-1]["content"], str):
                return SimpleNamespace(content=[search_call])
            output = json.loads(kwargs["messages"][-1]["content"][0]["content"])
            self.assertIn("Asyncio", output["results"][0]["snippet"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=output["results"][0]["url"])])

        ai = ClaudeClient.__new__(ClaudeClient)
        ai._client = SimpleNamespace(messages=SimpleNamespace(create=answer))
        ai.system, ai.model, ai.max_tokens, ai.history = "research", "fixture-model", 1024, []
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.ai = ai
        result = agent.research("Python asyncio documentation", depth="quick")
        self.assertEqual(result["report"], "https://docs.python.org/3/library/asyncio.html")
        self.assertEqual(ai.history, [])


class SelectionTests(unittest.TestCase):
    def test_default_configuration(self):
        env = {k: v for k, v in os.environ.items() if k != "SEARCH_PROVIDER"}
        output = subprocess.check_output([sys.executable, "-c",
                                          "from config import settings; print(settings.search_provider)"], env=env)
        self.assertEqual(output.strip(), b"duckduckgo")

    def test_explicit_duckduckgo_preserves_result_contract(self):
        with patch.object(tools, "settings", replace(tools.settings, search_provider="duckduckgo")), \
             patch.object(tools, "DDGS") as ddgs, \
             patch.object(parallel_search, "search") as parallel:
            ddgs.return_value.text.return_value = [
                {"title": "DDG", "href": "https://example.org", "body": "Snippet"},
                {"title": "Missing URL"},
            ]
            self.assertEqual(json.loads(tools.search_web("test", 2)), {
                "query": "test", "results": [{"title": "DDG", "url": "https://example.org", "snippet": "Snippet"}],
            })
            ddgs.return_value.text.assert_called_once_with("test", max_results=2)
            parallel.assert_not_called()

    def test_unknown_provider_is_reported(self):
        with patch.object(tools, "settings", replace(tools.settings, search_provider="typo")), \
             patch.object(tools, "DDGS") as ddgs:
            self.assertIn("Unknown SEARCH_PROVIDER", json.loads(tools.search_web("test"))["error"])
            ddgs.assert_not_called()

    def test_missing_optional_sdk_has_install_hint(self):
        with patch.object(tools, "settings", replace(tools.settings, search_provider="parallel")), \
             patch.dict(sys.modules, {"parallel_search": None}):
            self.assertIn("requirements-parallel.txt", json.loads(tools.search_web("test"))["error"])


if __name__ == "__main__":
    unittest.main()
