"""Thin wrapper around the Anthropic Claude API with conversation history and tool use."""
from __future__ import annotations

from typing import Any, Callable

from anthropic import Anthropic

from config import settings


class ClaudeClient:
    """Unified Claude client used by the agent.

    Features:
      - system prompt (agent role);
      - conversation history;
      - tool use (function calling) loop.
    """

    def __init__(self, system: str, model: str | None = None, max_tokens: int | None = None):
        self._client = Anthropic(api_key=settings.require_api_key())
        self.system = system
        self.model = model or settings.model
        self.max_tokens = max_tokens or settings.max_tokens
        self.history: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.history.clear()

    def ask(self, user_message: str) -> str:
        """Send a message and get a text reply (history-aware)."""
        self.history.append({"role": "user", "content": user_message})
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            messages=self.history,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        self.history.append({"role": "assistant", "content": text})
        return text

    def ask_with_tools(
        self,
        user_message: str,
        tools: list[dict[str, Any]],
        handlers: dict[str, Callable[..., Any]],
        max_steps: int = 5,
    ) -> str:
        """Conversation turn with tool use (function calling).

        tools     — list of tool JSON schemas in Anthropic format.
        handlers  — mapping {tool_name: callable(**args) -> result}.
        """
        self.history.append({"role": "user", "content": user_message})
        for _ in range(max_steps):
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system,
                tools=tools,
                messages=self.history,
            )
            self.history.append({"role": "assistant", "content": resp.content})

            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                return "".join(b.text for b in resp.content if b.type == "text")

            results = []
            for tu in tool_uses:
                handler = handlers.get(tu.name)
                try:
                    output = handler(**tu.input) if handler else f"No handler for {tu.name}"
                except Exception as exc:  # noqa: BLE001
                    output = f"Tool error: {exc}"
                results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": str(output)}
                )
            self.history.append({"role": "user", "content": results})

        return "Conversation step limit reached."
