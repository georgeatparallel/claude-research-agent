"""Web research agent — investigates a topic via tool calling and returns a report."""
from __future__ import annotations

from claude_client import ClaudeClient
from tools import TOOLS_SCHEMA, HANDLERS, get_notes

SYSTEM = """You are a professional research agent. Your job is to investigate a topic in depth.

Workflow:
1. Run 2-3 search queries on the topic first
2. Open and read the 2-3 most relevant pages
3. Save key facts and insights via save_note
4. Then write a structured report:
   - Executive summary (3-5 sentences)
   - Key facts (5-7 bullet points)
   - Trends and insights
   - Sources

Write the report in the same language as the topic. Be methodical and thorough."""


class ResearchAgent:
    def __init__(self) -> None:
        self.ai = ClaudeClient(system=SYSTEM, max_tokens=2000)

    def research(self, topic: str, depth: str = "medium") -> dict:
        """
        topic — the topic to investigate
        depth — "quick" | "medium" | "deep" (caps the number of tool-use steps)
        """
        max_steps = {"quick": 3, "medium": 6, "deep": 10}.get(depth, 6)

        prompt = f"Research this topic: {topic}\n\nProduce a detailed analytical report."
        report = self.ai.ask_with_tools(
            prompt,
            tools=TOOLS_SCHEMA,
            handlers=HANDLERS,
            max_steps=max_steps,
        )
        self.ai.reset()

        return {
            "topic": topic,
            "report": report,
            "notes": get_notes(),
        }
